"""
finance_digest agent — Daily market summary posted to #finance.

Pulls price data from Yahoo Finance via yfinance (no API key needed).
LLM generates a natural-language summary and flags notable moves.
Runs weekdays at market close (1:00 PM PT / 4:00 PM ET).

Usage:
    python agent.py              # run once (called by systemd timer)
    python agent.py --dry-run    # print output, no Discord post
"""

import argparse
import logging
import logging.handlers
import os
import pathlib
import sys
from datetime import datetime, timezone
from typing import Any

import httpx
import yaml
import yfinance as yf
from dotenv import load_dotenv

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from lib import llm
from lib.discord_out import post_error, send_embed
from lib.schemas import FinanceDigestResponse

load_dotenv(PROJECT_ROOT / ".env")

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            LOG_DIR / "finance_digest.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
        ),
    ],
)
logger = logging.getLogger("finance_digest")

AGENT = "finance_digest"

# Thresholds for flagging notable moves
ALERT_PCT_THRESHOLD = 2.0  # flag moves >= 2%


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------

def fetch_watchlist_data(watchlist: list[dict[str, str]]) -> list[dict[str, Any]]:
    """
    Fetch current price data for all tickers in the watchlist.
    Returns a list of dicts with ticker, label, price, change, change_pct, etc.
    """
    tickers = [item["ticker"] for item in watchlist]
    label_map = {item["ticker"]: item.get("label", item["ticker"]) for item in watchlist}

    results: list[dict[str, Any]] = []

    try:
        data = yf.download(tickers, period="2d", group_by="ticker", progress=False)
    except Exception as exc:
        logger.error("yfinance download failed: %s", exc)
        return results

    for ticker in tickers:
        try:
            if len(tickers) == 1:
                ticker_data = data
            else:
                ticker_data = data[ticker]

            # Get last two days of close prices
            closes = ticker_data["Close"].dropna()
            if len(closes) < 2:
                logger.warning("Insufficient data for %s", ticker)
                continue

            prev_close = float(closes.iloc[-2])
            current_close = float(closes.iloc[-1])
            change = current_close - prev_close
            change_pct = (change / prev_close) * 100 if prev_close != 0 else 0.0

            results.append({
                "ticker": ticker,
                "label": label_map[ticker],
                "price": round(current_close, 2),
                "prev_close": round(prev_close, 2),
                "change": round(change, 2),
                "change_pct": round(change_pct, 2),
            })
        except Exception as exc:
            logger.warning("Failed to process %s: %s", ticker, exc)
            continue

    return results


def identify_alerts(market_data: list[dict[str, Any]], threshold: float = ALERT_PCT_THRESHOLD) -> list[str]:
    """Flag tickers with moves exceeding the threshold."""
    alerts: list[str] = []
    for item in market_data:
        pct = abs(item["change_pct"])
        if pct >= threshold:
            direction = "up" if item["change"] > 0 else "down"
            alerts.append(
                f"{item['label']} ({item['ticker']}) {direction} {item['change_pct']:+.2f}% "
                f"to ${item['price']:.2f}"
            )
    return alerts


# ---------------------------------------------------------------------------
# LLM summary
# ---------------------------------------------------------------------------

