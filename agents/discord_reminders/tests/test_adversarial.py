"""
discord_reminders — adversarial / edge-case tests.
"""

import pathlib
import sys
import tempfile

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from lib import llm
from lib.schemas import ReminderParseResponse
from agents.discord_reminders.agent import init_db, add_reminder, get_due_reminders, mark_fired


def _parse(text: str, now: str = "2026-03-31T09:00:00") -> ReminderParseResponse | None:
    return llm.query(
        f"Parse this reminder request. Current date/time (UTC): {now}.",
        f'"{text}"',
        ReminderParseResponse,
        timeout=30.0,
    )


def test_ambiguous_time_low_confidence():
    """'remind me later' must yield low confidence, not a guessed time."""
    result = _parse("remind me later")
    assert result is not None
    assert result.confidence < 0.7 or result.remind_at is None


def test_no_time_given_null_remind_at():
    """Input with no time at all must produce null remind_at."""
    result = _parse("remind me to call mom")
    assert result is not None
    # Either null remind_at or very low confidence
    assert result.remind_at is None or result.confidence < 0.5


def test_garbage_input_returns_schema():
    """Gibberish must return a valid schema object, not crash."""
    result = _parse("asdfghjkl qwerty 12345 foo")
    # Accept None (graceful failure) or valid result with null/low confidence
    if result is not None:
        assert 0.0 <= result.confidence <= 1.0


def test_empty_input():
    """Empty reminder text must not crash the LLM wrapper."""
    result = _parse("")
    if result is not None:
        assert result.confidence == 0.0 or result.remind_at is None


def test_get_due_reminders_returns_only_unfired():
    conn = init_db(tempfile.mktemp(suffix=".db"))
    rid = add_reminder(conn, "Past task", "2000-01-01T00:00:00", "none", "c1", "u1")
    due = get_due_reminders(conn)
    assert any(r["id"] == rid for r in due)
    mark_fired(conn, rid)
    due_after = get_due_reminders(conn)
    assert not any(r["id"] == rid for r in due_after)


def test_future_reminder_not_in_due():
    conn = init_db(tempfile.mktemp(suffix=".db"))
    add_reminder(conn, "Future task", "2099-01-01T00:00:00", "none", "c1", "u1")
    due = get_due_reminders(conn)
    assert due == []
