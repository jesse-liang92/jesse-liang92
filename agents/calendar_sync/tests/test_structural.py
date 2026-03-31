"""
calendar_sync — structural tests.

These tests do NOT require Ollama (no LLM usage in this agent).
They validate deterministic data-transformation logic.
"""

import json
import pathlib
import sys

# Ensure project root is on path
PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from agents.calendar_sync.agent import _outlook_to_gcal_body

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "outlook_events.json"


def _load_fixtures() -> list[dict]:
    with open(FIXTURES) as f:
        return json.load(f)


def test_gcal_body_has_required_fields():
    """Converted GCal body must have summary, start, end, extendedProperties."""
    events = _load_fixtures()
    body = _outlook_to_gcal_body(events[0])
    assert "summary" in body
    assert "start" in body
    assert "end" in body
    assert "extendedProperties" in body


def test_gcal_body_preserves_outlook_id():
    """Outlook event ID must be stored in extendedProperties.private.outlook_id."""
    events = _load_fixtures()
    body = _outlook_to_gcal_body(events[0])
    oid = body["extendedProperties"]["private"]["outlook_id"]
    assert oid == events[0]["id"]


def test_gcal_body_datetime_event():
    """DateTime events must use dateTime fields, not date."""
    events = _load_fixtures()
    body = _outlook_to_gcal_body(events[0])
    assert "dateTime" in body["start"]
    assert "dateTime" in body["end"]


def test_gcal_body_all_day_event():
    """All-day events (date only) must use date field, not dateTime."""
    events = _load_fixtures()
    all_day = events[3]  # "All-Day Event"
    body = _outlook_to_gcal_body(all_day)
    assert "date" in body["start"]
    assert "date" in body["end"]
    assert "dateTime" not in body["start"]


def test_gcal_body_location_included():
    """Location field must be present when Outlook event has a location."""
    events = _load_fixtures()
    body = _outlook_to_gcal_body(events[0])
    assert body.get("location") == "BioSpace LA, 11150 Santa Monica Blvd"


def test_gcal_body_no_location_when_empty():
    """Location must be absent when Outlook event has no location."""
    events = _load_fixtures()
    body = _outlook_to_gcal_body(events[1])  # empty location
    assert "location" not in body or body.get("location") == ""