def generate_summary(
    market_data: list[dict[str, Any]],
    alerts: list[str],
    timeout: float = 45.0,
) -> FinanceDigestResponse | None:
    """Ask the LLM to generate a natural-language market summary."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Build a compact text representation — keep token count low
    lines = [f"{now}"]
    for item in market_data:
        lines.append(f"{item['ticker']} ${item['price']:.2f} {item['change_pct']:+.1f}%")

    input_data = "\n".join(lines)

    task = (
        "Write a 2-3 sentence market summary for a biotech R&D director who also tracks "
        "AI/semis. Be analytical, not hype — say 'rose' not 'surged' for <5% moves. "
        "Compare small-cap vs large-cap performance to signal breadth. "
        "Flag life science tools (ILMN, TMO) and biotech ETFs specifically. "
        "No generic sentiment statements — only observations supported by the numbers."
    )

    return llm.query(task, input_data, FinanceDigestResponse, timeout=timeout)


# ---------------------------------------------------------------------------
# Discord formatting
# ---------------------------------------------------------------------------

def build_market_embed(
    market_data: list[dict[str, Any]],
    summary: str,
    alerts: list[str],
) -> dict[str, Any]:
    """Build a Discord embed for the market digest."""
    now = datetime.now(timezone.utc).strftime("%B %d, %Y")

    # Group tickers by category for cleaner display
    categories = {
        "Broad Market": ["SPY", "QQQ", "DIA", "IWM"],
        "AI / Semis": ["NVDA", "ASML", "AMD", "MSFT", "GOOG"],
        "Tech": ["NFLX", "META", "AMZN"],
        "Biotech": ["XBI", "IBB", "ILMN", "TMO"],
    }

    ticker_map = {item["ticker"]: item for item in market_data}

    fields: list[dict[str, Any]] = []

    for category, tickers in categories.items():
        lines = []
        for t in tickers:
            item = ticker_map.get(t)
            if item is None:
                continue
            arrow = ":green_circle:" if item["change"] >= 0 else ":red_circle:"
            lines.append(
                f"{arrow} **{item['label']}** ${item['price']:.2f} "
                f"({item['change_pct']:+.2f}%)"
            )
        if lines:
            fields.append({
                "name": category,
                "value": "\n".join(lines),
                "inline": True,
            })

    # Add any tickers not in predefined categories
    known_tickers = {t for tl in categories.values() for t in tl}
    extras = [item for item in market_data if item["ticker"] not in known_tickers]
    if extras:
        lines = []
        for item in extras:
            arrow = ":green_circle:" if item["change"] >= 0 else ":red_circle:"
            lines.append(
                f"{arrow} **{item['label']}** ${item['price']:.2f} "
                f"({item['change_pct']:+.2f}%)"
            )
        fields.append({"name": "Other", "value": "\n".join(lines), "inline": True})

    if alerts:
        fields.append({
            "name": ":warning: Notable Moves",
            "value": "\n".join(f"• {a}" for a in alerts),
            "inline": False,
        })

    # Overall market color: green if SPY up, red if down, grey if missing
    spy = ticker_map.get("SPY")
    if spy:
        color = 0x57F287 if spy["change"] >= 0 else 0xED4245
    else:
        color = 0x95A5A6

    return {
        "title": f":chart_with_upwards_trend: Market Digest — {now}",
        "description": summary,
        "fields": fields,
        "color": color,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_config() -> dict:
    config_path = pathlib.Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def run_digest(config: dict, dry_run: bool = False) -> None:
    """Run the finance digest pipeline."""
    watchlist = config.get("watchlist", [])
    if not watchlist:
        logger.error("No tickers in watchlist config")
        return

    # 1. Fetch market data
    logger.info("Fetching market data for %d tickers", len(watchlist))
    market_data = fetch_watchlist_data(watchlist)

    if not market_data:
        logger.error("No market data returned — market may be closed or yfinance failed")
        post_error(AGENT, "No market data returned", dry_run=dry_run)
        return

    logger.info("Got data for %d/%d tickers", len(market_data), len(watchlist))

    # 2. Identify notable moves
    alerts = identify_alerts(market_data)
    if alerts:
        logger.info("Notable moves: %s", alerts)

    # 3. Generate LLM summary
    llm_timeout = float(config.get("llm", {}).get("timeout_seconds", 45))
    use_llm = config.get("llm", {}).get("enabled", True)

    summary = "Market data retrieved. LLM summary unavailable."

    if use_llm:
        logger.info("Generating LLM summary...")
        response = generate_summary(market_data, alerts, timeout=llm_timeout)
        if response is not None:
            summary = response.summary
            # Merge any LLM-generated alerts with deterministic ones
            for alert in response.alerts:
                if alert not in alerts:
                    alerts.append(alert)
        else:
            logger.warning("LLM summary failed, using fallback")
    else:
        logger.info("LLM disabled, using deterministic summary")

    # 4. Build and send embed
    embed = build_market_embed(market_data, summary, alerts)

    send_embed(
        channel="finance",
        title=embed["title"],
        description=embed["description"],
        fields=embed["fields"],
        color=embed["color"],
        dry_run=dry_run,
    )

    logger.info("Finance digest posted%s", " (dry run)" if dry_run else "")


def main() -> None:
    parser = argparse.ArgumentParser(description="finance_digest agent")
    parser.add_argument("--dry-run", action="store_true", help="Print output, no Discord post")
    args = parser.parse_args()

    config = load_config()

    try:
        run_digest(config, dry_run=args.dry_run)
    except Exception as exc:
        logger.exception("finance_digest failed")
        post_error(AGENT, str(exc), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
