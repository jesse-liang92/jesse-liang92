"""
commute_ping — adversarial / edge-case tests.
"""

import pathlib
import sys
from datetime import datetime, timezone

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from agents.commute_ping.agent import (
    _is_virtual_location,
    _looks_ambiguous,
    resolve_location,
)
from lib import llm
from lib.schemas import LocationResolutionResponse


def test_gibberish_location_low_confidence():
    """Nonsense location must yield low confidence or None from LLM."""
    result = llm.query(
        "Resolve the meeting location to a physical address in Los Angeles, CA.",
        "Event title: Meeting\nLocation field: asdfghjkl qwerty",
        LocationResolutionResponse,
        timeout=30.0,
    )
    # Accept None or a result with low confidence / null address
    if result is not None:
        assert result.confidence < 0.7 or result.resolved_address is None


def test_empty_location_field():
    result = resolve_location("", "Sprint Review")
    assert result is None


def test_whitespace_only_location():
    result = resolve_location("   ", "Meeting")
    # Should treat as empty — either None or whitespace
    assert not result or not result.strip()


def test_virtual_keyword_in_description():
    """Location with 'virtual' keyword must be caught."""
    assert _is_virtual_location("Virtual meeting room")


def test_very_long_address_not_flagged_as_ambiguous():
    """A long but legitimate address must not be sent to LLM."""
    addr = "Cedars-Sinai Medical Center, 8700 Beverly Blvd, Los Angeles, CA 90048"
    assert not _looks_ambiguous(addr)


def test_single_word_location_is_ambiguous():
    """A single-word location with no digits is ambiguous."""
    assert _looks_ambiguous("Headquarters")
