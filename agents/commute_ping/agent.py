"""
commute_ping agent — Calculate commute time and post a "leave by" alert to Discord.

LLM usage: optional, only when event location is ambiguous (e.g. "John's office").

Usage:
    python agent.py              # run once (called by systemd timer at 05:30)
    python agent.py --dry-run    # print output, no Discord post
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
from lib.discord_out import post_error, send_message
from lib.schemas import LocationResolutionResponse

load_dotenv(PROJECT_ROOT / ".env")

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            LOG_DIR / "commute_ping.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
        ),
    ],
)
logger = logging.getLogger("commute_ping")

AGENT = "commute_ping"

VIRTUAL_KEYWORDS = {
    "zoom.us", "teams.microsoft.com", "meet.google.com",
    "zoom", "teams", "webex", "bluejeans", "gotomeeting", "virtual", "remote",
}


def _is_virtual_location(location: str) -> bool:
    """Return True if location string indicates a virtual meeting."""
    loc_lower = location.lower()
    return any(kw in loc_lower for kw in VIRTUAL_KEYWORDS)


def _looks_ambiguous(location: str) -> bool:
    """
    Return True if the location string is a non-standard address that might
    benefit from LLM resolution (e.g. "John's office", "the usual spot").
    """
    # If it has digits (street number) or a known city/state pattern, it's probably real
    has_digits = any(c.isdigit() for c in location)
    has_comma = "," in location
    return not has_digits and not has_comma and len(location.split()) <= 4


def resolve_location(raw_location: str, event_title: str) -> str | None:
    """
    Return a physical address for navigation, or None if unresolvable.
    Uses LLM only for ambiguous locations.
    """
    if not raw_location:
        return None
    if _is_virtual_location(raw_location):
        logger.info("Location is virtual, skipping: %s", raw_location)
        return None

    if not _looks_ambiguous(raw_location):
        return raw_location

    # Ambiguous — ask the LLM
    logger.info("Ambiguous location '%s', querying LLM", raw_location)
    task = (
        "Resolve the meeting location to a physical street address in Los Angeles, CA. "
        "If it's clearly a virtual meeting (Zoom, Teams, etc.), set is_virtual=true. "
        "If you cannot determine a real address, set resolved_address to null."
    )
    input_data = f"Event title: {event_title}\nLocation field: {raw_location}"
    result = llm.query(task, input_data, LocationResolutionResponse, timeout=20.0)

    if result is None:
        logger.warning("LLM failed to resolve location — using raw value")
        return raw_location
    if result.is_virtual:
        return None
    if result.confidence < 0.6:
        logger.warning("Low-confidence location resolution (%.2f) — skipping", result.confidence)
        return None
    return result.resolved_address


def fetch_first_morning_event(config: dict) -> dict[str, Any] | None:
    """
    Fetch the first calendar event of the day that:
    - starts before 11:00 AM local time
    - has a non-virtual location
    """
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
    end_of_morning = now.replace(hour=18, minute=0, second=0, microsecond=0)  # 11 AM PT = 6 PM UTC

    result = (
        service.events()
        .list(
            calendarId=config["google"]["calendar_id"],
            timeMin=now.isoformat(),
            timeMax=end_of_morning.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=10,
        )
        .execute()
    )

    for event in result.get("items", []):
        location = event.get("location", "") or ""
        description = event.get("description", "") or ""
        # Skip virtual events (check both location and description for links)
        if _is_virtual_location(location) or _is_virtual_location(description):
            continue
        if location:
            return event

    return None


def get_directions(
    origin: str,
    destination: str,
    departure_time: datetime,
    traffic_model: str,
    api_key: str,
) -> dict[str, Any] | None:
    """Call Google Routes API and return duration info."""
    import httpx

    # Map legacy traffic_model names to Routes API equivalents
    routing_pref = "TRAFFIC_AWARE_OPTIMAL" if traffic_model == "pessimistic" else "TRAFFIC_AWARE"

    request_body: dict[str, Any] = {
        "origin": {"address": origin},
        "destination": {"address": destination},
        "travelMode": "DRIVE",
        "routingPreference": routing_pref,
    }
    # Routes API requires departureTime to be in the future; omit for "now"
    if departure_time > datetime.now(timezone.utc):
        request_body["departureTime"] = departure_time.strftime("%Y-%m-%dT%H:%M:%SZ")

    with httpx.Client(timeout=15.0) as client:
        resp = client.post(
            "https://routes.googleapis.com/directions/v2:computeRoutes",
            headers={
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": "routes.duration,routes.distanceMeters,routes.legs.duration",
            },
            json=request_body,
        )
        resp.raise_for_status()
        data = resp.json()

    if "routes" not in data or not data["routes"]:
        logger.error("Routes API returned no routes: %s", data)
        return None

    route = data["routes"][0]
    duration_secs = int(route["duration"].rstrip("s"))
    distance_miles = route["distanceMeters"] / 1609.34

    # Format human-readable strings
    hours, mins = divmod(duration_secs // 60, 60)
    duration_text = f"{hours} hr {mins} min" if hours else f"{mins} min"
    distance_text = f"{distance_miles:.1f} mi"

    return {
        "duration_seconds": duration_secs,
        "duration_text": duration_text,
        "distance_text": distance_text,
    }


def calculate_leave_time(
    event_start: datetime,
    travel_seconds: int,
    buffer_minutes: int,
) -> datetime:
    return event_start - timedelta(seconds=travel_seconds) - timedelta(minutes=buffer_minutes)


def run(config: dict, dry_run: bool = False) -> None:
    now = datetime.now()
    earliest = datetime.strptime(config["commute"]["earliest_ping_time"], "%H:%M").replace(
        year=now.year, month=now.month, day=now.day
    )
    latest = datetime.strptime(config["commute"]["latest_ping_time"], "%H:%M").replace(
        year=now.year, month=now.month, day=now.day
    )

    if now < earliest or now > latest:
        logger.info("Outside ping window (%s–%s), skipping", earliest.time(), latest.time())
        return

    # Fetch first morning event
    try:
        event = fetch_first_morning_event(config)
    except Exception as exc:
        logger.error("GCal fetch failed: %s", exc)
        post_error(AGENT, f"GCal fetch failed: {exc}", dry_run=dry_run)
        return

    if event is None:
        logger.info("No in-person morning events found, skipping commute ping")
        return

    title = event.get("summary", "Meeting")
    raw_location = event.get("location", "")
    start_raw = event["start"].get("dateTime") or event["start"].get("date")

    try:
        event_start = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
    except Exception as exc:
        logger.error("Could not parse event start time: %s", exc)
        return

    # Resolve location
    destination = resolve_location(raw_location, title)
    if destination is None:
        destination = config.get("default_destination", "")
    if not destination:
        logger.info("No destination resolved, skipping")
        return

    # Get directions
    try:
        directions = get_directions(
            origin=os.environ["HOME_ADDRESS"],
            destination=destination,
            departure_time=datetime.now(timezone.utc),
            traffic_model=config["commute"]["traffic_model"],
            api_key=os.environ["GOOGLE_MAPS_API_KEY"],
        )
    except Exception as exc:
        logger.error("Directions API failed: %s", exc)
        post_error(AGENT, f"Directions failed: {exc}", dry_run=dry_run)
        return

    if directions is None:
        return

    leave_time = calculate_leave_time(
        event_start=event_start,
        travel_seconds=directions["duration_seconds"],
        buffer_minutes=config["commute"]["buffer_minutes"],
    )
    leave_str = leave_time.strftime("%-I:%M %p")
    duration_text = directions["duration_text"]

    message = (
        f":car: Leave by **{leave_str}** for _{title}_ at {destination} "
        f"(est. {duration_text} with traffic)"
    )
    logger.info(message)
    send_message("commute", message, dry_run=dry_run)


def load_config() -> dict:
    config_path = pathlib.Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def main() -> None:
    parser = argparse.ArgumentParser(description="commute_ping agent")
    parser.add_argument("--dry-run", action="store_true", help="Print output, no Discord post")
    args = parser.parse_args()

    config = load_config()
    try:
        run(config, dry_run=args.dry_run)
    except Exception as exc:
        logger.exception("commute_ping failed")
        post_error(AGENT, str(exc), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
