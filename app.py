"""
EVE Online Market Dashboard  —  v2.0
Run: streamlit run app.py
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from constants import (
    COLOUR, DEFAULT_BROKER_FEE, DEFAULT_HAUL_PER_JUMP, DEFAULT_SALES_TAX,
    HUB_SYSTEMS, HUB_TO_REGION,
)
from eve_api import get_region_orders, resolve_names, get_route
from route_optimizer import RouteResult, compare_routes, recommend
from trading_logic import (
    TradeOpportunity, build_order_book, find_regional_arbitrage,
    find_station_trades,
)


# ── Page config (must be first Streamlit call) ────────────────────────────────

st.set_page_config(
    page_title = "EVE Market Dashboard",
    page_icon  = "🚀",
    layout     = "wide",
    initial_sidebar_state = "expanded",
)


# ── Global CSS (EVE dark-space theme) ────────────────────────────────────────

st.markdown(f"""
<style>
/* ── Base ── */
html, body, [data-testid="stAppViewContainer"] {{
    background-color: {COLOUR['bg']};
    color: {COLOUR['text']};
    font-family: 'Segoe UI', 'Helvetica Neue', sans-serif;
}}
[data-testid="stSidebar"] {{
    background-color: #080D12;
    border-right: 1px solid {COLOUR['border']};
}}
[data-testid="stHeader"] {{ background: transparent; }}

