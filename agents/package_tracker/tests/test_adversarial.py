"""
package_tracker — adversarial / edge-case tests.
"""

import pathlib
import sqlite3
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from lib.schemas import PackageStatusResponse
from agents.package_tracker.agent import (
    init_db,
    add_package,
    list_packages,
    update_package_status,
    build_update_embed,
    parse_status_with_llm,
    poll_packages,
    track_package,
)


def _temp_db(tmp_path: pathlib.Path) -> sqlite3.Connection:
    return init_db(str(tmp_path / "test_packages.db"))


# ---------------------------------------------------------------------------
# Database edge cases (no Ollama needed)
# ---------------------------------------------------------------------------

def test_add_package_strips_whitespace(tmp_path):
    conn = _temp_db(tmp_path)
    add_package(conn, "  1Z999AA1  ", "ups", "Test")
    packages = list_packages(conn)
    assert packages[0]["tracking_number"] == "1Z999AA1"


def test_carrier_stored_lowercase(tmp_path):
    conn = _temp_db(tmp_path)
    add_package(conn, "TEST123", "UPS", "Test")
    packages = list_packages(conn)
    assert packages[0]["carrier"] == "ups"


def test_empty_description_ok(tmp_path):
    conn = _temp_db(tmp_path)
    add_package(conn, "TEST123", "fedex")
    packages = list_packages(conn)
    assert packages[0]["description"] == ""


def test_poll_empty_db_returns_zero(tmp_path):
    conn = _temp_db(tmp_path)
    config = {"llm": {"enabled": False, "timeout_seconds": 20}}
    updates = poll_packages(conn, config, dry_run=True)
    assert updates == 0


def test_poll_skips_delivered_packages(tmp_path):
    conn = _temp_db(tmp_path)
    add_package(conn, "TEST123", "ups", "Already delivered")
    update_package_status(conn, "TEST123", "Delivered", "Front Door", "", True)
    config = {"llm": {"enabled": False, "timeout_seconds": 20}}
    updates = poll_packages(conn, config, dry_run=True)
    assert updates == 0


def test_unknown_carrier_returns_none():
    result = track_package("dhl", "FAKE123")
    assert result is None


# ---------------------------------------------------------------------------
# Embed edge cases
# ---------------------------------------------------------------------------

def test_embed_with_empty_location():
    pkg = {
        "tracking_number": "TEST123",
        "carrier": "usps",
        "description": "Test",
        "last_status": "",
        "delivered": 0,
    }
    embed = build_update_embed(pkg, "Label Created", "", "", False)
    field_names = [f["name"] for f in embed["fields"]]
    assert "Location" not in field_names  # no location field when empty


def test_embed_with_empty_estimated_delivery():
    pkg = {
        "tracking_number": "TEST123",
        "carrier": "fedex",
        "description": "Test",
        "last_status": "",
        "delivered": 0,
    }
    embed = build_update_embed(pkg, "In Transit", "Memphis, TN", "", False)
    field_names = [f["name"] for f in embed["fields"]]
    assert "Est. Delivery" not in field_names


# ---------------------------------------------------------------------------
# LLM adversarial (needs Ollama)
# ---------------------------------------------------------------------------

def test_llm_gibberish_input():
    """Gibberish must not crash — return None or a result."""
    result = parse_status_with_llm("xyzzy frobnicate qwerty 12345 !@#$%")
    # Accept None (graceful failure) or a valid response
    if result is not None:
        assert isinstance(result.status, str)


def test_llm_empty_input():
    """Empty string must not crash."""
    result = parse_status_with_llm("")
    if result is not None:
        assert isinstance(result.status, str)


def test_llm_very_long_input():
    """Very long status text must not hang."""
    long_text = "Package is in transit. " * 200
    result = parse_status_with_llm(long_text)
    # Accept None or valid — just don't hang or raise
    if result is not None:
        assert isinstance(result.status, str)
