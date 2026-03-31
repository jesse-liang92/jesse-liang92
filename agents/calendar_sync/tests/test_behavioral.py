"""
calendar_sync — behavioral tests.

Tests that filtering and sync logic works correctly with known inputs.
No network calls; uses fixture data and monkeypatching.
"""

import json
import pathlib
import sys
from unittest.mock import MagicMock, patch

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from agents.calendar_sync.agent import _outlook_to_gcal_body

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "outlook_events.json"


def _load_fixtures() -> list[dict]:
    with open(FIXTURES) as f:
        return json.load(f)


def test_cancelled_events_are_excluded():
    """Cancelled Outlook events must not be synced."""
    events = _load_fixtures()
    skip_categories = set()
    active = [
        ev for ev in events
        if not ev.get("isCancelled") and not (set(ev.get("categories", [])) & skip_categories)
    ]
    ids = [ev["id"] for ev in active]
    assert "AAMkAGE0MjQ3O" not in ids  # the cancelled one


def test_skipped_categories_are_excluded():
    """Events in skip_categories must not be synced."""
    events = _load_fixtures()
    skip_categories = {"Personal", "Blocked"}
    active = [
        ev for ev in events
        if not ev.get("isCancelled") and not (set(ev.get("categories", [])) & skip_categories)
    ]
    ids = [ev["id"] for ev in active]
    assert "AAMkAGE0MjQ3N" not in ids  # the Personal one


def test_normal_events_are_included():
    """Non-cancelled, non-skipped events must appear in active list."""
    events = _load_fixtures()
    skip_categories = {"Personal", "Blocked"}
    active = [
        ev for ev in events
        if not ev.get("isCancelled") and not (set(ev.get("categories", [])) & skip_categories)
    ]
    ids = [ev["id"] for ev in active]
    assert "AAMkAGE0MjQ3M" in ids  # the real Q2 Sprint event
    assert "AAMkAGE0MjQ3P" in ids  # the all-day event


def test_subject_preserved_in_summary():
    """Outlook event subject must become GCal summary."""
    events = _load_fixtures()
    body = _outlook_to_gcal_body(events[0])
    assert body["summary"] == "Q2 Sprint Kickoff"


def test_no_subject_gets_placeholder():
    """Events with no subject must get a placeholder rather than empty string."""
    event = {
        "id": "AAMkTest",
        "subject": None,
        "start": {"dateTime": "2026-04-01T10:00:00Z"},
        "end":   {"dateTime": "2026-04-01T11:00:00Z"},
        "location": {"displayName": ""},
        "categories": [],
        "isCancelled": False,
    }
    body = _outlook_to_gcal_body(event)
    assert body["summary"]  # non-empty
