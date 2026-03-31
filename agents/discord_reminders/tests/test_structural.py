"""
discord_reminders — structural tests.

Validates LLM output schema and SQLite DB logic.
"""

import pathlib
import sqlite3
import sys
import tempfile

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from lib import llm
from lib.schemas import ReminderParseResponse
from agents.discord_reminders.agent import init_db, add_reminder, list_reminders, cancel_reminder


def test_response_is_valid_json():
    """Raw LLM output for a reminder must be parseable as JSON."""
    import json, os, httpx
    schema_json = json.dumps(ReminderParseResponse.model_json_schema(), indent=2)
    prompt = (
        "You are a personal automation assistant. Respond ONLY with valid JSON matching "
        f"this schema. No markdown, no explanation.\n\nSchema:\n{schema_json}\n\n"
        "Task:\nParse this reminder request. Current datetime: 2026-03-31T09:00:00.\n\n"
        'Input:\n"remind me to call the vet tomorrow at 3pm"'
    )
    payload = {
        "model": os.getenv("OLLAMA_MODEL", "qwen3.5:9b-q8_0"),
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.1},
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            f"{os.getenv('OLLAMA_URL', 'http://localhost:11434')}/api/chat",
            json=payload,
        )
        resp.raise_for_status()
        raw = resp.json()["message"]["content"].strip()

    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    json.loads(raw)  # raises if not valid JSON


def test_response_matches_schema():
    """Parsed LLM response must validate as ReminderParseResponse."""
    result = llm.query(
        "Parse this reminder. Current datetime: 2026-03-31T09:00:00.",
        '"remind me to buy milk tomorrow morning"',
        ReminderParseResponse,
        timeout=30.0,
    )
    assert result is not None


def test_no_markdown_wrapping():
    """Response must not start with ```."""
    import json, os, httpx
    schema_json = json.dumps(ReminderParseResponse.model_json_schema(), indent=2)
    prompt = (
        "Respond ONLY with valid JSON. No markdown.\n\n"
        f"Schema:\n{schema_json}\n\nTask:\nParse: 'call mom at 5pm'\nInput:\n'call mom at 5pm'"
    )
    payload = {
        "model": os.getenv("OLLAMA_MODEL", "qwen3.5:9b-q8_0"),
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            f"{os.getenv('OLLAMA_URL', 'http://localhost:11434')}/api/chat", json=payload
        )
        raw = resp.json()["message"]["content"].strip()
    assert not raw.startswith("```")


def test_all_required_fields_present():
    """task, remind_at, recurrence, confidence must all be present."""
    result = llm.query(
        "Parse this reminder. Current datetime: 2026-03-31T09:00:00.",
        '"ping me at noon to eat lunch"',
        ReminderParseResponse,
        timeout=30.0,
    )
    assert result is not None
    assert result.task
    assert result.recurrence in ("none", "daily", "weekly", "monthly")
    assert 0.0 <= result.confidence <= 1.0


# ---------------------------------------------------------------------------
# DB logic tests (no LLM)
# ---------------------------------------------------------------------------

def _tmp_db() -> sqlite3.Connection:
    tmp = tempfile.mktemp(suffix=".db")
    return init_db(tmp)


def test_db_init_creates_table():
    conn = _tmp_db()
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='reminders'")
    assert cur.fetchone() is not None


def test_add_and_list_reminder():
    conn = _tmp_db()
    rid = add_reminder(conn, "Call vet", "2026-04-01T15:00:00", "none", "123", "user1")
    reminders = list_reminders(conn, "user1")
    assert len(reminders) == 1
    assert reminders[0]["id"] == rid
    assert reminders[0]["task"] == "Call vet"


def test_cancel_reminder():
    conn = _tmp_db()
    rid = add_reminder(conn, "Buy milk", "2026-04-01T08:00:00", "none", "123", "user1")
    assert cancel_reminder(conn, rid, "user1")
    assert list_reminders(conn, "user1") == []


def test_cancel_wrong_user_fails():
    conn = _tmp_db()
    rid = add_reminder(conn, "Task", "2026-04-01T10:00:00", "none", "123", "user1")
    assert not cancel_reminder(conn, rid, "user2")  # different user
