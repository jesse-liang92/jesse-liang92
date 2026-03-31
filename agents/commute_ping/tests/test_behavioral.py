"""
commute_ping — behavioral tests.
"""

import pathlib
import sys
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from agents.commute_ping.agent import (
    _is_virtual_location,
    _looks_ambiguous,
    calculate_leave_time,
    resolve_location,
)


def test_virtual_meeting_skipped():
    """resolve_location must return None for a Zoom URL."""
    result = resolve_location("https://zoom.us/j/99999", "Weekly Sync")
    assert result is None


def test_virtual_teams_skipped():
    result = resolve_location("https://teams.microsoft.com/l/meeting/...", "All Hands")
    assert result is None


def test_real_address_returned_unchanged():
    """Unambiguous physical address must be returned without calling LLM."""
    addr = "11150 Santa Monica Blvd, Los Angeles, CA 90025"
    result = resolve_location(addr, "Sprint Kickoff")
    assert result == addr


def test_empty_location_returns_none():
    result = resolve_location("", "No Location Meeting")
    assert result is None


def test_leave_time_with_zero_buffer():
    event_start = datetime(2026, 3, 31, 9, 0, tzinfo=timezone.utc)
    leave = calculate_leave_time(event_start, travel_seconds=1800, buffer_minutes=0)
    assert leave == event_start - timedelta(seconds=1800)


def test_leave_time_is_before_event():
    event_start = datetime(2026, 3, 31, 10, 30, tzinfo=timezone.utc)
    leave = calculate_leave_time(event_start, travel_seconds=3600, buffer_minutes=15)
    assert leave < event_start