/* ── Metric cards ── */
[data-testid="metric-container"] {{
    background: linear-gradient(135deg, {COLOUR['card']} 0%, #16232F 100%);
    border: 1px solid {COLOUR['border']};
    border-radius: 10px;
    padding: 12px 16px;
    box-shadow: 0 0 18px rgba(0,180,216,0.08);
}}
[data-testid="stMetricValue"] {{ color: {COLOUR['gold']} !important; font-size: 1.5rem; }}
[data-testid="stMetricLabel"] {{ color: {COLOUR['subtext']} !important; }}
[data-testid="stMetricDelta"]  {{ color: {COLOUR['green']} !important; }}

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {{
    background: #080D12;
    border-bottom: 2px solid {COLOUR['border']};
}}
.stTabs [data-baseweb="tab"] {{ color: {COLOUR['subtext']}; padding: 10px 24px; }}
.stTabs [aria-selected="true"] {{
    color: {COLOUR['accent']} !important;
    border-bottom: 3px solid {COLOUR['accent']} !important;
    background: transparent !important;
}}

/* ── Buttons ── */
.stButton > button {{
    background: linear-gradient(90deg, #0B3D5E, #0A2A42);
    border: 1px solid {COLOUR['accent']};
    color: {COLOUR['accent']};
    border-radius: 6px;
    font-weight: 600;
    letter-spacing: 0.05em;
    transition: all 0.2s;
}}
.stButton > button:hover {{
    background: {COLOUR['accent']};
    color: #000;
    box-shadow: 0 0 12px {COLOUR['accent']};
}}

/* ── Dataframe ── */
[data-testid="stDataFrame"] {{ border: 1px solid {COLOUR['border']}; border-radius: 8px; }}

/* ── Utility classes ── */
.profit  {{ color: {COLOUR['green']}; font-weight: bold; }}
.loss    {{ color: {COLOUR['red']};   font-weight: bold; }}
.isk     {{ color: {COLOUR['gold']};  font-family: monospace; }}
.risk-low    {{ color: {COLOUR['green']};  }}
.risk-mid    {{ color: {COLOUR['orange']}; }}
.risk-high   {{ color: {COLOUR['red']};    }}
.section-title {{
    font-size: 1.1rem; font-weight: 700; letter-spacing: 0.08em;
    color: {COLOUR['accent']}; margin-bottom: 4px;
    text-transform: uppercase;
}}
.divider {{ border-top: 1px solid {COLOUR['border']}; margin: 12px 0; }}
</style>
""", unsafe_allow_html=True)


# ── ISK formatting helpers ────────────────────────────────────────────────────

def isk(v: float, decimals: int = 2) -> str:
    if abs(v) >= 1e12: return f"{v/1e12:.{decimals}f}T"
    if abs(v) >= 1e9:  return f"{v/1e9:.{decimals}f}B"
    if abs(v) >= 1e6:  return f"{v/1e6:.{decimals}f}M"
    if abs(v) >= 1e3:  return f"{v/1e3:.0f}K"
    return f"{v:.0f}"

def isk_label(v: float) -> str:
    return f"{isk(v)} ISK"

def pct(v: float) -> str:
    return f"{v:+.1f}%" if v != 0 else "0.0%"

def vol_fmt(v: int) -> str:
    if v >= 1_000_000: return f"{v/1_000_000:.1f}M"
    if v >= 1_000:     return f"{v/1_000:.0f}K"
    return str(v)


# ── Data fetching (cached by Streamlit, actual fetch is synchronous) ──────────

@st.cache_data(ttl=300, show_spinner=False)
def fetch_region_data(region_id: int, max_pages: int) -> List[dict]:
    return get_region_orders(region_id, ttl=300, max_pages=max_pages)


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_names(type_ids_tuple: tuple) -> Dict[int, str]:
    return resolve_names(list(type_ids_tuple))


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_routes(origin: str, destination: str) -> dict:
    return compare_routes(origin, destination)


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_jump_count(origin: str, destination: str) -> int:
    """Return number of jumps on the shortest route between two hubs."""
    from constants import HUB_SYSTEMS
    from eve_api import get_route
    o_id = HUB_SYSTEMS.get(origin)
    d_id = HUB_SYSTEMS.get(destination)
    if not o_id or not d_id:
        return 99
    path = get_route(o_id, d_id, flag="shortest")
    return len(path) - 1 if path else 99


# ── Sidebar configuration ─────────────────────────────────────────────────────

def sidebar_config() -> dict:
    st.sidebar.markdown(
        f"<div style='text-align:center;color:{COLOUR['accent']};font-size:1.3rem;"
        f"font-weight:800;letter-spacing:0.12em;padding:8px 0'>🚀 EVE MARKET</div>",
        unsafe_allow_html=True,
    )
    st.sidebar.markdown(f"<div style='text-align:center;color:{COLOUR['subtext']};font-size:0.75rem;margin-bottom:16px'>TRADING DASHBOARD v2.0</div>", unsafe_allow_html=True)
    st.sidebar.divider()

    st.sidebar.markdown("**Data Source**")
    max_pages = st.sidebar.select_slider(
        "Order book depth",
        options=[20, 40, 60, 80, 120],
        value=60,
        help="Pages × 1 000 orders per page. More = slower but more opportunities.",
    )
    st.sidebar.caption(f"≈ {max_pages * 1000:,} orders per region")

    st.sidebar.divider()
    st.sidebar.markdown("**Fee Settings** (skill-based)")
    broker_fee = st.sidebar.slider("Broker Fee %",  1.0, 5.0, DEFAULT_BROKER_FEE * 100, 0.1) / 100
    sales_tax  = st.sidebar.slider("Sales Tax %",   1.0, 8.0, DEFAULT_SALES_TAX  * 100, 0.1) / 100
    haul_cost  = st.sidebar.number_input("Hauling ISK/jump", 500_000, 10_000_000,
                                         int(DEFAULT_HAUL_PER_JUMP), step=250_000)

    st.sidebar.divider()
    st.sidebar.markdown("**Station Trade Filters**")
    st_min_margin = st.sidebar.slider("Min margin %", 2.0, 30.0, 5.0, 0.5)
    st_min_vol    = st.sidebar.number_input("Min 24h volume", 50, 50_000, 300, step=50)
    st_min_price  = st.sidebar.number_input("Min price (ISK)", 10_000, 10_000_000, 50_000, step=10_000)
    st_max_price  = st.sidebar.number_input("Max price (ISK)", 1_000_000, 2_000_000_000,
                                            500_000_000, step=1_000_000)

    st.sidebar.divider()
    st.sidebar.markdown("**Regional Arbitrage**")

    all_hubs = list(HUB_SYSTEMS.keys())

    source_hub = st.sidebar.selectbox(
        "Source hub (buy from)",
        all_hubs,
        index=0,
        help="Hub where you buy items. Jita has the most liquidity.",
    )

    dest_options = [h for h in all_hubs if h != source_hub]
    dest_hubs = st.sidebar.multiselect(
        "Destination hubs (sell to)",
        dest_options,
        default=dest_options,
        help="Select one or more hubs to scan for arbitrage opportunities.",
    )

    arb_max_jumps  = st.sidebar.slider(
        "Max jumps to destination", 1, 40, 20,
        help="Hide routes longer than this. Actual jump count fetched from ESI.",
    )
    arb_min_margin = st.sidebar.slider("Min margin %", 3.0, 30.0, 8.0, 0.5, key="arb_margin")
    arb_min_vol    = st.sidebar.number_input("Min volume", 10, 10_000, 50, step=10, key="arb_vol")

    return dict(
        max_pages      = max_pages,
        broker_fee     = broker_fee,
        sales_tax      = sales_tax,
        haul_cost      = haul_cost,
        st_min_margin  = st_min_margin,
        st_min_vol     = st_min_vol,
        st_min_price   = st_min_price,
        st_max_price   = st_max_price,
        source_hub     = source_hub,
        dest_hubs      = dest_hubs,
        arb_max_jumps  = arb_max_jumps,
        arb_min_margin = arb_min_margin,
        arb_min_vol    = arb_min_vol,
    )


# ── Header ────────────────────────────────────────────────────────────────────

def render_header(cfg: dict) -> bool:
    col_title, col_refresh, col_status = st.columns([4, 1, 1])
    with col_title:
        st.markdown(
            f"<h2 style='color:{COLOUR['accent']};margin:0;letter-spacing:0.06em'>"
            f"⚡ EVE ONLINE — MARKET INTELLIGENCE</h2>",
            unsafe_allow_html=True,
        )
        st.caption("Real-time market opportunities • Tax-adjusted margins • Route risk scoring")

    refresh = False
    with col_refresh:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🔄 Refresh", use_container_width=True):
            st.cache_data.clear()
            refresh = True

    with col_status:
        last = st.session_state.get("last_fetch_ts")
        if last:
            age = int(time.time() - last)
            colour = COLOUR['green'] if age < 180 else COLOUR['orange'] if age < 300 else COLOUR['red']
            st.markdown(
                f"<div style='text-align:center;margin-top:24px'>"
                f"<span style='color:{colour};font-size:0.8rem'>⬤ {age}s ago</span></div>",
                unsafe_allow_html=True,
            )

    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)
    return refresh


# ── Tab 1: Station Trading ───────────────────────────────────────────────────

def render_overview_tab(
    raw_orders: List[dict],
    book: dict,
    names: Dict[int, str],
    all_arb: Dict[str, List[TradeOpportunity]],
    cfg: dict,
) -> None:
    st.markdown("<div class='section-title'>📊 Live Market Overview — Data Verification</div>", unsafe_allow_html=True)
    st.caption("Confirm raw ESI data is flowing before trusting the trading tabs.")

    # ── Health metrics ────────────────────────────────────────────────────────
    buy_orders  = [o for o in raw_orders if o["is_buy_order"]]
    sell_orders = [o for o in raw_orders if not o["is_buy_order"]]
    unique_items = len({o["type_id"] for o in raw_orders})
    paired_items = len(book)   # items that have BOTH a buy and sell side

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Orders (Jita)",  f"{len(raw_orders):,}")
    c2.metric("Buy Orders",           f"{len(buy_orders):,}")
    c3.metric("Sell Orders",          f"{len(sell_orders):,}")
    c4.metric("Unique Items",         f"{unique_items:,}")
    c5.metric("Tradeable Pairs",      f"{paired_items:,}",
              help="Items with both a buy and sell order — candidates for station trading")

    if len(raw_orders) == 0:
        st.error("❌ No orders fetched. Check your internet connection or ESI status.")
        st.stop()
    else:
        st.success(f"✅ ESI is working. {len(raw_orders):,} Jita orders loaded successfully.")

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Top items by sell volume ──────────────────────────────────────────────
    st.markdown("#### 🔥 Top 20 Items by Total Sell Volume (Jita)")

    from collections import defaultdict

    # Build best buy/sell directly from raw orders — reliable regardless of page order
    best_sell_price: dict = {}
    best_buy_price:  dict = {}
    sell_vol_by_item: dict = defaultdict(int)
    buy_vol_by_item:  dict = defaultdict(int)

    for o in raw_orders:
        tid   = o["type_id"]
        price = o["price"]
        vol   = o["volume_remain"]
        if o["is_buy_order"]:
            buy_vol_by_item[tid] += vol
            if tid not in best_buy_price or price > best_buy_price[tid]:
                best_buy_price[tid] = price
        else:
            sell_vol_by_item[tid] += vol
            if tid not in best_sell_price or price < best_sell_price[tid]:
                best_sell_price[tid] = price

    top_vol = sorted(sell_vol_by_item.items(), key=lambda x: x[1], reverse=True)[:20]

    # Resolve names for top items — may include sell-only items missing from names dict
    top_ids   = tuple(tid for tid, _ in top_vol)
    top_names = fetch_names(top_ids)
    names     = {**names, **top_names}

    vol_rows = []
    for tid, vol in top_vol:
        sell_p = best_sell_price.get(tid, 0)
        buy_p  = best_buy_price.get(tid, 0)
        spread = round((sell_p - buy_p) / buy_p * 100, 1) if buy_p > 0 else 0
        vol_rows.append({
            "Item":        names.get(tid, f"#{tid}"),
            "Sell Volume": vol,
            "Buy Volume":  buy_vol_by_item.get(tid, 0),
            "Best Sell":   sell_p,
            "Best Buy":    buy_p,
            "Spread %":    spread,
        })

    df_vol = pd.DataFrame(vol_rows)
    st.dataframe(
        df_vol, use_container_width=True, hide_index=True,
        column_config={
            "Item":        st.column_config.TextColumn(width="large"),
            "Sell Volume": st.column_config.NumberColumn(format="%,d"),
            "Buy Volume":  st.column_config.NumberColumn(format="%,d"),
            "Best Sell":   st.column_config.NumberColumn(format="%,.2f ISK"),
            "Best Buy":    st.column_config.NumberColumn(format="%,.2f ISK"),
            "Spread %":    st.column_config.ProgressColumn(
                               format="%.1f%%", min_value=0, max_value=50),
        },
    )

    # ── Spread distribution ───────────────────────────────────────────────────
    st.markdown("#### 📈 Spread Distribution Across All Paired Items")
    spreads = []
    for tid, entry in book.items():
        spread_pct = (entry.best_sell - entry.best_buy) / entry.best_buy * 100
        if 0 < spread_pct < 100:
            spreads.append({"Spread %": round(spread_pct, 1), "Item": names.get(tid, f"#{tid}")})

    if spreads:
        df_spread = pd.DataFrame(spreads)
        col_a, col_b = st.columns(2)
        with col_a:
            fig = px.histogram(
                df_spread, x="Spread %", nbins=50,
                title=f"Spread Distribution ({len(spreads):,} items with buy+sell)",
                color_discrete_sequence=[COLOUR["accent"]],
            )
            fig.add_vline(x=5,  line_dash="dash", line_color=COLOUR["green"],
                          annotation_text="5% filter", annotation_font_color=COLOUR["green"])
            fig.update_layout(
                paper_bgcolor=COLOUR["card"], plot_bgcolor=COLOUR["card"],
                font_color=COLOUR["text"], margin=dict(t=40),
            )
            st.plotly_chart(fig, use_container_width=True)
        with col_b:
            above_5  = sum(1 for s in spreads if s["Spread %"] >= 5)
            above_10 = sum(1 for s in spreads if s["Spread %"] >= 10)
            above_20 = sum(1 for s in spreads if s["Spread %"] >= 20)
            st.markdown("<br><br>", unsafe_allow_html=True)
            st.metric("Items with spread ≥ 5%",  f"{above_5:,}",
                      help="Potential station trade candidates before fee/volume filters")
            st.metric("Items with spread ≥ 10%", f"{above_10:,}")
            st.metric("Items with spread ≥ 20%", f"{above_20:,}")
            st.caption(f"Median spread: {sorted(s['Spread %'] for s in spreads)[len(spreads)//2]:.1f}%")

    # ── Raw order sample ──────────────────────────────────────────────────────
    st.markdown("#### 🔬 Raw Order Sample (first 50 orders from ESI)")
    sample = raw_orders[:50]
    sample_ids   = tuple({o["type_id"] for o in sample})
    sample_names = fetch_names(sample_ids)
    names = {**names, **sample_names}
    sample_rows = [{
        "type_id":      o["type_id"],
        "item":         names.get(o["type_id"], f"#{o['type_id']}"),
        "is_buy_order": o["is_buy_order"],
        "price":        o["price"],
        "volume_remain":o["volume_remain"],
        "location_id":  o.get("location_id", ""),
    } for o in sample]
    st.dataframe(pd.DataFrame(sample_rows), use_container_width=True, hide_index=True,
                 column_config={
                     "price":         st.column_config.NumberColumn(format="%,.2f"),
                     "volume_remain": st.column_config.NumberColumn(format="%,d"),
                 })

    # ── Arbitrage pipeline status ─────────────────────────────────────────────
    st.markdown("#### 🚚 Arbitrage Pipeline Status")
    for hub, trades in all_arb.items():
        status = f"✅ {len(trades)} opportunities found" if trades else "⚠️ 0 opportunities (filters may be too strict)"
        colour = COLOUR["green"] if trades else COLOUR["orange"]
        card_bg  = COLOUR["card"]
        card_bdr = COLOUR["border"]
        st.markdown(
            f"<div style='background:{card_bg};border:1px solid {card_bdr};"
            f"border-radius:6px;padding:10px 16px;margin-bottom:6px'>"
            f"<b>Jita → {hub}</b> &nbsp;&nbsp;"
            f"<span style='color:{colour}'>{status}</span></div>",
            unsafe_allow_html=True,
        )

    # ── Filter debug ──────────────────────────────────────────────────────────
    with st.expander("🔧 Active Filter Settings (debug)"):
        st.json({
            "broker_fee_%":    round(cfg["broker_fee"] * 100, 2),
            "sales_tax_%":     round(cfg["sales_tax"]  * 100, 2),
            "station_min_margin_%": cfg["st_min_margin"],
            "station_min_volume":   cfg["st_min_vol"],
            "station_min_price":    cfg["st_min_price"],
            "station_max_price":    cfg["st_max_price"],
            "arb_min_margin_%":     cfg["arb_min_margin"],
            "arb_min_volume":       cfg["arb_min_vol"],
            "haul_isk_per_jump":    cfg["haul_cost"],
            "max_pages_jita":       cfg["max_pages"],
        })


def render_station_tab(trades: List[TradeOpportunity]) -> None:
    st.markdown("<div class='section-title'>🏪 Station Trading — Jita IV-4 Tranquility</div>", unsafe_allow_html=True)
    st.caption("Place a buy order below market, place a sell order above — profit from the spread. No hauling required.")

    if not trades:
        st.info("No station trading opportunities found with current filters. Try lowering minimum margin or volume.")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Opportunities", len(trades))
    c2.metric("Best Margin", f"{trades[0].margin_pct:.1f}%")
    c3.metric("Avg Margin", f"{sum(t.margin_pct for t in trades)/len(trades):.1f}%")
    c4.metric("Top Total Profit", isk_label(trades[0].total_profit))

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Table ──
    rows = []
    for t in trades:
        rows.append({
            "Item":            t.type_name,
            "Buy Order (ISK)": t.buy_price,
            "Sell Order (ISK)":t.sell_price,
            "Margin %":        t.margin_pct,
            "Profit/unit":     t.profit_per_unit,
            "Trade Vol":       t.trade_volume,
            "Total Profit":    t.total_profit,
            "Buy Depth":       t.buy_volume,
            "Sell Depth":      t.sell_volume,
        })
    df = pd.DataFrame(rows)

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Item":             st.column_config.TextColumn(width="large"),
            "Buy Order (ISK)":  st.column_config.NumberColumn(format="%,.0f"),
            "Sell Order (ISK)": st.column_config.NumberColumn(format="%,.0f"),
            "Margin %":         st.column_config.ProgressColumn(
                                    format="%.1f%%", min_value=0, max_value=40),
            "Profit/unit":      st.column_config.NumberColumn(format="%,.0f ISK"),
            "Trade Vol":        st.column_config.NumberColumn(format="%,d"),
            "Total Profit":     st.column_config.NumberColumn(format="%,.0f ISK"),
            "Buy Depth":        st.column_config.NumberColumn(format="%,d"),
            "Sell Depth":       st.column_config.NumberColumn(format="%,d"),
        },
    )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Charts ──
    col_a, col_b = st.columns(2)
    with col_a:
        fig = px.bar(
            df.head(15), x="Item", y="Margin %",
            title="Top 15 — Margin %",
            color="Margin %",
            color_continuous_scale=["#003300", "#39FF14"],
        )
        fig.update_layout(
            paper_bgcolor=COLOUR["card"], plot_bgcolor=COLOUR["card"],
            font_color=COLOUR["text"], xaxis_tickangle=-45,
            coloraxis_showscale=False, margin=dict(t=40,b=100),
        )
        st.plotly_chart(fig, use_container_width=True)

    with col_b:
        fig2 = px.scatter(
            df.head(20),
            x="Trade Vol", y="Margin %",
            size="Total Profit", color="Margin %",
            hover_name="Item",
            title="Volume vs Margin (bubble = total profit)",
            color_continuous_scale=["#0A3300", "#39FF14"],
        )
        fig2.update_layout(
            paper_bgcolor=COLOUR["card"], plot_bgcolor=COLOUR["card"],
            font_color=COLOUR["text"], coloraxis_showscale=False,
        )
        st.plotly_chart(fig2, use_container_width=True)


# ── Tab 2: Regional Arbitrage ────────────────────────────────────────────────

def render_arbitrage_tab(
    all_arb: Dict[str, List[TradeOpportunity]],
    cfg: dict,
) -> None:
    st.markdown("<div class='section-title'>🚚 Regional Arbitrage — Jita → Other Hubs</div>", unsafe_allow_html=True)
    st.caption("Buy at Jita sell prices, haul to a regional hub, sell into local buy orders.")

    destinations = list(all_arb.keys())
    if not destinations:
        st.info("No arbitrage opportunities found.")
        return

    dest_tabs = st.tabs([f"📦 {d}" for d in destinations])

    for tab, dest in zip(dest_tabs, destinations):
        trades = all_arb[dest]
        with tab:
            if not trades:
                jumps_note = fetch_jump_count(cfg.get("source_hub","Jita"), dest)
                if jumps_note > cfg.get("arb_max_jumps", 40):
                    st.warning(f"⛔ {dest} is {jumps_note} jumps away — exceeds your **{cfg['arb_max_jumps']}-jump cap**. Raise the slider to include it.")
                else:
                    st.info(f"No opportunities to {dest} with current filters. Try lowering minimum margin or volume.")
                continue

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Opportunities", len(trades))
            c2.metric("Best Margin", f"{trades[0].margin_pct:.1f}%")
            c3.metric("Best Total Profit", isk_label(trades[0].total_profit))
            real_jumps = trades[0].jumps if trades else "—"
            c4.metric("Jumps (ESI route)", real_jumps,
                      help="Actual jump count from EVE route API")

            rows = []
            for t in trades:
                rows.append({
                    "Item":          t.type_name,
                    "Jita Buy":      t.buy_price,
                    f"{dest} Sell":  t.sell_price,
                    "Margin %":      t.margin_pct,
                    "Profit/unit":   t.profit_per_unit,
                    "Volume":        t.trade_volume,
                    "Total Profit":  t.total_profit,
                    "Jumps":         t.jumps,
                    "Haul Cost":     t.fees.get("haul_total", 0),
                })
            df = pd.DataFrame(rows)

            st.dataframe(
                df, use_container_width=True, hide_index=True,
                column_config={
                    "Item":         st.column_config.TextColumn(width="large"),
                    "Jita Buy":     st.column_config.NumberColumn(format="%,.0f ISK"),
                    f"{dest} Sell": st.column_config.NumberColumn(format="%,.0f ISK"),
                    "Margin %":     st.column_config.ProgressColumn(
                                        format="%.1f%%", min_value=0, max_value=60),
                    "Profit/unit":  st.column_config.NumberColumn(format="%,.0f ISK"),
                    "Volume":       st.column_config.NumberColumn(format="%,d"),
                    "Total Profit": st.column_config.NumberColumn(format="%,.0f ISK"),
                    "Haul Cost":    st.column_config.NumberColumn(format="%,.0f ISK"),
                },
            )

            # Profit waterfall chart for top 10
            top10 = df.head(10)
            fig = go.Figure(go.Bar(
                x=top10["Item"],
                y=top10["Total Profit"],
                marker_color=[
                    f"rgba(57,255,20,{0.5 + 0.5 * (i/len(top10))})"
                    for i in range(len(top10), 0, -1)
                ],
                text=[isk_label(v) for v in top10["Total Profit"]],
                textposition="outside",
            ))
            fig.update_layout(
                title=f"Top 10 Profit Opportunities → {dest}",
                paper_bgcolor=COLOUR["card"], plot_bgcolor=COLOUR["card"],
                font_color=COLOUR["text"], xaxis_tickangle=-30,
                yaxis_title="Total Profit (ISK)", margin=dict(t=50, b=100),
            )
            st.plotly_chart(fig, use_container_width=True)


# ── Tab 3: Route Planner ──────────────────────────────────────────────────────

def render_route_tab() -> None:
    st.markdown("<div class='section-title'>🗺️ Route Optimization Engine</div>", unsafe_allow_html=True)
    st.caption("Compare shortest vs. safest paths between trade hubs. Factored into arbitrage profit calculations.")

    hubs = list(HUB_SYSTEMS.keys())
    col_o, col_d, col_btn = st.columns([2, 2, 1])
    with col_o:
        origin = st.selectbox("Origin Hub", hubs, index=0)
    with col_d:
        dest_opts = [h for h in hubs if h != origin]
        destination = st.selectbox("Destination Hub", dest_opts, index=0)
    with col_btn:
        st.markdown("<br>", unsafe_allow_html=True)
        calc = st.button("Calculate Routes", type="primary", use_container_width=True)

    if not calc and "route_result" not in st.session_state:
        st.info("Select two hubs and click **Calculate Routes** to compare paths.")
        return

    if calc:
        with st.spinner(f"Fetching routes {origin} → {destination} from ESI…"):
            routes = fetch_routes(origin, destination)
        st.session_state["route_result"] = routes
        st.session_state["route_pair"] = (origin, destination)

    routes = st.session_state.get("route_result", {})
    pair   = st.session_state.get("route_pair", (origin, destination))

    if not routes:
        st.warning("Could not retrieve route data from ESI.")
        return

    short  = routes.get("shortest")
    secure = routes.get("secure")

    # ── Summary comparison ──
    col_a, col_b = st.columns(2)

    def _route_card(r: Optional[RouteResult], title: str, col) -> None:
        with col:
            if not r:
                st.warning(f"{title}: not available")
                return
            risk_colour = (COLOUR['green'] if r.risk_score == 0
                           else COLOUR['orange'] if r.risk_score <= 25
                           else COLOUR['red'])
            st.markdown(
                f"""<div style="background:{COLOUR['card']};border:1px solid {COLOUR['border']};
                border-radius:10px;padding:16px">
                <div style="color:{COLOUR['accent']};font-weight:700;font-size:1rem">{title}</div>
                <div style="margin:10px 0">
                  <span style="color:{COLOUR['gold']};font-size:1.6rem;font-weight:800">{r.jumps}</span>
                  <span style="color:{COLOUR['subtext']}"> jumps</span>
                </div>
                <div>🟢 Highsec: <b>{r.highsec}</b> &nbsp;|&nbsp;
                     🟡 Lowsec: <b>{r.lowsec}</b> &nbsp;|&nbsp;
                     🔴 Null: <b>{r.nullsec}</b></div>
                <div style="margin-top:8px">Risk: <span style="color:{risk_colour};font-weight:700">{r.risk_label}</span>
                  ({r.risk_score}/100)</div>
                <div style="color:{COLOUR['subtext']};margin-top:4px">Est. {r.est_minutes} min</div>
                </div>""",
                unsafe_allow_html=True,
            )

    _route_card(short,  "⚡ Shortest Route", col_a)
    _route_card(secure, "🛡️ Secure Route",   col_b)

    # ── Recommendation ──
    if short and secure:
        rec = recommend(routes)
        if rec:
            colour = COLOUR['green'] if rec.is_safe else COLOUR['orange']
            overhead = ""
            if rec.flag == "secure" and short and rec.jumps > short.jumps:
                extra = rec.jumps - short.jumps
                overhead = f"  (+{extra} jumps over shortest)"
            st.success(
                f"**Recommended:** {rec.flag.capitalize()} route "
                f"— {rec.jumps} jumps{overhead}  |  {rec.risk_label}"
            )

    # ── System-by-system breakdown ──
    chosen = secure or short
    if chosen and chosen.path:
        st.markdown("<br><div class='section-title'>System Breakdown</div>", unsafe_allow_html=True)
        rows = []
        for node in chosen.path:
            rows.append({
                "System":   node.name,
                "Security": round(node.security, 1),
                "Class":    node.sec_class.capitalize(),
            })
        df_route = pd.DataFrame(rows)
        fig = go.Figure(go.Bar(
            x=df_route["System"],
            y=df_route["Security"],
            marker_color=[
                "#39FF14" if s >= 0.5 else "#FFB347" if s >= 0.1 else "#FF4444"
                for s in df_route["Security"]
            ],
            text=df_route["Class"],
            textposition="outside",
        ))
        fig.update_layout(
            title=f"Security Status Along Route ({pair[0]} → {pair[1]})",
            paper_bgcolor=COLOUR["card"], plot_bgcolor=COLOUR["card"],
            font_color=COLOUR["text"], yaxis=dict(range=[0, 1.1]),
            yaxis_title="Security Status", margin=dict(b=60),
        )
        fig.add_hline(y=0.5, line_dash="dash", line_color="#888",
                      annotation_text="Lowsec boundary", annotation_font_color="#888")
        st.plotly_chart(fig, use_container_width=True)


# ── Tab 4: Profit Calculator ─────────────────────────────────────────────────

def render_calc_tab(cfg: dict) -> None:
    st.markdown("<div class='section-title'>🧮 Trade Profit Calculator</div>", unsafe_allow_html=True)
    st.caption("Verify exact profit for a specific item before committing ISK.")

    mode = st.radio("Trade type", ["Station Flip", "Regional Arbitrage"], horizontal=True)

    c1, c2 = st.columns(2)
    with c1:
        buy_price = st.number_input("Buy price (ISK/unit)", min_value=1, value=1_000_000, step=10_000)
        quantity  = st.number_input("Quantity", min_value=1, value=10, step=1)
    with c2:
        sell_price = st.number_input("Sell price (ISK/unit)", min_value=1, value=1_200_000, step=10_000)
        if mode == "Regional Arbitrage":
            jumps = st.number_input("Jumps to destination", min_value=1, max_value=100, value=9)
        else:
            jumps = 0

    bf  = cfg["broker_fee"]
    st_ = cfg["sales_tax"]
    hl  = cfg["haul_cost"]

    if mode == "Station Flip":
        cost_pu = buy_price  * (1 + bf)
        rev_pu  = sell_price * (1 - bf - st_)
    else:
        haul_pu = (jumps * hl) / max(quantity, 1)
        cost_pu = buy_price + haul_pu
        rev_pu  = sell_price * (1 - bf - st_)

    profit_pu    = rev_pu - cost_pu
    total_profit = profit_pu * quantity
    margin       = (profit_pu / max(cost_pu, 1)) * 100

    colour = COLOUR["green"] if profit_pu > 0 else COLOUR["red"]
    st.markdown(
        f"""<div style="background:{COLOUR['card']};border:1px solid {COLOUR['border']};
        border-radius:10px;padding:20px;margin-top:16px">
        <div style="font-size:0.9rem;color:{COLOUR['subtext']};margin-bottom:12px">RESULT</div>
        <div style="display:flex;gap:40px;flex-wrap:wrap">
          <div>
            <div style="color:{COLOUR['subtext']};font-size:0.8rem">Profit / unit</div>
            <div style="color:{colour};font-size:1.6rem;font-weight:800">{isk_label(profit_pu)}</div>
          </div>
          <div>
            <div style="color:{COLOUR['subtext']};font-size:0.8rem">Total profit ({quantity} units)</div>
            <div style="color:{colour};font-size:1.6rem;font-weight:800">{isk_label(total_profit)}</div>
          </div>
          <div>
            <div style="color:{COLOUR['subtext']};font-size:0.8rem">Net margin</div>
            <div style="color:{colour};font-size:1.6rem;font-weight:800">{margin:.1f}%</div>
          </div>
        </div>
        <div style="margin-top:14px;font-size:0.85rem;color:{COLOUR['subtext']}">
          Broker fee: {isk_label(buy_price * bf * quantity if mode=='Station Flip' else 0 + sell_price * bf * quantity)}  ·
          Sales tax: {isk_label(sell_price * st_ * quantity)}
          {"  ·  Haul: " + isk_label(jumps * hl) if mode=='Regional Arbitrage' else ""}
        </div>
        </div>""",
        unsafe_allow_html=True,
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    cfg = sidebar_config()
    refresh = render_header(cfg)
    if refresh:
        st.rerun()

    tab_overview, tab_station, tab_arb, tab_route, tab_calc = st.tabs([
        "📊 Market Overview",
        "🏪 Station Trading",
        "🚚 Regional Arbitrage",
        "🗺️ Route Planner",
        "🧮 Profit Calculator",
    ])

    source_hub = cfg["source_hub"]
    dest_hubs  = cfg["dest_hubs"]

    # ── Load source hub data ──────────────────────────────────────────────────
    with st.spinner(f"📡 Loading {source_hub} market data from ESI…"):
        try:
            source_raw  = fetch_region_data(HUB_TO_REGION[source_hub], cfg["max_pages"])
            source_book = build_order_book(source_raw)
            st.session_state["last_fetch_ts"] = time.time()
        except Exception as exc:
            st.error(f"Failed to load {source_hub} data: {exc}")
            st.stop()

    # Keep jita_raw alias for the overview tab (it always shows Jita stats)
    jita_raw  = source_raw
    jita_book = source_book

    # ── Resolve type names ────────────────────────────────────────────────────
    all_ids = tuple(sorted(source_book.keys()))
    with st.spinner(f"🔤 Resolving {len(all_ids):,} item names…"):
        names = fetch_names(all_ids)

    # ── Station trading (on source hub) ──────────────────────────────────────
    station_trades = find_station_trades(
        source_book, names,
        min_margin_pct = cfg["st_min_margin"],
        min_volume     = cfg["st_min_vol"],
        min_price      = cfg["st_min_price"],
        max_price      = cfg["st_max_price"],
        broker_fee     = cfg["broker_fee"],
        sales_tax      = cfg["sales_tax"],
    )

    # ── Regional arbitrage ────────────────────────────────────────────────────
    all_arb: Dict[str, List[TradeOpportunity]] = {}

    if not dest_hubs:
        st.warning("No destination hubs selected — pick at least one in the sidebar.")

    for dest in dest_hubs:
        # Get real jump count from ESI (cached 1 h)
        jumps = fetch_jump_count(source_hub, dest)

        if jumps > cfg["arb_max_jumps"]:
            all_arb[dest] = []   # filtered by jump cap — still show tab
            continue

        region_id = HUB_TO_REGION[dest]
        with st.spinner(f"📡 Loading {dest} market data… ({jumps} jumps from {source_hub})"):
            try:
                dest_raw  = fetch_region_data(region_id, max(cfg["max_pages"] // 2, 20))
                dest_book = build_order_book(dest_raw)
                dest_ids  = tuple(sorted(dest_book.keys()))
                combined_names = {**names, **fetch_names(dest_ids)}

                arb = find_regional_arbitrage(
                    source_book, dest_book, combined_names,
                    jumps            = jumps,
                    destination_name = dest,
                    min_margin_pct   = cfg["arb_min_margin"],
                    min_volume       = cfg["arb_min_vol"],
                    broker_fee       = cfg["broker_fee"],
                    sales_tax        = cfg["sales_tax"],
                    haul_per_jump    = cfg["haul_cost"],
                )
                all_arb[dest] = arb
            except Exception as exc:
                st.warning(f"Could not load {dest} data: {exc}")
                all_arb[dest] = []

    # ── Render tabs ───────────────────────────────────────────────────────────
    with tab_overview:
        render_overview_tab(jita_raw, jita_book, names, all_arb, cfg)

    with tab_station:
        render_station_tab(station_trades)

    with tab_arb:
        render_arbitrage_tab(all_arb, cfg)

    with tab_route:
        render_route_tab()

    with tab_calc:
        render_calc_tab(cfg)


if __name__ == "__main__":
    main()
