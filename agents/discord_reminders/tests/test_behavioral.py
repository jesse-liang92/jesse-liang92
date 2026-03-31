"""
discord_reminders — behavioral tests.

Tests time-parsing quality with known inputs.
"""

import pathlib
import sys
from datetime import datetime

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from lib import llm
from lib.schemas import ReminderParseResponse


def _parse(text: str, now: str = "2026-03-31T09:00:00") -> ReminderParseResponse | None:
    return llm.query(
        f"Parse this reminder request into structured data. Current date/time (UTC): {now}.",
        f'"{text}"',
        ReminderParseResponse,
        timeout=30.0,
    )


def test_tomorrow_3pm():
    """'tomorrow at 3pm' on 2026-03-31 must yield 2026-04-01T15:00."""
    result = _parse("remind me to call the vet tomorrow at 3pm", now="2026-03-31T09:00:00")
    assert result is not None
    assert result.remind_at is not None
    dt = datetime.fromisoformat(result.remind_at)
    assert dt.date().isoformat() == "2026-04-01"
    assert dt.hour == 15


def test_task_extracted_correctly():
    """Task field must capture the actual task, not the time expression."""
    result = _parse("remind me to buy milk tomorrow morning")
    assert result is not None
    assert "milk" in result.task.lower()


def test_daily_recurrence_detected():
    """'every day' must set recurrence to daily."""
    result = _parse("remind me to take my vitamins every day at 8am")
    assert result is not None
    assert result.recurrence == "daily"


def test_weekly_recurrence_detected():
    """'every week' or 'every Monday' must set recurrence to weekly."""
    result = _parse("remind me to review my goals every Monday at 9am")
    assert result is not None
    assert result.recurrence == "weekly"


def test_high_confidence_for_clear_time():
    """Clear, unambiguous time expression must yield confidence >= 0.7."""
    result = _parse("remind me at 2pm today to send the report")
    assert result is not None
    assert result.confidence >= 0.7


def test_recurrence_none_for_one_time():
    """One-time reminders must have recurrence='none'."""
    result = _parse("remind me at 5pm to pick up dry cleaning")
    assert result is not None
    assert result.recurrence == "none"
