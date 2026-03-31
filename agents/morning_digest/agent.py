"""
morning_digest agent — Daily briefing: calendar + tasks + weather → Discord + Obsidian.

LLM usage: YES — summarization and conflict detection.

Usage:
    python agent.py              # run once (called by systemd timer at 06:00)
    python agent.py --dry-run    # print output, no Discord post, no Obsidian write
"""

import argparse
import logging
import logging.handlers
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from lib import llm
from lib.discord_out import post_error, send_embed
from lib.schemas import MorningDigestResponse

load_dotenv(PROJECT_ROOT / ".env")

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            LOG_DIR / "morning_digest.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
        ),
    ],
)
logger = logging.getLogger("morning_digest")

AGENT = "morning_digest"

# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_gcal_events(config: dict) -> list[dict[str, Any]]:
    """Fetch today's Google Calendar events."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    scopes = ["https://www.googleapis.com/auth/calendar.readonly"]
    creds_path = pathlib.Path(config["google"]["credentials_path"]).expanduser()
    tok_path = pathlib.Path(config["google"]["token_path"]).expanduser()

    creds = None
    if tok_path.exists():
        creds = Credentials.from_authorized_user_file(str(tok_path), scopes)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), scopes)
            creds = flow.run_local_server(port=0)
        tok_path.parent.mkdir(parents=True, exist_ok=True)
        tok_path.write_text(creds.to_json())

    service = build("calendar", "v3", credentials=creds)
    now = datetime.now(timezone.utc)
    end = now + timedelta(hours=config["google"]["lookahead_hours"])

    result = (
        service.events()
        .list(
            calendarId=config["google"]["calendar_id"],
            timeMin=now.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=20,
        )
        .execute()
    )
    return result.get("items", [])


def fetch_todo_items(config: dict) -> list[str]:
    """Fetch to-do items from Microsoft To Do via Graph API."""
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
        raise RuntimeError("Microsoft auth needed — run calendar_sync first to authenticate")

    token = result["access_token"]
    list_name = config["microsoft"]["todo_list_name"]

    with httpx.Client(timeout=15.0) as client:
        headers = {"Authorization": f"Bearer {token}"}
        # Find the list ID
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
            "?$filter=status ne 'completed'&$top=20",
            headers=headers,
        )
        tasks_resp.raise_for_status()
        return [t["title"] for t in tasks_resp.json().get("value", [])]


def fetch_weather(config: dict) -> dict[str, Any]:
    """Fetch current weather and today's forecast from OpenWeatherMap."""
    import httpx

    api_key = os.environ["OPENWEATHERMAP_API_KEY"]
    location = config["weather"]["location"]
    units = config["weather"]["units"]

    with httpx.Client(timeout=10.0) as client:
        resp = client.get(
            "https://api.openweathermap.org/data/2.5/forecast",
            params={"q": location, "appid": api_key, "units": units, "cnt": 8},
        )
        resp.raise_for_status()
        data = resp.json()

    forecasts = data.get("list", [])
    if not forecasts:
        return {}

    current = forecasts[0]
    return {
        "temp_f": round(current["main"]["temp"]),
        "feels_like_f": round(current["main"]["feels_like"]),
        "description": current["weather"][0]["description"],
        "rain_chance": max(
            (f.get("pop", 0) for f in forecasts), default=0
        ),
        "wind_mph": round(current.get("wind", {}).get("speed", 0)),
    }


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _format_events_for_prompt(events: list[dict]) -> str:
    lines = []
    for ev in events:
        start = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date", "")
        end = ev.get("end", {}).get("dateTime") or ev.get("end", {}).get("date", "")
        title = ev.get("summary", "(No title)")
        location = ev.get("location", "")
        loc_str = f" @ {location}" if location else ""
        lines.append(f"- {start} → {end}: {title}{loc_str}")
    return "\n".join(lines) if lines else "No events today."


def _format_events_for_obsidian(events: list[dict]) -> str:
    lines = []
    for ev in events:
        start_raw = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date", "")
        try:
            dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
            time_str = dt.strftime("%-I:%M %p")
        except Exception:
            time_str = start_raw
        title = ev.get("summary", "(No title)")
        location = ev.get("location", "")
        loc_str = f" — {location}" if location else ""
        lines.append(f"- {time_str}: **{title}**{loc_str}")
    return "\n".join(lines) if lines else "_No events._"


def _format_todos_for_obsidian(todos: list[str]) -> str:
    return "\n".join(f"- [ ] {t}" for t in todos) if todos else "_No pending tasks._"


# ---------------------------------------------------------------------------
# Obsidian writer
# ---------------------------------------------------------------------------

