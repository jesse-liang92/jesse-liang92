"""
calendar_sync agent — Outlook/M365 → Google Calendar one-way sync.

No LLM usage. Purely deterministic API calls.

Usage:
    python agent.py              # run once then exit (called by systemd timer)
    python agent.py --dry-run    # print actions, no writes
    python agent.py --loop       # run continuously (poll_interval from config)
"""

import argparse
import logging
import os
import pathlib
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import yaml
from dotenv import load_dotenv

# Add project root to path so lib/ is importable
PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from lib.discord_out import post_error, post_status

load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            PROJECT_ROOT / "logs" / "calendar_sync.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
        ),
    ],
)
logger = logging.getLogger("calendar_sync")

# ---------------------------------------------------------------------------
# Lazy imports for heavy API libraries (not loaded in test context)
# ---------------------------------------------------------------------------

def _get_ms_token(tenant_id: str, client_id: str) -> str:
    """Acquire Microsoft Graph access token via MSAL device code + cache."""
    import msal

    token_cache_path = pathlib.Path("~/.config/allyx/ms_token_cache.json").expanduser()
    cache = msal.SerializableTokenCache()
    if token_cache_path.exists():
        cache.deserialize(token_cache_path.read_text())

    app = msal.PublicClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        token_cache=cache,
    )

    scope = ["Calendars.Read"]
    accounts = app.get_accounts()
    result = None
    if accounts:
        result = app.acquire_token_silent(scope, account=accounts[0])

    if not result:
        flow = app.initiate_device_flow(scopes=scope)
        print(flow["message"])  # Instructs user to visit device login URL
        result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        raise RuntimeError(f"MS auth failed: {result.get('error_description')}")

    token_cache_path.parent.mkdir(parents=True, exist_ok=True)
    token_cache_path.write_text(cache.serialize())
    return result["access_token"]


def _fetch_outlook_events(
    token: str, window_days: int
) -> list[dict[str, Any]]:
    """Fetch calendar events from Microsoft Graph for the next window_days."""
    import httpx

    now = datetime.now(timezone.utc)
    end = now + timedelta(days=window_days)
    start_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_str = end.strftime("%Y-%m-%dT%H:%M:%SZ")

    url = (
        "https://graph.microsoft.com/v1.0/me/calendarView"
        f"?startDateTime={start_str}&endDateTime={end_str}"
        "&$select=id,subject,start,end,location,categories,isCancelled,bodyPreview,onlineMeeting"
        "&$top=100"
    )
    headers = {"Authorization": f"Bearer {token}"}
    events: list[dict] = []

    with httpx.Client(timeout=30.0) as client:
        while url:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            events.extend(data.get("value", []))
            url = data.get("@odata.nextLink")

    return events


