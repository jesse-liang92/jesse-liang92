"""
Discord output utilities.

Supports two delivery modes:
1. Webhook: fire-and-forget POST to a webhook URL (most agents)
2. Bot: requires DISCORD_BOT_TOKEN; used by discord_reminders

All functions are synchronous to keep agents simple. For bots
(long-running listeners) use discord.py directly in the agent.
"""

import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Channel → env-var name mapping.  Resolved at call time so that
# agents which call load_dotenv() after importing this module still work.
_WEBHOOK_ENV_KEYS: dict[str, str] = {
    "calendar": "DISCORD_CALENDAR_WEBHOOK",
    "commute": "DISCORD_COMMUTE_WEBHOOK",
    "reminders": "DISCORD_REMINDERS_WEBHOOK",
    "groceries": "DISCORD_GROCERIES_WEBHOOK",
    "finance": "DISCORD_FINANCE_WEBHOOK",
    "packages": "DISCORD_PACKAGES_WEBHOOK",
    "bills": "DISCORD_BILLS_WEBHOOK",
    "agent-status": "DISCORD_STATUS_WEBHOOK",
    "drops": "DISCORD_DROPS_WEBHOOK",
}


def _get_webhook_url(channel: str) -> str:
    """Look up webhook URL at call time, not import time."""
    env_key = _WEBHOOK_ENV_KEYS.get(channel, "")
    return os.getenv(env_key, "") if env_key else ""


def _post_webhook(url: str, payload: dict[str, Any], dry_run: bool = False) -> bool:
    """POST a payload to a Discord webhook. Returns True on success."""
    if dry_run:
        logger.info("[DRY RUN] Would POST to webhook: %s", json.dumps(payload, indent=2))
        return True
    if not url:
        logger.error("Webhook URL is empty — check your .env")
        return False
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, json=payload)
            if resp.status_code in (200, 204):
                return True
            logger.error("Webhook returned %s: %s", resp.status_code, resp.text)
            return False
    except Exception as exc:
        logger.error("Webhook POST failed: %s", exc)
        return False


def send_message(
    channel: str,
    content: str,
    dry_run: bool = False,
) -> bool:
    """
    Post a plain text message to a named channel webhook.

    Args:
        channel: Key from WEBHOOKS dict (e.g. "calendar", "agent-status").
        content: Message text (Discord markdown supported).
        dry_run: If True, print instead of posting.
    """
    url = _get_webhook_url(channel)
    return _post_webhook(url, {"content": content}, dry_run=dry_run)


def send_embed(
    channel: str,
    title: str,
    description: str,
    fields: list[dict[str, str]] | None = None,
    color: int = 0x5865F2,
    dry_run: bool = False,
) -> bool:
    """
    Post a rich embed to a named channel webhook.

    Args:
        channel: Key from WEBHOOKS dict.
        title: Embed title.
        description: Embed body text.
        fields: Optional list of {"name": ..., "value": ..., "inline": ...} dicts.
        color: Embed sidebar color as integer (default Discord blurple).
        dry_run: If True, print instead of posting.
    """
    embed: dict[str, Any] = {
        "title": title,
        "description": description,
        "color": color,
    }
    if fields:
        embed["fields"] = fields

    url = _get_webhook_url(channel)
    return _post_webhook(url, {"embeds": [embed]}, dry_run=dry_run)


def post_error(agent_name: str, error: str, dry_run: bool = False) -> bool:
    """
    Post an error notice to #agent-status.

    Args:
        agent_name: Which agent is reporting the error.
        error: Short description of what went wrong.
        dry_run: If True, print instead of posting.
    """
    content = f":red_circle: **{agent_name}** error: {error}"
    return send_message("agent-status", content, dry_run=dry_run)


def post_status(agent_name: str, message: str, dry_run: bool = False) -> bool:
    """
    Post an informational status to #agent-status (use sparingly).

    Args:
        agent_name: Which agent is reporting.
        message: Status message.
        dry_run: If True, print instead of posting.
    """
    content = f":white_check_mark: **{agent_name}**: {message}"
    return send_message("agent-status", content, dry_run=dry_run)
