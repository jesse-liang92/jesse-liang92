"""
discord_reminders agent — Discord bot that accepts natural-language reminder commands.

LLM usage: YES — parses natural language time expressions.

Commands:
    !remind <text>      Set a reminder
    !reminders          List pending reminders
    !cancel <id>        Cancel a reminder by ID

Usage:
    python agent.py              # run as long-lived bot (systemd service)
    python agent.py --dry-run    # connect and log commands, don't fire reminders
"""

import argparse
import asyncio
import logging
import logging.handlers
import pathlib
import sqlite3
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

PT = ZoneInfo("America/Los_Angeles")
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from lib import llm
from lib.discord_out import post_error
from lib.schemas import ReminderParseResponse

load_dotenv(PROJECT_ROOT / ".env")

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            LOG_DIR / "discord_reminders.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
        ),
    ],
)
logger = logging.getLogger("discord_reminders")

AGENT = "discord_reminders"


# ---------------------------------------------------------------------------
# SQLite persistence
# ---------------------------------------------------------------------------

def init_db(db_path: str) -> sqlite3.Connection:
    path = pathlib.Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            task        TEXT NOT NULL,
            remind_at   TEXT NOT NULL,   -- ISO 8601
            recurrence  TEXT DEFAULT 'none',
            channel_id  TEXT,
            user_id     TEXT,
            created_at  TEXT NOT NULL,
            fired       INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    return conn


