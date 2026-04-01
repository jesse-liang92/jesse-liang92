"""
drop_monitor — structural tests.

Validates database operations and embed building. No LLM needed.
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
    list_seen_products,
    reset_seen_products,
    build_drop_embed,
    _get_price,
    _get_image,
)


SAMPLE_PRODUCT = {
    "id": 12345,
    "handle": "test-chroma-pen",
    "title": "Test CHROMA Pen",
    "vendor": "Pvramid",
    "product_type": "Fountain Pen",
    "created_at": "2026-03-20T17:56:15-07:00",
    "variants": [{"price": "260.00"}],
    "images": [{"src": "https://cdn.shopify.com/test.jpg"}],
}


def _temp_db(tmp_path: pathlib.Path) -> sqlite3.Connection:
    return init_db(str(tmp_path / "test_drops.db"))


# ---------------------------------------------------------------------------
# Database tests
# ---------------------------------------------------------------------------

def test_init_db_creates_table(tmp_path):
    conn = _temp_db(tmp_path)
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seen_products'")
    assert cur.fetchone() is not None


def test_mark_and_check_seen(tmp_path):
    conn = _temp_db(tmp_path)
    assert is_product_seen(conn, "test_store", "12345") is False
    mark_product_seen(conn, "test_store", SAMPLE_PRODUCT)
    assert is_product_seen(conn, "test_store", "12345") is True


def test_duplicate_insert_ignored(tmp_path):
    conn = _temp_db(tmp_path)
    mark_product_seen(conn, "test_store", SAMPLE_PRODUCT)
    mark_product_seen(conn, "test_store", SAMPLE_PRODUCT)  # should not raise
    products = list_seen_products(conn)
    assert len(products) == 1


def test_list_seen_products(tmp_path):
    conn = _temp_db(tmp_path)
    mark_product_seen(conn, "store_a", SAMPLE_PRODUCT)
    product_b = {**SAMPLE_PRODUCT, "id": 99999, "title": "Other Pen"}
    mark_product_seen(conn, "store_b", product_b)
    all_products = list_seen_products(conn)
    assert len(all_products) == 2
    store_a_only = list_seen_products(conn, store="store_a")
    assert len(store_a_only) == 1


def test_reset_seen_products(tmp_path):
    conn = _temp_db(tmp_path)
    mark_product_seen(conn, "test_store", SAMPLE_PRODUCT)
    count = reset_seen_products(conn)
    assert count == 1
    assert list_seen_products(conn) == []


def test_reset_specific_store(tmp_path):
    conn = _temp_db(tmp_path)
    mark_product_seen(conn, "store_a", SAMPLE_PRODUCT)
    product_b = {**SAMPLE_PRODUCT, "id": 99999}
    mark_product_seen(conn, "store_b", product_b)
    reset_seen_products(conn, store="store_a")
    remaining = list_seen_products(conn)
    assert len(remaining) == 1
    assert remaining[0]["store"] == "store_b"


# ---------------------------------------------------------------------------
# Price / image helpers
# ---------------------------------------------------------------------------

def test_get_price_from_variants():
    assert _get_price(SAMPLE_PRODUCT) == "$260.00"


def test_get_price_no_variants():
    assert _get_price({"variants": []}) == ""
    assert _get_price({}) == ""


def test_get_image():
    assert _get_image(SAMPLE_PRODUCT) == "https://cdn.shopify.com/test.jpg"


def test_get_image_no_images():
    assert _get_image({"images": []}) == ""
    assert _get_image({}) == ""


# ---------------------------------------------------------------------------
# Embed tests
# ---------------------------------------------------------------------------

def test_build_embed_has_title():
    embed = build_drop_embed(SAMPLE_PRODUCT, "Pvramid Flash Chroma")
    assert "Test CHROMA Pen" in embed["title"]
    assert "NEW DROP" in embed["title"]


def test_build_embed_has_price_field():
    embed = build_drop_embed(SAMPLE_PRODUCT, "Pvramid Flash Chroma")
    field_names = [f["name"] for f in embed["fields"]]
    assert "Price" in field_names


def test_build_embed_has_link():
    embed = build_drop_embed(SAMPLE_PRODUCT, "Pvramid Flash Chroma")
    field_values = [f["value"] for f in embed["fields"]]
    assert any("pvramid.com/products/test-chroma-pen" in v for v in field_values)


def test_build_embed_gold_color():
    embed = build_drop_embed(SAMPLE_PRODUCT, "Test")
    assert embed["color"] == 0xFFD700


def test_build_embed_minimal_product():
    product = {"id": 1, "title": "Bare Product"}
    embed = build_drop_embed(product, "Store")
    assert "Bare Product" in embed["title"]
