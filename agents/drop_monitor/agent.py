"""
drop_monitor agent — Watch Shopify stores for new product drops.

Polls Shopify /products.json endpoints, tracks seen products in SQLite,
and alerts via Discord when new items appear.

LLM usage: None — purely deterministic.

Usage:
    python agent.py              # poll once (run on a schedule)
    python agent.py --dry-run    # print alerts, no Discord post
    python agent.py --list       # show all tracked products
    python agent.py --reset      # clear seen products (re-alerts on next run)
"""

import argparse
import json
import logging
import logging.handlers
import os
import pathlib
import sqlite3
import sys
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import yaml
from dotenv import load_dotenv

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from lib.discord_out import post_error, send_embed

load_dotenv(PROJECT_ROOT / ".env")

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            LOG_DIR / "drop_monitor.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
        ),
    ],
)
logger = logging.getLogger("drop_monitor")

AGENT = "drop_monitor"


# ---------------------------------------------------------------------------
# SQLite persistence
# ---------------------------------------------------------------------------

def init_db(db_path: str) -> sqlite3.Connection:
    path = pathlib.Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_products (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            store           TEXT NOT NULL,
            product_id      TEXT NOT NULL,
            handle          TEXT NOT NULL,
            title           TEXT NOT NULL,
            price           TEXT DEFAULT '',
            image_url       TEXT DEFAULT '',
            product_url     TEXT DEFAULT '',
            created_at      TEXT DEFAULT '',
            first_seen_at   TEXT NOT NULL,
            UNIQUE(store, product_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS restock_tracking (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            store           TEXT NOT NULL,
            product_id      TEXT NOT NULL,
            handle          TEXT NOT NULL,
            title           TEXT NOT NULL,
            price           TEXT DEFAULT '',
            image_url       TEXT DEFAULT '',
            available       INTEGER DEFAULT 0,
            last_checked_at TEXT NOT NULL,
            UNIQUE(store, product_id)
        )
    """)
    conn.commit()
    return conn


def is_product_seen(conn: sqlite3.Connection, store: str, product_id: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM seen_products WHERE store=? AND product_id=?",
        (store, product_id),
    )
    return cur.fetchone() is not None


def mark_product_seen(
    conn: sqlite3.Connection,
    store: str,
    product: dict[str, Any],
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT OR IGNORE INTO seen_products
           (store, product_id, handle, title, price, image_url, product_url, created_at, first_seen_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            store,
            str(product["id"]),
            product.get("handle", ""),
            product.get("title", "Unknown"),
            _get_price(product),
            _get_image(product),
            _get_product_url(product, store),
            product.get("published_at", product.get("created_at", "")),
            now,
        ),
    )
    conn.commit()


def list_seen_products(conn: sqlite3.Connection, store: str | None = None) -> list[dict[str, Any]]:
    query = "SELECT store, product_id, title, price, product_url, first_seen_at FROM seen_products"
    params: list[str] = []
    if store:
        query += " WHERE store=?"
        params.append(store)
    query += " ORDER BY first_seen_at DESC"
    cur = conn.execute(query, params)
    cols = ["store", "product_id", "title", "price", "product_url", "first_seen_at"]
    return [dict(zip(cols, row)) for row in cur]


def reset_seen_products(conn: sqlite3.Connection, store: str | None = None) -> int:
    if store:
        cur = conn.execute("DELETE FROM seen_products WHERE store=?", (store,))
    else:
        cur = conn.execute("DELETE FROM seen_products")
    conn.commit()
    return cur.rowcount


# ---------------------------------------------------------------------------
# Restock tracking persistence
# ---------------------------------------------------------------------------

def _is_product_available(product: dict[str, Any]) -> bool:
    """Check if any variant is available."""
    for variant in product.get("variants", []):
        if variant.get("available", False):
            return True
    return False


def get_tracked_availability(conn: sqlite3.Connection, store: str, product_id: str) -> bool | None:
    """Get last known availability. Returns None if not tracked yet."""
    cur = conn.execute(
        "SELECT available FROM restock_tracking WHERE store=? AND product_id=?",
        (store, product_id),
    )
    row = cur.fetchone()
    return bool(row[0]) if row else None


def upsert_restock_tracking(
    conn: sqlite3.Connection,
    store: str,
    product: dict[str, Any],
    available: bool,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO restock_tracking
           (store, product_id, handle, title, price, image_url, available, last_checked_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(store, product_id) DO UPDATE SET
               available=excluded.available,
               price=excluded.price,
               last_checked_at=excluded.last_checked_at""",
        (
            store,
            str(product["id"]),
            product.get("handle", ""),
            product.get("title", "Unknown"),
            _get_price(product),
            _get_image(product),
            int(available),
            now,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Shopify product fetching
# ---------------------------------------------------------------------------

def _get_price(product: dict[str, Any]) -> str:
    variants = product.get("variants", [])
    if variants:
        price = variants[0].get("price", "")
        if price:
            return f"${float(price):.2f}"
    return ""


def _get_image(product: dict[str, Any]) -> str:
    images = product.get("images", [])
    if images:
        return images[0].get("src", "")
    return ""


def _get_product_url(product: dict[str, Any], store_key: str) -> str:
    handle = product.get("handle", "")
    # Extract base domain from the store config products_json URL
    # For now, hardcode known stores
    base_urls = {
        "pvramid_chroma": "https://pvramid.com",
        "pvramid_instock": "https://pvramid.com",
    }
    base = base_urls.get(store_key, "")
    if base and handle:
        return f"{base}/products/{handle}"
    return ""


def fetch_products(products_json_url: str) -> list[dict[str, Any]] | None:
    """Fetch products from a Shopify collection's products.json endpoint."""
    try:
        all_products: list[dict[str, Any]] = []
        page = 1
        while True:
            with httpx.Client(timeout=15.0) as client:
                resp = client.get(
                    products_json_url,
                    params={"page": page, "limit": 250},
                    headers={"User-Agent": "AllYX-DropMonitor/1.0"},
                )

                # Back off on rate limit
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "30"))
                    logger.warning("Rate limited (429). Backing off %ds", retry_after)
                    time.sleep(retry_after)
                    continue

                resp.raise_for_status()
                data = resp.json()

            products = data.get("products", [])
            if not products:
                break
            all_products.extend(products)
            page += 1

        return all_products
    except Exception as exc:
        logger.error("Failed to fetch products from %s: %s", products_json_url, exc)
        return None


# ---------------------------------------------------------------------------
# Discord formatting
# ---------------------------------------------------------------------------

def build_drop_embed(product: dict[str, Any], store_name: str) -> dict[str, Any]:
    """Build a Discord embed for a new product drop."""
    title = product.get("title", "New Product")
    price = _get_price(product)
    image_url = _get_image(product)
    handle = product.get("handle", "")
    product_url = f"https://pvramid.com/products/{handle}" if handle else ""

    fields: list[dict[str, Any]] = []

    if price:
        fields.append({"name": "Price", "value": price, "inline": True})

    fields.append({"name": "Store", "value": store_name, "inline": True})

    if product_url:
        fields.append({"name": "Link", "value": f"[View Product]({product_url})", "inline": False})

    vendor = product.get("vendor", "")
    if vendor:
        fields.append({"name": "Vendor", "value": vendor, "inline": True})

    product_type = product.get("product_type", "")
    if product_type:
        fields.append({"name": "Type", "value": product_type, "inline": True})

    embed: dict[str, Any] = {
        "title": f":rotating_light: NEW DROP: {title}",
        "description": product_url if product_url else "New item listed!",
        "fields": fields,
        "color": 0xFFD700,  # gold
    }

    if image_url:
        embed["image"] = {"url": image_url}

    return embed


def build_restock_embed(product: dict[str, Any], store_name: str) -> dict[str, Any]:
    """Build a Discord embed for a restocked product."""
    title = product.get("title", "Restocked Product")
    price = _get_price(product)
    image_url = _get_image(product)
    handle = product.get("handle", "")
    product_url = f"https://pvramid.com/products/{handle}" if handle else ""

    fields: list[dict[str, Any]] = []

    if price:
        fields.append({"name": "Price", "value": price, "inline": True})

    fields.append({"name": "Store", "value": store_name, "inline": True})

    if product_url:
        fields.append({"name": "Link", "value": f"[Buy Now]({product_url})", "inline": False})

    embed: dict[str, Any] = {
        "title": f":green_circle: BACK IN STOCK: {title}",
        "description": product_url if product_url else "Item is available again!",
        "fields": fields,
        "color": 0x57F287,  # green
    }

    if image_url:
        embed["image"] = {"url": image_url}

    return embed


# ---------------------------------------------------------------------------
# Main polling logic
# ---------------------------------------------------------------------------

def poll_stores(
    conn: sqlite3.Connection,
    config: dict,
    dry_run: bool = False,
    seed: bool = False,
) -> int:
    """
    Poll all configured stores for new products.
    Returns count of new products found.

    If seed=True, marks all current products as seen without alerting.
    """
    stores = config.get("stores", {})
    total_new = 0

    for store_key, store_config in stores.items():
        store_name = store_config.get("name", store_key)
        products_url = store_config.get("products_json", "")

        if not products_url:
            logger.warning("No products_json URL for store %s", store_key)
            continue

        logger.info("Checking %s...", store_name)
        products = fetch_products(products_url)

        if products is None:
            post_error(AGENT, f"Failed to fetch {store_name}", dry_run=dry_run)
            continue

        logger.info("Found %d products in %s", len(products), store_name)

        new_products = []
        for product in products:
            product_id = str(product.get("id", ""))
            if not product_id:
                continue

            if not is_product_seen(conn, store_key, product_id):
                if seed:
                    # First run: just mark as seen, don't alert
                    mark_product_seen(conn, store_key, product)
                    logger.info("Seeded: %s", product.get("title", "?"))
                else:
                    new_products.append(product)

        if seed:
            logger.info("Seeded %d products for %s", len(products), store_name)
            continue

        for product in new_products:
            product_id = str(product["id"])
            title = product.get("title", "Unknown")
            logger.info("NEW DROP: %s", title)

            mark_product_seen(conn, store_key, product)

            embed = build_drop_embed(product, store_name)

            send_embed(
                channel="drops",
                title=embed["title"],
                description=embed["description"],
                fields=embed["fields"],
                color=embed["color"],
                dry_run=dry_run,
            )
            total_new += 1

        if not new_products:
            logger.info("No new products in %s", store_name)

    return total_new


def poll_restocks(
    conn: sqlite3.Connection,
    config: dict,
    dry_run: bool = False,
    seed: bool = False,
) -> int:
    """
    Poll restock-watch stores for availability changes.
    Returns count of restocked products found.

    If seed=True, records current availability without alerting.
    """
    stores = config.get("restock_watches", {})
    total_restocked = 0

    for store_key, store_config in stores.items():
        store_name = store_config.get("name", store_key)
        products_url = store_config.get("products_json", "")

        if not products_url:
            logger.warning("No products_json URL for restock watch %s", store_key)
            continue

        logger.info("Checking restocks for %s...", store_name)
        products = fetch_products(products_url)

        if products is None:
            post_error(AGENT, f"Failed to fetch restocks for {store_name}", dry_run=dry_run)
            continue

        logger.info("Tracking %d products in %s", len(products), store_name)

        for product in products:
            product_id = str(product.get("id", ""))
            if not product_id:
                continue

            available = _is_product_available(product)
            prev_available = get_tracked_availability(conn, store_key, product_id)

            # Update tracking state
            upsert_restock_tracking(conn, store_key, product, available)

            if seed:
                logger.info("Restock tracked: %s (available=%s)", product.get("title", "?"), available)
                continue

            # Alert on restock: was unavailable (or unknown), now available
            if available and prev_available is not None and not prev_available:
                title = product.get("title", "Unknown")
                logger.info("RESTOCKED: %s", title)

                embed = build_restock_embed(product, store_name)
                send_embed(
                    channel="drops",
                    title=embed["title"],
                    description=embed["description"],
                    fields=embed["fields"],
                    color=embed["color"],
                    dry_run=dry_run,
                )
                total_restocked += 1

        if seed:
            logger.info("Seeded restock tracking for %d products in %s", len(products), store_name)

    return total_restocked


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def load_config() -> dict:
    config_path = pathlib.Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def main() -> None:
    parser = argparse.ArgumentParser(description="drop_monitor agent")
    parser.add_argument("--dry-run", action="store_true", help="Print alerts, no Discord post")
    parser.add_argument("--list", action="store_true", dest="list_all", help="List all seen products")
    parser.add_argument("--reset", action="store_true", help="Clear seen products DB")
    parser.add_argument("--seed", action="store_true",
                        help="Mark all current products as seen without alerting (first run)")
    parser.add_argument("--watch", action="store_true",
                        help="Run continuously, polling at the configured interval")
    args = parser.parse_args()

    config = load_config()
    db_conn = init_db(config["database"]["path"])

    # --- List ---
    if args.list_all:
        products = list_seen_products(db_conn)
        if not products:
            print("No products tracked yet.")
            return
        for p in products:
            print(f"  [{p['store']}] {p['title']} — {p['price']} — seen {p['first_seen_at'][:10]}")
        return

    # --- Reset ---
    if args.reset:
        count = reset_seen_products(db_conn)
        print(f"Cleared {count} tracked products.")
        return

    # --- Seed (first run) ---
    if args.seed:
        poll_stores(db_conn, config, dry_run=True, seed=True)
        poll_restocks(db_conn, config, dry_run=True, seed=True)
        print("Seeded existing products and restock tracking. Future runs will only alert on changes.")
        return

    # --- Poll (single or watch mode) ---
    def _is_active_hours() -> bool:
        schedule = config.get("schedule", {})
        start_hour = schedule.get("active_hours_start")
        end_hour = schedule.get("active_hours_end")
        if start_hour is not None and end_hour is not None:
            now_pt = datetime.now(ZoneInfo("America/Los_Angeles"))
            current = now_pt.hour + now_pt.minute / 60
            if not (float(start_hour) <= current < float(end_hour)):
                logger.info("Outside active hours (%.2f-%.2f PT, now %.2f), sleeping",
                            float(start_hour), float(end_hour), current)
                return False
        return True

    def _run_poll() -> None:
        """Single poll: new drops + restocks."""
        if not _is_active_hours():
            return
        try:
            new_count = poll_stores(db_conn, config, dry_run=args.dry_run)
            restock_count = poll_restocks(db_conn, config, dry_run=args.dry_run)
            logger.info("Poll complete. %d new drop(s), %d restock(s).", new_count, restock_count)
        except Exception as exc:
            logger.exception("drop_monitor failed")
            post_error(AGENT, str(exc), dry_run=args.dry_run)

    if args.watch:
        drop_interval = int(config.get("schedule", {}).get("interval_minutes", 3))
        restock_interval = int(config.get("restock_schedule", {}).get("interval_minutes", 30))
        logger.info("Watch mode — drops every %dm, restocks every %dm. Ctrl+C to stop.",
                     drop_interval, restock_interval)
        # Seed on first watch if DB is empty
        if not list_seen_products(db_conn):
            logger.info("No products in DB, seeding...")
            poll_stores(db_conn, config, dry_run=True, seed=True)
            poll_restocks(db_conn, config, dry_run=True, seed=True)
            logger.info("Seed complete. Now watching.")
        try:
            cycles = 0
            restock_every_n = max(1, restock_interval // drop_interval)
            while True:
                if not _is_active_hours():
                    time.sleep(drop_interval * 60)
                    continue
                try:
                    # Always check drops
                    new_count = poll_stores(db_conn, config, dry_run=args.dry_run)
                    # Check restocks less frequently
                    restock_count = 0
                    if cycles % restock_every_n == 0:
                        restock_count = poll_restocks(db_conn, config, dry_run=args.dry_run)
                    logger.info("Cycle %d: %d new drop(s), %d restock(s).", cycles, new_count, restock_count)
                except Exception as exc:
                    logger.exception("Poll cycle failed")
                    post_error(AGENT, str(exc), dry_run=args.dry_run)
                cycles += 1
                time.sleep(drop_interval * 60)
        except KeyboardInterrupt:
            logger.info("Watch mode stopped.")
    else:
        _run_poll()


if __name__ == "__main__":
    main()
