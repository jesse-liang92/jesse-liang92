#!/usr/bin/env python3
"""
Restock monitor for https://pvramid.com/collections/in-stock-artisan-field-cloths
Checks every hour and sends a Discord notification when new items come in stock.
"""

import json
import os
import time
import logging
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

COLLECTION_URL = "https://pvramid.com/collections/in-stock-artisan-field-cloths/products.json"
PRODUCT_PAGE_URL = "https://pvramid.com/collections/in-stock-artisan-field-cloths"
DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
STATE_FILE = Path("state.json")
CHECK_INTERVAL_SECONDS = 3600  # 1 hour

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def fetch_products() -> list[dict]:
    """Fetch all products from the Shopify collection JSON endpoint."""
    products = []
    page = 1
    while True:
        resp = requests.get(
            COLLECTION_URL,
            headers=HEADERS,
            params={"limit": 250, "page": page},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json().get("products", [])
        if not data:
            break
        products.extend(data)
        if len(data) < 250:
            break
        page += 1
    return products


def get_in_stock(products: list[dict]) -> dict[str, dict]:
    """
    Return a dict of { product_id: {title, url, variants} }
    for products that have at least one available variant.
    """
    in_stock = {}
    for p in products:
        available_variants = [v for v in p.get("variants", []) if v.get("available")]
        if available_variants:
            in_stock[str(p["id"])] = {
                "title": p["title"],
                "url": f"https://pvramid.com/products/{p['handle']}",
                "variants": [v["title"] for v in available_variants],
            }
    return in_stock


def load_state() -> dict[str, dict]:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict[str, dict]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def send_discord_notification(new_items: dict[str, dict]) -> None:
    """Send a Discord embed for each newly restocked item."""
    for item_id, item in new_items.items():
        variants_str = ", ".join(item["variants"])
        embed = {
            "title": f"Restock: {item['title']}",
            "url": item["url"],
            "color": 0x00C853,  # green
            "fields": [
                {"name": "Available variants", "value": variants_str, "inline": False},
                {"name": "Shop", "value": f"[View collection]({PRODUCT_PAGE_URL})", "inline": False},
            ],
            "footer": {"text": f"Detected at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"},
        }
        payload = {
            "content": "@everyone New restock detected!",
            "embeds": [embed],
        }
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        resp.raise_for_status()
        log.info("Discord notification sent for: %s", item["title"])


def check_once() -> None:
    log.info("Checking for restocks...")
    try:
        products = fetch_products()
    except requests.HTTPError as e:
        log.error("Failed to fetch products: %s", e)
        return

    current = get_in_stock(products)
    previous = load_state()

    # New items = in stock now but not in stock last time
    new_items = {k: v for k, v in current.items() if k not in previous}

    if new_items:
        log.info("New restock(s) found: %s", [v["title"] for v in new_items.values()])
        try:
            send_discord_notification(new_items)
        except requests.HTTPError as e:
            log.error("Discord notification failed: %s", e)
    else:
        log.info("No new restocks. %d item(s) currently in stock.", len(current))

    save_state(current)


def main() -> None:
    log.info("Restock monitor started. Checking every %d minutes.", CHECK_INTERVAL_SECONDS // 60)
    while True:
        check_once()
        log.info("Next check in %d minutes.", CHECK_INTERVAL_SECONDS // 60)
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
