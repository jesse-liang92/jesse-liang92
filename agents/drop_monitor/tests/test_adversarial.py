"""
drop_monitor — adversarial / edge-case tests.
"""

import pathlib
import sqlite3
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from agents.drop_monitor.agent import (
    init_db,
    is_product_seen,
    mark_product_seen,
    build_drop_embed,
    fetch_products,
    poll_stores,
    _get_price,
)


def _temp_db(tmp_path: pathlib.Path) -> sqlite3.Connection:
    return init_db(str(tmp_path / "test_drops.db"))


# ---------------------------------------------------------------------------
# Database edge cases
# ---------------------------------------------------------------------------

def test_product_id_stored_as_string(tmp_path):
    """Product IDs should work whether int or string."""
    conn = _temp_db(tmp_path)
    product = {"id": 12345, "title": "Test", "handle": "test"}
    mark_product_seen(conn, "store", product)
    assert is_product_seen(conn, "store", "12345") is True


def test_missing_fields_dont_crash(tmp_path):
    """Product with minimal fields should not crash."""
    conn = _temp_db(tmp_path)
    product = {"id": 1, "title": "Bare"}
    mark_product_seen(conn, "store", product)
    assert is_product_seen(conn, "store", "1") is True


def test_poll_empty_config(tmp_path):
    """Empty stores config should return 0."""
    conn = _temp_db(tmp_path)
    config = {"stores": {}}
    result = poll_stores(conn, config, dry_run=True)
    assert result == 0


def test_poll_no_stores_key(tmp_path):
    """Missing stores key should return 0."""
    conn = _temp_db(tmp_path)
    config = {}
    result = poll_stores(conn, config, dry_run=True)
    assert result == 0


# ---------------------------------------------------------------------------
# Fetch edge cases
# ---------------------------------------------------------------------------

def test_fetch_invalid_url():
    """Invalid URL should return None, not crash."""
    result = fetch_products("https://not-a-real-store-xyz.com/products.json")
    assert result is None


# ---------------------------------------------------------------------------
# Price edge cases
# ---------------------------------------------------------------------------

def test_price_zero():
    product = {"variants": [{"price": "0.00"}]}
    assert _get_price(product) == "$0.00"


def test_price_string_with_decimals():
    product = {"variants": [{"price": "1299.99"}]}
    assert _get_price(product) == "$1299.99"


# ---------------------------------------------------------------------------
# Embed edge cases
# ---------------------------------------------------------------------------

def test_embed_no_handle():
    product = {"id": 1, "title": "No Handle"}
    embed = build_drop_embed(product, "Store")
    # Should not crash, link field may be absent
    assert "No Handle" in embed["title"]


def test_embed_no_images():
    product = {"id": 1, "title": "No Image", "handle": "no-image"}
    embed = build_drop_embed(product, "Store")
    assert "image" not in embed  # no image key when no images


def test_embed_with_image():
    product = {
        "id": 1,
        "title": "With Image",
        "handle": "with-image",
        "images": [{"src": "https://example.com/img.jpg"}],
    }
    embed = build_drop_embed(product, "Store")
    assert embed["image"]["url"] == "https://example.com/img.jpg"
