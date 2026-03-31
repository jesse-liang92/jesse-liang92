"""
commute_ping — structural tests.

Validates deterministic logic (no LLM needed for most tests).
LLM structural test covers LocationResolutionResponse schema.
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
)
from lib import llm
from lib.schemas import LocationResolutionResponse


def test_virtual_zoom_url_detected():
    assert _is_virtual_location("https://zoom.us/j/12345")


def test_virtual_teams_url_detected():
    assert _is_virtual_location("https://teams.microsoft.com/l/meetup-join/...")


def test_virtual_keyword_detected():
    assert _is_virtual_location("Zoom call")


def test_physical_address_not_virtual():
    assert not _is_virtual_location("11150 Santa Monica Blvd, Los Angeles, CA")


def test_ambiguous_short_name():
    assert _looks_ambiguous("John's office")


def test_street_address_not_ambiguous():
    assert not _looks_ambiguous("11150 Santa Monica Blvd, Los Angeles")


def test_calculate_leave_time_basic():
    """leave_time = event_start - travel - buffer."""
    event_start = datetime(2026, 3, 31, 10, 0, tzinfo=timezone.utc)
    leave = calculate_leave_time(event_start, travel_seconds=2040, buffer_minutes=10)
    # 2040s = 34 min; 34 + 10 = 44 min before 10:00 = 09:16
    assert leave.hour == 9
    assert leave.minute == 16


def test_location_resolution_schema():
    """LLM must return valid LocationResolutionResponse for a simple ambiguous input."""
    result = llm.query(
        "Resolve the meeting location to a physical address in Los Angeles, CA.",
        "Event title: Doctor Appointment\nLocation field: Cedars-Sinai",
        LocationResolutionResponse,
        timeout=30.0,
    )
    assert result is not None
    assert isinstance(result.confidence, float)
    assert 0.0 <= result.confidence <= 1.0
