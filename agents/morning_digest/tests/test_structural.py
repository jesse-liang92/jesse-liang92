"""
morning_digest — structural tests.

These tests call the real Ollama model and validate that the response
conforms to MorningDigestResponse schema. Requires Ollama running locally.
"""

import json
import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from lib import llm
from lib.schemas import MorningDigestResponse

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "sample_input.json"

TASK = (
    "Generate a morning briefing for Jesse. Lead with the most important item. "
    "Flag scheduling conflicts. Note weather only if actionable. Keep under 200 words."
)


def _get_raw_response() -> str:
    """Helper: get raw LLM text without schema validation (for structural checks)."""
    import httpx, os
    data = json.loads(FIXTURES.read_text())
    schema_json = json.dumps(MorningDigestResponse.model_json_schema(), indent=2)
    prompt = (
        "You are a personal automation assistant. Respond ONLY with valid JSON matching "
        f"this schema. No markdown, no explanation, no preamble.\n\nSchema:\n{schema_json}"
        f"\n\nTask:\n{TASK}\n\nInput:\n{json.dumps(data)}"
    )
    payload = {
        "model": os.getenv("OLLAMA_MODEL", "qwen3.5:9b-q8_0"),
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.1},
    }
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(
            f"{os.getenv('OLLAMA_URL', 'http://localhost:11434')}/api/chat",
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]


def test_response_is_valid_json():
    """Raw LLM output must be parseable as JSON."""
    raw = _get_raw_response()
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    json.loads(text)  # raises if not valid


def test_response_matches_schema():
    """Parsed JSON must validate against MorningDigestResponse."""
    data = json.loads(FIXTURES.read_text())
    result = llm.query(TASK, json.dumps(data), MorningDigestResponse, timeout=60.0)
    assert result is not None, "LLM returned None — schema validation failed"


def test_no_markdown_wrapping():
    """Response must not be wrapped in ```json ``` fences."""
    raw = _get_raw_response()
    assert not raw.strip().startswith("```"), f"Response has markdown fences: {raw[:100]}"


def test_all_required_fields_present():
    """Every required field must be present: headline, full_briefing."""
    data = json.loads(FIXTURES.read_text())
    result = llm.query(TASK, json.dumps(data), MorningDigestResponse, timeout=60.0)
    assert result is not None
    assert result.headline
    assert result.full_briefing