def add_reminder(
    conn: sqlite3.Connection,
    task: str,
    remind_at: str,
    recurrence: str,
    channel_id: str,
    user_id: str,
) -> int:
    cur = conn.execute(
        """INSERT INTO reminders (task, remind_at, recurrence, channel_id, user_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (task, remind_at, recurrence, channel_id, user_id, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return cur.lastrowid


def list_reminders(conn: sqlite3.Connection, user_id: str) -> list[dict[str, Any]]:
    cur = conn.execute(
        "SELECT id, task, remind_at, recurrence FROM reminders WHERE user_id=? AND fired=0 ORDER BY remind_at",
        (user_id,),
    )
    return [{"id": r[0], "task": r[1], "remind_at": r[2], "recurrence": r[3]} for r in cur]


def cancel_reminder(conn: sqlite3.Connection, reminder_id: int, user_id: str) -> bool:
    cur = conn.execute(
        "DELETE FROM reminders WHERE id=? AND user_id=?",
        (reminder_id, user_id),
    )
    conn.commit()
    return cur.rowcount > 0


def get_due_reminders(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "SELECT id, task, remind_at, recurrence, channel_id, user_id FROM reminders "
        "WHERE fired=0 AND remind_at <= ?",
        (now,),
    )
    return [
        {"id": r[0], "task": r[1], "remind_at": r[2], "recurrence": r[3],
         "channel_id": r[4], "user_id": r[5]}
        for r in cur
    ]


def mark_fired(conn: sqlite3.Connection, reminder_id: int) -> None:
    conn.execute("UPDATE reminders SET fired=1 WHERE id=?", (reminder_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# LLM parsing
# ---------------------------------------------------------------------------

def parse_reminder(user_message: str, confidence_threshold: float, timeout: float) -> ReminderParseResponse | None:
    now_pt = datetime.now(PT).isoformat()
    task_desc = (
        f"Parse this reminder request into structured data. "
        f"Current date/time (Pacific Time): {now_pt}. "
        "The user is in Pacific Time. Interpret all times as PT unless explicitly stated otherwise. "
        "Store remind_at as an ISO 8601 string with UTC offset. "
        "If you cannot determine a time, set remind_at to null and confidence to 0."
    )
    result = llm.query(task_desc, f'"{user_message}"', ReminderParseResponse, timeout=timeout)
    return result


# ---------------------------------------------------------------------------
# Discord bot
# ---------------------------------------------------------------------------

def build_bot(config: dict, db_conn: sqlite3.Connection, dry_run: bool):
    import os
    import discord

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)
    prefix = config["bot"]["prefix"]
    conf_threshold = config["llm"]["confidence_threshold"]
    llm_timeout = float(config["llm"]["timeout_seconds"])

    @client.event
    async def on_ready():
        logger.info("Bot connected as %s", client.user)

    @client.event
    async def on_message(message: discord.Message):
        if message.author.bot:
            return
        content = message.content.strip()

        # --- !remind ---
        if content.lower().startswith(f"{prefix}remind "):
            user_text = content[len(f"{prefix}remind "):].strip()
            if not user_text:
                await message.reply("Usage: `!remind <what and when>`")
                return

            parsed = parse_reminder(user_text, conf_threshold, llm_timeout)

            if parsed is None:
                await message.reply(
                    ":warning: I couldn't parse that reminder. Please try again with a clearer time."
                )
                return

            if parsed.remind_at is None or parsed.confidence < conf_threshold:
                await message.reply(
                    f":thinking: I got the task (**{parsed.task}**) but couldn't determine the time. "
                    "Could you be more specific? (e.g. 'tomorrow at 3pm')"
                )
                return

            if dry_run:
                await message.reply(
                    f"[DRY RUN] Would set reminder: **{parsed.task}** at `{parsed.remind_at}` "
                    f"(recurrence: {parsed.recurrence})"
                )
                return

            reminder_id = add_reminder(
                db_conn,
                task=parsed.task,
                remind_at=parsed.remind_at,
                recurrence=parsed.recurrence,
                channel_id=str(message.channel.id),
                user_id=str(message.author.id),
            )
            try:
                dt = datetime.fromisoformat(parsed.remind_at)
                time_str = dt.astimezone(PT).strftime("%b %-d at %-I:%M %p PT")
            except Exception:
                time_str = parsed.remind_at

            rec_str = f" (repeats {parsed.recurrence})" if parsed.recurrence != "none" else ""
            await message.reply(
                f":alarm_clock: Reminder #{reminder_id} set: **{parsed.task}** — {time_str}{rec_str}"
            )

        # --- !reminders ---
        elif content.lower() == f"{prefix}reminders":
            reminders = list_reminders(db_conn, str(message.author.id))
            if not reminders:
                await message.reply("You have no pending reminders.")
                return
            lines = []
            for r in reminders:
                try:
                    dt = datetime.fromisoformat(r["remind_at"])
                    time_str = dt.astimezone(PT).strftime("%b %-d at %-I:%M %p PT")
                except Exception:
                    time_str = r["remind_at"]
                rec = f" ({r['recurrence']})" if r["recurrence"] != "none" else ""
                lines.append(f"`#{r['id']}` **{r['task']}** — {time_str}{rec}")
            await message.reply("**Your reminders:**\n" + "\n".join(lines))

        # --- !cancel <id> ---
        elif content.lower().startswith(f"{prefix}cancel "):
            parts = content.split()
            if len(parts) < 2 or not parts[1].isdigit():
                await message.reply("Usage: `!cancel <id>`")
                return
            reminder_id = int(parts[1])
            if cancel_reminder(db_conn, reminder_id, str(message.author.id)):
                await message.reply(f":white_check_mark: Reminder #{reminder_id} cancelled.")
            else:
                await message.reply(f":x: Reminder #{reminder_id} not found or not yours.")

    return client


async def fire_due_reminders(client, db_conn: sqlite3.Connection, dry_run: bool) -> None:
    """Background task: check for due reminders every 60 seconds."""
    import discord
    await client.wait_until_ready()
    while not client.is_closed():
        due = get_due_reminders(db_conn)
        for reminder in due:
            try:
                channel = client.get_channel(int(reminder["channel_id"]))
                msg = f":alarm_clock: <@{reminder['user_id']}> Reminder: **{reminder['task']}**"
                if dry_run:
                    logger.info("[DRY RUN] Would fire: %s", msg)
                else:
                    if channel:
                        await channel.send(msg)
                    else:
                        logger.warning("Channel %s not found for reminder %s", reminder["channel_id"], reminder["id"])
                mark_fired(db_conn, reminder["id"])
            except Exception as exc:
                logger.error("Failed to fire reminder %s: %s", reminder["id"], exc)
        await asyncio.sleep(60)


def load_config() -> dict:
    config_path = pathlib.Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def main() -> None:
    import os

    parser = argparse.ArgumentParser(description="discord_reminders bot")
    parser.add_argument("--dry-run", action="store_true", help="Log actions, don't actually fire")
    args = parser.parse_args()

    config = load_config()
    db_conn = init_db(config["bot"]["db_path"])

    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        logger.error("DISCORD_BOT_TOKEN not set in .env")
        sys.exit(1)

    client = build_bot(config, db_conn, dry_run=args.dry_run)

    async def setup_hook():
        client.loop.create_task(fire_due_reminders(client, db_conn, dry_run=args.dry_run))

    client.setup_hook = setup_hook
    client.run(token)


if __name__ == "__main__":
    main()
