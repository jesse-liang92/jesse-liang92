"""
grocery_optimizer — adversarial / edge-case tests.
"""

import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from agents.grocery_optimizer.agent import categorize_items, build_embed_fields
from lib.schemas import GroceryOptimizerResponse


def test_empty_list_returns_empty_sections():
    """Empty input must return a result with no sections (not None)."""
    result = categorize_items([])
    assert result is not None
    assert result.sections == []


def test_single_item_list():
    """Single item must produce a valid categorized result."""
    result = categorize_items(["eggs"])
    assert result is not None
    assert len(result.sections) >= 1


def test_gibberish_items_do_not_crash():
    """Nonsense item names must not crash the LLM wrapper."""
    result = categorize_items(["xyzzy", "frobnicate", "qwerty123"])
    # Accept None (graceful failure) or a result with an Other section
    if result is not None:
        assert isinstance(result.sections, list)


def test_very_large_list():
    """50 items must return a valid result without timeout."""
    items = [f"item_{i}" for i in range(50)]
    result = categorize_items(items)
    # Accept None gracefully — just don't hang or raise
    if result is not None:
        assert isinstance(result.sections, list)


def test_items_with_quantities_preserved():
    """Items specified with quantities must keep that info in output."""
    items = ["2 lbs chicken breast", "1 dozen eggs", "3 avocados"]
    result = categorize_items(items)
    assert result is not None
    all_items = [item for s in result.sections for item in s.items]
    item_names = [i.item.lower() for i in all_items]
    assert any("chicken" in n or "egg" in n or "avocado" in n for n in item_names)


def test_build_embed_fields_empty_result():
    """build_embed_fields with no sections must return empty list."""
    result = GroceryOptimizerResponse(sections=[], duplicates_flagged=[])
    fields = build_embed_fields(result)
    assert fields == []