def write_obsidian_note(
    config: dict,
    date_str: str,
    briefing: MorningDigestResponse,
    events: list[dict],
    todos: list[str],
    dry_run: bool = False,
) -> None:
    vault = pathlib.Path(config["obsidian"]["vault_path"]).expanduser()
    notes_dir = vault / config["obsidian"]["daily_notes_dir"]
    note_path = notes_dir / f"{date_str}.md"

    content = f"""# {date_str}

## Morning Briefing
{briefing.full_briefing}

## Schedule
{_format_events_for_obsidian(events)}

## To-Do
{_format_todos_for_obsidian(todos)}

## Notes
<!-- space for manual notes throughout the day -->
"""
    if dry_run:
        logger.info("[DRY RUN] Would write Obsidian note to %s:\n%s", note_path, content)
        return

    notes_dir.mkdir(parents=True, exist_ok=True)
    note_path.write_text(content)
    logger.info("Wrote Obsidian note: %s", note_path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(config: dict, dry_run: bool = False) -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    now_iso = datetime.now().isoformat()

    # --- Fetch data ---
    events: list[dict] = []
    todos: list[str] = []
    weather: dict = {}

    try:
        events = fetch_gcal_events(config)
        logger.info("Fetched %d calendar events", len(events))
    except Exception as exc:
        logger.error("GCal fetch failed: %s", exc)
        post_error(AGENT, f"GCal fetch failed: {exc}", dry_run=dry_run)

    try:
        todos = fetch_todo_items(config)
        logger.info("Fetched %d to-do items", len(todos))
    except Exception as exc:
        logger.warning("To Do fetch failed (non-fatal): %s", exc)

    try:
        weather = fetch_weather(config)
        logger.info("Fetched weather: %s", weather)
    except Exception as exc:
        logger.warning("Weather fetch failed (non-fatal): %s", exc)

    # --- Build LLM input ---
    input_data = (
        f"Current datetime: {now_iso}\n\n"
        f"Calendar events:\n{_format_events_for_prompt(events)}\n\n"
        f"To-do items:\n" + ("\n".join(f"- {t}" for t in todos) or "None") + "\n\n"
        f"Weather: {weather if weather else 'unavailable'}"
    )

    task_description = (
        "You are Jesse's personal briefing assistant. Given today's calendar events, "
        "to-do items, and weather forecast, generate a morning briefing. "
        "Rules: lead with the most important/time-sensitive item; flag scheduling conflicts "
        "or back-to-back meetings with no buffer; note weather only if actionable; "
        "keep full_briefing under 200 words; if meetings with external parties, note prep needed."
    )

    result = llm.query(task_description, input_data, MorningDigestResponse, timeout=45.0)

    if result is None:
        logger.error("LLM returned None — posting fallback")
        post_error(AGENT, "LLM failed to generate morning digest", dry_run=dry_run)
        return

    # --- Discord embed ---
    fields = []
    if result.schedule_conflicts:
        fields.append({
            "name": ":warning: Conflicts",
            "value": "\n".join(result.schedule_conflicts),
            "inline": False,
        })
    if result.time_sensitive:
        fields.append({
            "name": ":zap: Time-Sensitive",
            "value": "\n".join(f"• {i}" for i in result.time_sensitive),
            "inline": False,
        })
    if result.todo_priorities:
        fields.append({
            "name": ":white_check_mark: Top Tasks",
            "value": "\n".join(f"• {i}" for i in result.todo_priorities),
            "inline": False,
        })
    if result.weather_note:
        fields.append({"name": ":cloud: Weather", "value": result.weather_note, "inline": False})

    send_embed(
        channel="calendar",
        title=f":sunrise: Morning Briefing — {today}",
        description=result.full_briefing,
        fields=fields,
        color=0xF4A261,
        dry_run=dry_run,
    )

    # --- Obsidian ---
    try:
        write_obsidian_note(config, today, result, events, todos, dry_run=dry_run)
    except Exception as exc:
        logger.error("Obsidian write failed: %s", exc)
        post_error(AGENT, f"Obsidian write failed: {exc}", dry_run=dry_run)


def load_config() -> dict:
    config_path = pathlib.Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def main() -> None:
    parser = argparse.ArgumentParser(description="morning_digest agent")
    parser.add_argument("--dry-run", action="store_true", help="Print output, no posts or writes")
    args = parser.parse_args()

    config = load_config()
    try:
        run(config, dry_run=args.dry_run)
    except Exception as exc:
        logger.exception("morning_digest failed")
        post_error(AGENT, str(exc), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
