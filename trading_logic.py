"""
Core trading calculations.

Fee model (with maxed skills):
  Station trading:
    buy order placed  → broker fee on (qty × price)
    sell order placed → broker fee on (qty × price)  + sales tax on proceeds
  Regional arbitrage (instant-buy Jita, place sell order at hub):
    Jita buy          → market-take, no broker fee, no tax
    Destination sell  → broker fee + sales tax on proceeds
    Hauling           → flat ISK/jump hauling cost
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from constants import (
    BLACKLISTED_GROUPS,
    DEFAULT_BROKER_FEE,
    DEFAULT_HAUL_PER_JUMP,
    DEFAULT_SALES_TAX,
)


# ── Order book ───────────────────────────────────────────────────────────────

@dataclass
class BookEntry:
    best_buy: float    # highest buy-order price
    best_sell: float   # lowest sell-order price
    buy_volume: int
    sell_volume: int
    buy_orders: int
    sell_orders: int


def build_order_book(raw_orders: List[dict]) -> Dict[int, BookEntry]:
    """Convert flat ESI order list → {type_id: BookEntry}."""
    buys: Dict[int, list] = defaultdict(list)
    sells: Dict[int, list] = defaultdict(list)

    for o in raw_orders:
        tid = o["type_id"]
        if o["is_buy_order"]:
            buys[tid].append(o)
        else:
            sells[tid].append(o)

    book: Dict[int, BookEntry] = {}
    for tid in set(buys) | set(sells):
        b_list = buys.get(tid, [])
        s_list = sells.get(tid, [])
        if not b_list or not s_list:
            continue

        best_buy  = max(o["price"] for o in b_list)
        best_sell = min(o["price"] for o in s_list)

        if best_sell <= best_buy:   # inverted / crossed market — skip
            continue

        book[tid] = BookEntry(
            best_buy   = best_buy,
            best_sell  = best_sell,
            buy_volume = sum(o["volume_remain"] for o in b_list),
            sell_volume= sum(o["volume_remain"] for o in s_list),
            buy_orders = len(b_list),
            sell_orders= len(s_list),
        )

    return book


# ── Trade opportunity ─────────────────────────────────────────────────────────

@dataclass
class TradeOpportunity:
    type_id:            int
    type_name:          str
    trade_type:         str        # "station" | "regional"
    buy_price:          float
    sell_price:         float
    margin_pct:         float      # net margin after all fees
    profit_per_unit:    float
    total_profit:       float
    trade_volume:       int        # units this calc is based on
    buy_volume:         int
    sell_volume:        int
    destination:        str = "Jita"
    jumps:              int = 0
    fees:               dict = field(default_factory=dict)


# ── Station trading ───────────────────────────────────────────────────────────

def find_station_trades(
    book: Dict[int, BookEntry],
    type_names: Dict[int, str],
    *,
    min_margin_pct: float = 5.0,
    min_volume: int = 300,
    min_price: float = 50_000,
    max_price: float = 500_000_000,
    max_spread_ratio: float = 1.40,   # anti-scam: sell must be ≤ 40 % above buy
    broker_fee: float = DEFAULT_BROKER_FEE,
    sales_tax: float = DEFAULT_SALES_TAX,
    top_n: int = 50,
) -> List[TradeOpportunity]:
    """
    Identify station flipping opportunities (same station, buy order + sell order).

    Cost model per unit:
        buy cost  = best_buy  × (1 + broker_fee)
        sell rev  = best_sell × (1 − broker_fee − sales_tax)
        profit    = sell_rev  − buy_cost
    """
    results: List[TradeOpportunity] = []

    for tid, entry in book.items():
        buy  = entry.best_buy
        sell = entry.best_sell

        if buy < min_price or sell > max_price:
            continue
        if sell / buy > max_spread_ratio:
            continue
        if entry.buy_volume < min_volume or entry.sell_volume < min_volume:
            continue

        cost_per_unit = buy  * (1.0 + broker_fee)
        rev_per_unit  = sell * (1.0 - broker_fee - sales_tax)
        profit        = rev_per_unit - cost_per_unit
        margin        = (profit / cost_per_unit) * 100.0

        if margin < min_margin_pct:
            continue

        vol          = min(entry.buy_volume, entry.sell_volume)
        total_profit = profit * vol

        results.append(TradeOpportunity(
            type_id         = tid,
            type_name       = type_names.get(tid, f"#{tid}"),
            trade_type      = "station",
            buy_price       = buy,
            sell_price      = sell,
            margin_pct      = round(margin, 2),
            profit_per_unit = round(profit),
            total_profit    = round(total_profit),
            trade_volume    = vol,
            buy_volume      = entry.buy_volume,
            sell_volume     = entry.sell_volume,
            fees={
                "broker_buy":  round(buy  * broker_fee),
                "broker_sell": round(sell * broker_fee),
                "sales_tax":   round(sell * sales_tax),
            },
        ))

    results.sort(key=lambda t: t.margin_pct, reverse=True)
    return results[:top_n]


# ── Regional arbitrage ────────────────────────────────────────────────────────

def find_regional_arbitrage(
    source_book: Dict[int, BookEntry],   # Jita
    dest_book:   Dict[int, BookEntry],   # e.g. Rens
    type_names:  Dict[int, str],
    *,
    jumps: int,
    destination_name: str,
    min_margin_pct: float = 8.0,
    min_volume: int = 50,
    min_price: float = 100_000,
    max_price: float = 2_000_000_000,
    broker_fee: float = DEFAULT_BROKER_FEE,
    sales_tax:  float = DEFAULT_SALES_TAX,
    haul_per_jump: float = DEFAULT_HAUL_PER_JUMP,
    top_n: int = 40,
) -> List[TradeOpportunity]:
    """
    Buy from Jita sell orders, haul, place sell orders at destination hub.

    Cost model per unit:
        buy cost  = jita_best_sell              (market-take, no broker fee)
        haul cost = jumps × haul_per_jump / vol (amortised)
        sell rev  = dest_best_buy × (1 − broker_fee − sales_tax)
        profit    = sell_rev − buy_cost − haul_cost_per_unit
    """
    results: List[TradeOpportunity] = []

    for tid, src in source_book.items():
        if tid not in dest_book:
            continue
        dst = dest_book[tid]

        jita_price = src.best_sell
        dest_price = dst.best_buy

        if jita_price < min_price or jita_price > max_price:
            continue
        if dest_price <= jita_price:
            continue

        vol = min(src.sell_volume, dst.buy_volume)
        if vol < min_volume:
            continue

        haul_per_unit = (jumps * haul_per_jump) / max(vol, 1)
        rev_per_unit  = dest_price * (1.0 - broker_fee - sales_tax)
        profit        = rev_per_unit - jita_price - haul_per_unit
        margin        = (profit / jita_price) * 100.0

        if margin < min_margin_pct or profit <= 0:
            continue

        results.append(TradeOpportunity(
            type_id         = tid,
            type_name       = type_names.get(tid, f"#{tid}"),
            trade_type      = "regional",
            buy_price       = jita_price,
            sell_price      = dest_price,
            margin_pct      = round(margin, 2),
            profit_per_unit = round(profit),
            total_profit    = round(profit * vol),
            trade_volume    = vol,
            buy_volume      = src.sell_volume,
            sell_volume     = dst.buy_volume,
            destination     = destination_name,
            jumps           = jumps,
            fees={
                "haul_total":  round(jumps * haul_per_jump),
                "broker_sell": round(dest_price * broker_fee),
                "sales_tax":   round(dest_price * sales_tax),
            },
        ))

    results.sort(key=lambda t: t.total_profit, reverse=True)
    return results[:top_n]


# ── Helpers ───────────────────────────────────────────────────────────────────

def top_opportunities_combined(
    station: List[TradeOpportunity],
    regional: List[TradeOpportunity],
    n: int = 10,
) -> Tuple[List[TradeOpportunity], List[TradeOpportunity]]:
    return station[:n], regional[:n]