def _build_gcal_service(credentials_path: str, token_path: str):
    """Build and return an authorized Google Calendar service object."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    scopes = ["https://www.googleapis.com/auth/calendar"]
    creds_path = pathlib.Path(credentials_path).expanduser()
    tok_path = pathlib.Path(token_path).expanduser()

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

    return build("calendar", "v3", credentials=creds)


def _get_gcal_events_by_outlook_id(
    service, calendar_id: str, outlook_ids: list[str]
) -> dict[str, str]:
    """
    Return a mapping of outlook_id → gcal_event_id for known synced events.
    Uses extendedProperties privateProperties to find them.
    """
    result: dict[str, str] = {}
    page_token = None
    while True:
        resp = (
            service.events()
            .list(
                calendarId=calendar_id,
                privateExtendedProperty=[f"outlook_id={oid}" for oid in outlook_ids],
                pageToken=page_token,
                maxResults=250,
            )
            .execute()
        )
        for event in resp.get("items", []):
            oid = (
                event.get("extendedProperties", {})
                .get("private", {})
                .get("outlook_id")
            )
            if oid:
                result[oid] = event["id"]
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return result


def _outlook_to_gcal_body(event: dict[str, Any]) -> dict[str, Any]:
    """Convert a Microsoft Graph event dict to a Google Calendar event body."""
    start_dt = event["start"].get("dateTime") or event["start"].get("date")
    end_dt = event["end"].get("dateTime") or event["end"].get("date")

    body: dict[str, Any] = {
        "summary": event.get("subject", "(No title)"),
        "start": {},
        "end": {},
        "extendedProperties": {
            "private": {"outlook_id": event["id"]}
        },
    }

    if "T" in (start_dt or ""):
        body["start"] = {"dateTime": start_dt, "timeZone": "UTC"}
        body["end"] = {"dateTime": end_dt, "timeZone": "UTC"}
    else:
        body["start"] = {"date": start_dt}
        body["end"] = {"date": end_dt}

    location = event.get("location", {}).get("displayName")
    if location:
        body["location"] = location

    return body


def sync_once(config: dict, dry_run: bool = False) -> dict[str, int]:
    """
    Run a single sync cycle.

    Returns counts: {"created": n, "updated": n, "deleted": n, "skipped": n}
    """
    counts = {"created": 0, "updated": 0, "deleted": 0, "skipped": 0}
    skip_categories = set(config["sync"].get("categories_to_skip", []))

    # --- Auth ---
    token = _get_ms_token(
        os.environ["MS_TENANT_ID"],
        os.environ["MS_CLIENT_ID"],
    )
    gcal = _build_gcal_service(
        config["google"]["credentials_path"],
        config["google"]["token_path"],
    )
    calendar_id = config["google"]["calendar_id"]
    window_days = config["outlook"]["sync_window_days"]

    # --- Fetch Outlook events ---
    outlook_events = _fetch_outlook_events(token, window_days)
    logger.info("Fetched %d Outlook events", len(outlook_events))

    # Filter cancelled and skipped categories
    active_events = []
    for ev in outlook_events:
        if ev.get("isCancelled"):
            continue
        cats = set(ev.get("categories", []))
        if cats & skip_categories:
            counts["skipped"] += 1
            continue
        active_events.append(ev)

    # --- Map existing GCal synced events ---
    outlook_ids = [ev["id"] for ev in active_events]
    existing = _get_gcal_events_by_outlook_id(gcal, calendar_id, outlook_ids)

    # --- Create or update ---
    for ev in active_events:
        oid = ev["id"]
        body = _outlook_to_gcal_body(ev)

        if oid in existing:
            if config["sync"].get("update_existing", True):
                if not dry_run:
                    gcal.events().update(
                        calendarId=calendar_id,
                        eventId=existing[oid],
                        body=body,
                    ).execute()
                counts["updated"] += 1
                logger.debug("Updated GCal event for outlook_id=%s", oid)
        else:
            if not dry_run:
                gcal.events().insert(calendarId=calendar_id, body=body).execute()
            counts["created"] += 1
            logger.debug("Created GCal event for outlook_id=%s", oid)

    # --- Deletions: find GCal events with outlook_id not in active set ---
    active_set = set(outlook_ids)
    # Fetch all synced events (no filter) to find orphans
    all_synced = _get_all_synced_gcal_events(gcal, calendar_id)
    for oid, gcal_id in all_synced.items():
        if oid not in active_set:
            if not dry_run:
                gcal.events().delete(calendarId=calendar_id, eventId=gcal_id).execute()
            counts["deleted"] += 1
            logger.debug("Deleted GCal event for orphaned outlook_id=%s", oid)

    return counts


def _get_all_synced_gcal_events(service, calendar_id: str) -> dict[str, str]:
    """Return all GCal events that have an outlook_id extended property."""
    result: dict[str, str] = {}
    page_token = None
    while True:
        resp = (
            service.events()
            .list(
                calendarId=calendar_id,
                privateExtendedProperty="outlook_id",
                pageToken=page_token,
                maxResults=500,
            )
            .execute()
        )
        for event in resp.get("items", []):
            oid = (
                event.get("extendedProperties", {})
                .get("private", {})
                .get("outlook_id")
            )
            if oid:
                result[oid] = event["id"]
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return result


def load_config() -> dict:
    config_path = pathlib.Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def main() -> None:
    import logging.handlers

    parser = argparse.ArgumentParser(description="calendar_sync agent")
    parser.add_argument("--dry-run", action="store_true", help="Print actions, no writes")
    parser.add_argument("--loop", action="store_true", help="Run continuously on schedule")
    args = parser.parse_args()

    config = load_config()
    interval = config["sync"].get("poll_interval_minutes", 15) * 60

    def run_cycle() -> None:
        try:
            counts = sync_once(config, dry_run=args.dry_run)
            logger.info(
                "Sync complete: +%d created, ~%d updated, -%d deleted, %d skipped",
                counts["created"], counts["updated"], counts["deleted"], counts["skipped"],
            )
            if counts["created"] + counts["deleted"] > 0:
                post_status(
                    "calendar_sync",
                    f"+{counts['created']} created, ~{counts['updated']} updated, "
                    f"-{counts['deleted']} deleted",
                    dry_run=args.dry_run,
                )
        except Exception as exc:
            logger.exception("Sync cycle failed")
            post_error("calendar_sync", str(exc), dry_run=args.dry_run)

    run_cycle()
    if args.loop:
        while True:
            time.sleep(interval)
            run_cycle()


if __name__ == "__main__":
    main()
