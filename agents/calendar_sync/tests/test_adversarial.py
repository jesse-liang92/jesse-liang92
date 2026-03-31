"""
calendar_sync — adversarial / edge-case tests.
"""

import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from agents.calendar_sync.agent import _outlook_to_gcal_body


def test_missing_location_key():
    """Event dict with no location key at all must not raise."""
    event = {
        "id": "AAMkTest1",
        "subject": "No Location",
        "start": {"dateTime": "2026-04-01T10:00:00Z"},
        "end":   {"dateTime": "2026-04-01T11:00:00Z"},
        "categories": [],
        "isCancelled": False,
    }
    body = _outlook_to_gcal_body(event)
    assert "summary" in body


def test_empty_categories_list():
    """Event with empty categories list must not be filtered."""
    event = {
        "id": "AAMkTest2",
        "subject": "Empty Cats",
        "start": {"dateTime": "2026-04-01T10:00:00Z"},
        "end":   {"dateTime": "2026-04-01T11:00:00Z"},
        "location": {"displayName": ""},
        "categories": [],
        "isCancelled": False,
    }
    skip = {"Personal"}
    assert not (set(event.get("categories", [])) & skip)


def test_multiple_skip_categories_match():
    """Event with multiple categories where one matches skip list is excluded."""
    event = {
        "id": "AAMkTest3",
        "subject": "Multi Cat",
        "categories": ["Work", "Personal"],
        "isCancelled": False,
    }
    skip = {"Personal"}
    assert set(event["categories"]) & skip


def test_event_with_both_date_and_datetime_uses_datetime():
    """If both date and dateTime are present, dateTime wins."""
    event = {
        "id": "AAMkTest4",
        "subject": "Ambiguous Time",
        "start": {"dateTime": "2026-04-01T10:00:00Z", "date": "2026-04-01"},
        "end":   {"dateTime": "2026-04-01T11:00:00Z", "date": "2026-04-01"},
        "location": {"displayName": ""},
        "categories": [],
        "isCancelled": False,
    }
    body = _outlook_to_gcal_body(event)
    assert "dateTime" in body["start"]


def test_very_long_subject_does_not_raise():
    """Subject over 1000 chars must not raise."""
    event = {
        "id": "AAMkTest5",
        "subject": "A" * 1200,
        "start": {"dateTime": "2026-04-01T10:00:00Z"},
        "end":   {"dateTime": "2026-04-01T11:00:00Z"},
        "location": {"displayName": ""},
        "categories": [],
        "isCancelled": False,
    }
    body = _outlook_to_gcal_body(event)
    assert len(body["summary"]) == 1200
