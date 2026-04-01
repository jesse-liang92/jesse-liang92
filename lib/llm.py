"""
Shared Ollama LLM client wrapper.

All agents that need LLM calls should use this module. It handles:
- Structured JSON output with schema enforcement
- One automatic retry with a JSON nudge on parse failure
- Graceful None return (never raises) so agents can handle failures
- DEBUG-level logging of raw prompts and responses for fixture capture
"""

import json
import logging
import os
from typing import Type, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Defaults — overridden by .env at call time
_DEFAULT_OLLAMA_URL = "http://localhost:11434"
_DEFAULT_OLLAMA_MODEL = "qwen3.5:9b"


def _get_ollama_url() -> str:
    return os.getenv("OLLAMA_URL", _DEFAULT_OLLAMA_URL)


def _get_ollama_model() -> str:
    return os.getenv("OLLAMA_MODEL", _DEFAULT_OLLAMA_MODEL)

_PROMPT_TEMPLATE = """\
You are a personal automation assistant. Respond ONLY with valid JSON matching this schema. No markdown, no explanation, no preamble.

Schema:
{schema_json}

Task:
{task_description}

Input:
{input_data}"""

_RETRY_NUDGE = (
    "Your previous response was not valid JSON. "
    "Respond ONLY with valid JSON. No markdown fences, no explanation."
)


def _build_prompt(schema_json: str, task_description: str, input_data: str) -> str:
    return _PROMPT_TEMPLATE.format(
        schema_json=schema_json,
        task_description=task_description,
        input_data=input_data,
    )


def _parse_response(raw: str, model_cls: Type[T]) -> T | None:
    """Strip optional markdown fences and validate against schema."""
    text = raw.strip()
    if text.startswith("```"):
        # Strip ```json ... ``` or ``` ... ```
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        data = json.loads(text)
        return model_cls.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.debug("Parse failure: %s | raw=%r", exc, raw)
        return None


def _chat(messages: list[dict], timeout: float) -> str | None:
    """Send messages to Ollama /api/chat and return the assistant content."""
    payload = {
        "model": _get_ollama_model(),
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.1},
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(f"{_get_ollama_url()}/api/chat", json=payload)
            resp.raise_for_status()
            return resp.json()["message"]["content"]
    except httpx.TimeoutException:
        logger.error("Ollama request timed out after %.0fs", timeout)
        return None
    except Exception as exc:
        logger.error("Ollama request failed: %s", exc)
        return None


def query(
    task_description: str,
    input_data: str,
    response_schema: Type[T],
    timeout: float = 30.0,
) -> T | None:
    """
    Send a structured query to the local LLM.

    Args:
        task_description: Plain English description of what to extract/generate.
        input_data: The raw data string to process.
        response_schema: Pydantic model class defining expected JSON shape.
        timeout: Seconds before giving up on the HTTP call.

    Returns:
        Validated Pydantic instance, or None on failure.
    """
    schema_json = json.dumps(response_schema.model_json_schema(), indent=2)
    prompt = _build_prompt(schema_json, task_description, input_data)

    messages: list[dict] = [{"role": "user", "content": prompt}]
    logger.debug("LLM prompt: %s", prompt)

    raw = _chat(messages, timeout)
    if raw is None:
        return None
    logger.debug("LLM response (attempt 1): %r", raw)

    result = _parse_response(raw, response_schema)
    if result is not None:
        return result

    # Retry once with a JSON nudge
    logger.warning("LLM returned invalid JSON on attempt 1, retrying...")
    messages.append({"role": "assistant", "content": raw})
    messages.append({"role": "user", "content": _RETRY_NUDGE})

    raw2 = _chat(messages, timeout)
    if raw2 is None:
        return None
    logger.debug("LLM response (attempt 2): %r", raw2)

    result = _parse_response(raw2, response_schema)
    if result is None:
        logger.error("LLM failed to return valid JSON after retry. raw=%r", raw2)
    return result
