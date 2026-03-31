"""
grocery_optimizer agent — Fetch grocery list from MS To Do → categorize by store
section → post formatted Discord embed.

LLM usage: YES — categorization, quantity normalization, duplicate detection.

Usage:
    python agent.py              # run once (called by systemd timer Saturday 08:00)
    python agent.py --dry-run    # print embed, no Discord post
"""

import argparse
import logging
import logging.handlers
import os
import pathlib
import sys
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from lib import llm
from lib.discord_out import post_error, send_embed, send_message
from lib.schemas import GroceryOptimizerResponse, GrocerySection

load_dotenv(PROJECT_ROOT / ".env")

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            LOG_DIR / "grocery_optimizer.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
        ),
    ],
)
logger = logging.getLogger("grocery_optimizer")

AGENT = "grocery_optimizer"


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_grocery_items(config: dict) -> list[str]:
    """Fetch items from the Groceries list in Microsoft To Do."""
    import msal
    import httpx

    token_cache_path = pathlib.Path("~/.config/allyx/ms_token_cache.json").expanduser()
    cache = msal.SerializableTokenCache()
    if token_cache_path.exists():
        cache.deserialize(token_cache_path.read_text())

    app = msal.PublicClientApplication(
        os.environ["MS_CLIENT_ID"],
        authority=f"https://login.microsoftonline.com/{os.environ['MS_TENANT_ID']}",
        token_cache=cache,
    )
    scopes = ["Tasks.Read"]
    accounts = app.get_accounts()
    result = None
    if accounts:
        result = app.acquire_token_silent(scopes, account=accounts[0])
    if not result or "access_token" not in result:
        raise RuntimeError("Microsoft auth needed — run calendar_sync to authenticate first")

    token = result["access_token"]
    list_name = config["microsoft"]["todo_list_name"]

    with httpx.Client(timeout=15.0) as client:
        headers = {"Authorization": f"Bearer {token}"}
        lists_resp = client.get(
            "https://graph.microsoft.com/v1.0/me/todo/lists", headers=headers
        )
        lists_resp.raise_for_status()
        todo_list = next(
            (l for l in lists_resp.json()["value"] if l["displayName"] == list_name),
            None,
        )
        if not todo_list:
            logger.warning("To Do list '%s' not found", list_name)
            return []

        tasks_resp = client.get(
            f"https://graph.microsoft.com/v1.0/me/todo/lists/{todo_list['id']}/tasks"
            "?$filter=status ne 'completed'&$top=100",
            headers=headers,
        )
        tasks_resp.raise_for_status()
        return [t["title"] for t in tasks_resp.json().get("value", [])]


# ---------------------------------------------------------------------------
# LLM categorization
# ---------------------------------------------------------------------------

def categorize_items(items: list[str]) -> GroceryOptimizerResponse | None:
    if not items:
        return GroceryOptimizerResponse(sections=[], duplicates_flagged=[])

    task = (
        "Organize this grocery list by store section (e.g. Produce, Dairy, Meat, "
        "Bakery, Frozen, Canned Goods, Beverages, Household, Personal Care, Other). "
        "Normalize quantities where missing (e.g. '1 bunch' for bananas). "
        "Flag any items that look like duplicates of each other."
    )
    input_data = "\n".join(f"- {item}" for item in items)
    return llm.query(task, input_data, GroceryOptimizerResponse, timeout=45.0)


# ---------------------------------------------------------------------------
# Discord formatting
# ---------------------------------------------------------------------------

SECTION_EMOJIS: dict[str, str] = {
    "produce": ":leafy_green:",
    "dairy": ":milk:",
    "meat": ":cut_of_meat:",
    "seafood": ":fish:",
    "bakery": ":bread:",
    "frozen": ":snowflake:",
    "canned": ":canned_food:",
    "beverages": ":beverage_box:",
    "snacks": ":popcorn:",
    "household": ":soap:",
    "personal": ":toothbrush:",
    "other": ":shopping_cart:",
}


def _section_emoji(name: str) -> str:
    for key, emoji in SECTION_EMOJIS.items():
        if key in name.lower():
            return emoji
    return ":shopping_cart:"


def build_embed_fields(result: GroceryOptimizerResponse) -> list[dict[str, str]]:
    fields = []
    for section in result.sections:
        if not section.items:
            continue
        emoji = _section_emoji(section.name)
        lines = []
        for item in section.items:
            qty = f" ({item.quantity})" if item.quantity else ""
            note = f" — _{item.note}_" if item.note else ""
            lines.append(f"• {item.item}{qty}{note}")
        fields.append({
            "name": f"{emoji} {section.name}",
            "value": "\n".join(lines),
            "inline": True,
        })
    if result.duplicates_flagged:
        fields.append({
            "name": ":warning: Possible Duplicates",
            "value": "\n".join(f"• {d}" for d in result.duplicates_flagged),
            "inline": False,
        })
    return fields


def run(config: dict, dry_run: bool = False) -> None:
    try:
        items = fetch_grocery_items(config)
    except Exception as exc:
        logger.error("Failed to fetch grocery items: %s", exc)
        post_error(AGENT, f"MS To Do fetch failed: {exc}", dry_run=dry_run)
        return

    if not items:
        logger.info("Grocery list is empty, skipping")
        send_message("groceries", ":shopping_cart: Your grocery list is empty!", dry_run=dry_run)
        return

    logger.info("Fetched %d grocery items, categorizing...", len(items))

    result = categorize_items(items)
    if result is None:
        logger.error("LLM categorization failed, posting raw list")
        raw_list = "\n".join(f"• {i}" for i in items)
        send_embed(
            "groceries",
            title=":shopping_cart: Grocery List (uncategorized)",
            description=raw_list,
            dry_run=dry_run,
        )
        post_error(AGENT, "LLM categorization failed — posted raw list", dry_run=dry_run)
        return

    total_items = sum(len(s.items) for s in result.sections)
    fields = build_embed_fields(result)

    send_embed(
        channel="groceries",
        title=f":shopping_cart: Grocery List — {total_items} items",
        description=f"Organized into {len(result.sections)} sections",
        fields=fields,
        color=0x57F287,
        dry_run=dry_run,
    )
    logger.info("Posted grocery list: %d items in %d sections", total_items, len(result.sections))


def load_config() -> dict:
    config_path = pathlib.Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def main() -> None:
    parser = argparse.ArgumentParser(description="grocery_optimizer agent")
    parser.add_argument("--dry-run", action="store_true", help="Print embed, no Discord post")
    args = parser.parse_args()

    config = load_config()
    try:
        run(config, dry_run=args.dry_run)
    except Exception as exc:
        logger.exception("grocery_optimizer failed")
        post_error(AGENT, str(exc), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
