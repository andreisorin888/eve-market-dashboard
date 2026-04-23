"""
ESI API client — synchronous requests + ThreadPoolExecutor for parallel page fetching.
EVE ESI market endpoints are fully public; no OAuth or API key required.
"""

import json
import logging
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from constants import ESI_BASE, ESI_HEADERS

logger = logging.getLogger(__name__)

# On Streamlit Cloud the repo root is read-only; /tmp is always writable.
# Locally we prefer keeping the cache next to the project for persistence.
_LOCAL_DB = os.path.join(os.path.dirname(__file__), "data", "cache.db")
DB_PATH   = _LOCAL_DB if os.access(os.path.dirname(_LOCAL_DB) or ".", os.W_OK) else "/tmp/eve_cache.db"


# ── HTTP session with retries ─────────────────────────────────────────────────

def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(ESI_HEADERS)
    retry = Retry(total=3, backoff_factor=0.5,
                  status_forcelist=[500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


SESSION = _make_session()


# ── SQLite cache ──────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


SCHEMA_VERSION = 3   # bump this whenever tables change

def init_db() -> None:
    with _conn() as db:
        db.execute("PRAGMA journal_mode=WAL")
        # Drop and recreate tables if schema version changed
        cur_ver = db.execute("PRAGMA user_version").fetchone()[0]
        if cur_ver != SCHEMA_VERSION:
            db.executescript("""
                DROP TABLE IF EXISTS region_orders;
                DROP TABLE IF EXISTS item_names;
                DROP TABLE IF EXISTS routes;
            """)
        db.executescript(f"""
            PRAGMA user_version = {SCHEMA_VERSION};
            CREATE TABLE IF NOT EXISTS region_orders (
                region_id INTEGER PRIMARY KEY,
                data      TEXT    NOT NULL,
                ts        REAL    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS item_names (
                type_id   INTEGER PRIMARY KEY,
                name      TEXT    NOT NULL,
                ts        REAL    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS routes (
                cache_key TEXT    PRIMARY KEY,
                data      TEXT    NOT NULL,
                ts        REAL    NOT NULL
            );
        """)


init_db()


# ── Market orders ─────────────────────────────────────────────────────────────

def get_region_orders(
    region_id: int,
    ttl: int = 300,
    max_pages: int = 80,
) -> List[Dict]:
    """
    Return all active market orders for a region.
    Results cached in SQLite for `ttl` seconds.
    max_pages × 1 000 orders = upper bound on data fetched per region.
    """
    with _conn() as db:
        row = db.execute(
            "SELECT data, ts FROM region_orders WHERE region_id = ?", (region_id,)
        ).fetchone()
        if row and (time.time() - row["ts"]) < ttl:
            return json.loads(row["data"])

    orders = _fetch_all_pages(region_id, max_pages)

    with _conn() as db:
        db.execute(
            "INSERT OR REPLACE INTO region_orders (region_id, data, ts) VALUES (?,?,?)",
            (region_id, json.dumps(orders), time.time()),
        )
    return orders


def _fetch_page(region_id: int, order_type: str, page: int) -> List[Dict]:
    r = SESSION.get(
        f"{ESI_BASE}/markets/{region_id}/orders/",
        params={"order_type": order_type, "page": page},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def _fetch_side(region_id: int, order_type: str, max_pages: int) -> List[Dict]:
    """Fetch one side (buy OR sell) with parallel page fetching."""
    r = SESSION.get(
        f"{ESI_BASE}/markets/{region_id}/orders/",
        params={"order_type": order_type, "page": 1},
        timeout=30,
    )
    r.raise_for_status()
    total_pages = min(int(r.headers.get("X-Pages", 1)), max_pages)
    all_data: List[Dict] = r.json()

    if total_pages <= 1:
        return all_data

    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {
            pool.submit(_fetch_page, region_id, order_type, p): p
            for p in range(2, total_pages + 1)
        }
        for future in as_completed(futures):
            try:
                all_data.extend(future.result())
            except Exception as exc:
                logger.warning("Page %d (%s) fetch failed: %s", futures[future], order_type, exc)

    return all_data


def _fetch_all_pages(region_id: int, max_pages: int) -> List[Dict]:
    """
    Fetch buy and sell orders separately — ESI paginates them in blocks
    (sell orders first, then buy orders), so fetching 'all' with a page cap
    returns only one side.  Fetching each side independently guarantees
    both are fully represented within the page budget.
    """
    sell = _fetch_side(region_id, "sell", max_pages)
    buy  = _fetch_side(region_id, "buy",  max_pages)
    return sell + buy


# ── Item name resolution ──────────────────────────────────────────────────────

def resolve_names(type_ids: List[int]) -> Dict[int, str]:
    """Return {type_id: name}. Uses SQLite cache; fetches missing IDs from ESI."""
    if not type_ids:
        return {}

    with _conn() as db:
        ph = ",".join("?" * len(type_ids))
        rows = db.execute(
            f"SELECT type_id, name FROM item_names WHERE type_id IN ({ph})", type_ids
        ).fetchall()
    cached = {r["type_id"]: r["name"] for r in rows}

    missing = [t for t in type_ids if t not in cached]
    if not missing:
        return cached

    fetched: Dict[int, str] = {}
    for i in range(0, len(missing), 1000):
        batch = missing[i : i + 1000]
        try:
            r = SESSION.post(
                f"{ESI_BASE}/universe/names/", json=batch, timeout=30
            )
            if r.status_code == 200:
                for item in r.json():
                    fetched[item["id"]] = item["name"]
        except Exception as exc:
            logger.warning("Name resolution batch failed: %s", exc)

    if fetched:
        with _conn() as db:
            db.executemany(
                "INSERT OR REPLACE INTO item_names (type_id, name, ts) VALUES (?,?,?)",
                [(tid, name, time.time()) for tid, name in fetched.items()],
            )

    return {**cached, **fetched}


# ── Route fetching ────────────────────────────────────────────────────────────

def get_route(
    origin: int,
    destination: int,
    flag: str = "shortest",
    ttl: int = 3600,
) -> Optional[List[int]]:
    """Return ordered list of system IDs for the route. Cached 1 hour."""
    key = f"{origin}:{destination}:{flag}"

    with _conn() as db:
        row = db.execute(
            "SELECT data, ts FROM routes WHERE cache_key = ?", (key,)
        ).fetchone()
        if row and (time.time() - row["ts"]) < ttl:
            return json.loads(row["data"])

    try:
        r = SESSION.get(
            f"{ESI_BASE}/route/{origin}/{destination}/",
            params={"flag": flag},
            timeout=30,
        )
        if r.status_code == 200:
            route = r.json()
            with _conn() as db:
                db.execute(
                    "INSERT OR REPLACE INTO routes (cache_key, data, ts) VALUES (?,?,?)",
                    (key, json.dumps(route), time.time()),
                )
            return route
        logger.warning("Route ESI returned %d", r.status_code)
    except Exception as exc:
        logger.warning("Route fetch %d→%d (%s) failed: %s", origin, destination, flag, exc)

    return None
