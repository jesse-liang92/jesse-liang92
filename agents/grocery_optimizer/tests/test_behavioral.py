"""
grocery_optimizer — behavioral tests.
"""

import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from agents.grocery_optimizer.agent import categorize_items, build_embed_fields
from lib.schemas import GroceryOptimizerResponse, GrocerySection, GroceryItem


def test_produce_items_grouped_together():
    """Bananas and apples must land in a Produce section."""
    items = ["bananas", "apples", "spinach", "whole milk", "cheddar cheese"]
    result = categorize_items(items)
    assert result is not None
    section_names = [s.name.lower() for s in result.sections]
    assert any("produce" in n or "fruit" in n or "vegetable" in n for n in section_names)


def test_dairy_items_grouped_together():
    """Milk, cheese, yogurt must land in a Dairy section."""
    items = ["whole milk", "cheddar cheese", "greek yogurt", "bananas"]
    result = categorize_items(items)
    assert result is not None
    section_names = [s.name.lower() for s in result.sections]
    assert any("dairy" in n for n in section_names)


def test_duplicate_detected():
    """'bananas' listed twice must appear in duplicates_flagged."""
    items = ["bananas", "whole milk", "bananas"]
    result = categorize_items(items)
    assert result is not None
    assert len(result.duplicates_flagged) > 0


def test_all_items_accounted_for():
    """Total items across all sections must equal or approximate input count."""
    items = ["bananas", "milk", "bread", "chicken", "eggs"]
    result = categorize_items(items)
    assert result is not None
    total = sum(len(s.items) for s in result.sections)
    # Allow for deduplication — total should be within 1 of input
    assert abs(total - len(items)) <= 1


def test_embed_fields_inline_for_sections():
    """Section fields must be inline=True for compact Discord display."""
    result = categorize_items(["bananas", "milk", "bread"])
    assert result is not None
    fields = build_embed_fields(result)
    section_fields = [f for f in fields if ":warning:" not in f["name"]]
    for f in section_fields:
        assert f.get("inline") is True
