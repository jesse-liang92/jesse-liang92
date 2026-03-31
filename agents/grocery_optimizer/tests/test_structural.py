"""
grocery_optimizer — structural tests.

Validates LLM response conforms to GroceryOptimizerResponse schema.
"""

import json
import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from lib import llm
from lib.schemas import GroceryOptimizerResponse
from agents.grocery_optimizer.agent import build_embed_fields, categorize_items

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "sample_items.json"


def _items() -> list[str]:
    return json.loads(FIXTURES.read_text())


def test_response_is_valid_json():
    """Raw LLM output must be parseable as JSON."""
    import os, httpx
    items = _items()
    schema_json = json.dumps(GroceryOptimizerResponse.model_json_schema(), indent=2)
    input_data = "\n".join(f"- {i}" for i in items)
    prompt = (
        "You are a personal automation assistant. Respond ONLY with valid JSON. No markdown.\n\n"
        f"Schema:\n{schema_json}\n\n"
        "Task:\nOrganize this grocery list by store section.\n\n"
        f"Input:\n{input_data}"
    )
    payload = {
        "model": os.getenv("OLLAMA_MODEL", "qwen3.5:9b-q8_0"),
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.1},
    }
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(
            f"{os.getenv('OLLAMA_URL', 'http://localhost:11434')}/api/chat", json=payload
        )
        raw = resp.json()["message"]["content"].strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    json.loads(raw)


def test_response_matches_schema():
    result = categorize_items(_items())
    assert result is not None, "LLM returned None"


def test_no_markdown_wrapping():
    import os, httpx
    items = _items()
    schema_json = json.dumps(GroceryOptimizerResponse.model_json_schema(), indent=2)
    input_data = "\n".join(f"- {i}" for i in items[:5])
    prompt = (
        f"Respond ONLY with valid JSON. No markdown.\nSchema:\n{schema_json}\n"
        f"Task:\nCategorize groceries.\nInput:\n{input_data}"
    )
    payload = {
        "model": os.getenv("OLLAMA_MODEL", "qwen3.5:9b-q8_0"),
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(
            f"{os.getenv('OLLAMA_URL', 'http://localhost:11434')}/api/chat", json=payload
        )
        raw = resp.json()["message"]["content"].strip()
    assert not raw.startswith("```")


def test_sections_field_present():
    result = categorize_items(_items())
    assert result is not None
    assert isinstance(result.sections, list)
    assert len(result.sections) > 0


def test_embed_fields_built_from_result():
    """build_embed_fields must produce at least one field for a valid result."""
    result = categorize_items(_items())
    assert result is not None
    fields = build_embed_fields(result)
    assert len(fields) > 0
