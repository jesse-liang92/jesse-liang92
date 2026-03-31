"""
morning_digest — behavioral tests.

Tests reasoning quality with known inputs. Requires Ollama running locally.
"""

import json
import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from lib import llm
from lib.schemas import MorningDigestResponse

TASK = (
    "Generate a morning briefing for Jesse. Lead with the most important item. "
    "Flag scheduling conflicts. Note weather only if actionable. Keep under 200 words."
)


def _query(data: dict) -> MorningDigestResponse | None:
    return llm.query(TASK, json.dumps(data), MorningDigestResponse, timeout=60.0)


def test_meeting_conflict_detection():
    """Back-to-back meetings with no buffer must be flagged in schedule_conflicts."""
    data = {
        "events": [
            {
                "summary": "Standup",
                "start": {"dateTime": "2026-03-31T09:00:00Z"},
                "end":   {"dateTime": "2026-03-31T09:30:00Z"},
            },
            {
                "summary": "Q2 Planning",
                "start": {"dateTime": "2026-03-31T09:30:00Z"},
                "end":   {"dateTime": "2026-03-31T11:00:00Z"},
            },
        ],
        "todos": [],
        "weather": {},
    }
    result = _query(data)
    assert result is not None
    assert len(result.schedule_conflicts) > 0, "Expected conflict to be flagged"


def test_weather_note_for_rain():
    """High rain probability must produce an actionable weather_note."""
    data = {
        "events": [],
        "todos": [],
        "weather": {"temp_f": 58, "description": "heavy rain", "rain_chance": 0.9, "wind_mph": 15},
    }
    result = _query(data)
    assert result is not None
    assert result.weather_note is not None, "Expected weather note for heavy rain"


def test_no_weather_note_for_clear_day():
    """Clear, mild weather must not produce a weather_note (or note null/empty)."""
    data = {
        "events": [],
        "todos": [],
        "weather": {"temp_f": 72, "description": "clear sky", "rain_chance": 0.0, "wind_mph": 5},
    }
    result = _query(data)
    assert result is not None
    # weather_note should be null or mention nothing critical
    if result.weather_note:
        text = result.weather_note.lower()
        assert "umbrella" not in text and "rain" not in text


def test_external_meeting_flags_prep():
    """Meeting with 'Investors' in title must appear in prep_needed."""
    data = {
        "events": [
            {
                "summary": "Q2 Planning with Investors",
                "start": {"dateTime": "2026-03-31T10:00:00Z"},
                "end":   {"dateTime": "2026-03-31T11:30:00Z"},
                "location": "BioSpace LA",
            }
        ],
        "todos": [],
        "weather": {},
    }
    result = _query(data)
    assert result is not None
    assert len(result.prep_needed) > 0, "Expected prep flag for external investor meeting"


def test_todo_priorities_capped_at_three():
    """todo_priorities must contain at most 3 items."""
    data = {
        "events": [],
        "todos": [f"Task {i}" for i in range(10)],
        "weather": {},
    }
    result = _query(data)
    assert result is not None
    assert len(result.todo_priorities) <= 3
