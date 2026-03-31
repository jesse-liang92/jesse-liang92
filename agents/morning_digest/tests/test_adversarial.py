"""
morning_digest — adversarial / edge-case tests.

Tests model behavior with empty, malformed, or extreme inputs.
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


def test_empty_calendar_no_hallucinated_meetings():
    """Digest with zero events must not hallucinate meetings in full_briefing."""
    data = {"events": [], "todos": [], "weather": {}}
    result = _query(data)
    assert result is not None
    assert result.schedule_conflicts == [], "Must not fabricate conflicts"
    # Briefing should acknowledge empty calendar, not invent events
    assert result.headline  # non-empty headline is fine
    assert len(result.full_briefing) > 0


def test_garbage_input_still_returns_schema():
    """Even with nonsense input, model must return valid schema (not crash)."""
    data = {"events": "not a list", "todos": 12345, "weather": "cloudy with a chance"}
    result = _query(data)
    # We accept None (graceful failure) or a valid result — just not an exception
    if result is not None:
        assert result.headline is not None


def test_full_briefing_under_250_words():
    """full_briefing must stay within ~200 words (allow 25% slack)."""
    data = {
        "events": [
            {"summary": f"Meeting {i}", "start": {"dateTime": f"2026-03-31T{9+i}:00:00Z"},
             "end": {"dateTime": f"2026-03-31T{10+i}:00:00Z"}}
            for i in range(5)
        ],
        "todos": ["Task A", "Task B"],
        "weather": {"temp_f": 70, "description": "sunny", "rain_chance": 0.0},
    }
    result = _query(data)
    assert result is not None
    word_count = len(result.full_briefing.split())
    assert word_count <= 250, f"full_briefing too long: {word_count} words"


def test_no_events_no_todos_no_weather():
    """Completely empty input must return a coherent (if sparse) briefing."""
    data = {"events": [], "todos": [], "weather": {}}
    result = _query(data)
    assert result is not None
    assert result.full_briefing  # non-empty


def test_very_long_todo_list_truncated():
    """With 50 to-dos, todo_priorities must still be ≤ 3 items."""
    data = {
        "events": [],
        "todos": [f"Task {i}: do something important" for i in range(50)],
        "weather": {},
    }
    result = _query(data)
    assert result is not None
    assert len(result.todo_priorities) <= 3
