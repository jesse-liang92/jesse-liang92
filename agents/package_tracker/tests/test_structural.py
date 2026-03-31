"""
package_tracker — structural tests.

Validates schema conformance and database operations.
Tests marked with LLM comments require Ollama running.
"""

import json
import pathlib
import sqlite3
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from lib.schemas import PackageStatusResponse
from agents.package_tracker.agent import (
    init_db,
    add_package,
    remove_package,
    list_packages,
    update_package_status,
    build_update_embed,
)


def _temp_db(tmp_path: pathlib.Path) -> sqlite3.Connection:
    return init_db(str(tmp_path / "test_packages.db"))


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

def test_package_status_schema_valid():
    """PackageStatusResponse accepts valid data."""
    data = {
        "status": "In Transit",
        "location": "Memphis, TN",
        "estimated_delivery": "2026-04-02",
        "delivered": False,
    }
    result = PackageStatusResponse.model_validate(data)
    assert result.status == "In Transit"
    assert result.delivered is False


def test_package_status_schema_minimal():
    """PackageStatusResponse works with only required fields."""
    data = {"status": "Label Created"}
    result = PackageStatusResponse.model_validate(data)
    assert result.location is None
    assert result.delivered is False


def test_package_status_schema_delivered():
    """PackageStatusResponse accepts delivered=True."""
    data = {"status": "Delivered", "delivered": True, "location": "Front Door"}
    result = PackageStatusResponse.model_validate(data)
    assert result.delivered is True


# ---------------------------------------------------------------------------
# Database tests (no Ollama needed)
# ---------------------------------------------------------------------------

def test_init_db_creates_table(tmp_path):
    conn = _temp_db(tmp_path)
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='packages'")
    assert cur.fetchone() is not None


def test_add_and_list_package(tmp_path):
    conn = _temp_db(tmp_path)
    pkg_id = add_package(conn, "1Z999AA10123456784", "ups", "Test package")
    assert pkg_id > 0
    packages = list_packages(conn)
    assert len(packages) == 1
    assert packages[0]["tracking_number"] == "1Z999AA10123456784"
    assert packages[0]["carrier"] == "ups"


def test_add_duplicate_tracking_number_raises(tmp_path):
    conn = _temp_db(tmp_path)
    add_package(conn, "1Z999AA10123456784", "ups")
    try:
        add_package(conn, "1Z999AA10123456784", "ups")
        assert False, "Should have raised IntegrityError"
    except sqlite3.IntegrityError:
        pass


def test_remove_package(tmp_path):
    conn = _temp_db(tmp_path)
    add_package(conn, "1Z999AA10123456784", "ups")
    assert remove_package(conn, "1Z999AA10123456784") is True
    assert list_packages(conn) == []


def test_remove_nonexistent_returns_false(tmp_path):
    conn = _temp_db(tmp_path)
    assert remove_package(conn, "FAKE123") is False


def test_update_status(tmp_path):
    conn = _temp_db(tmp_path)
    add_package(conn, "1Z999AA10123456784", "ups", "Test")
    update_package_status(conn, "1Z999AA10123456784", "In Transit", "Memphis, TN", "2026-04-02", False)
    packages = list_packages(conn)
    assert packages[0]["last_status"] == "In Transit"
    assert packages[0]["last_location"] == "Memphis, TN"


def test_delivered_package_excluded_from_active_list(tmp_path):
    conn = _temp_db(tmp_path)
    add_package(conn, "1Z999AA10123456784", "ups", "Test")
    update_package_status(conn, "1Z999AA10123456784", "Delivered", "Front Door", "", True)
    active = list_packages(conn, include_delivered=False)
    assert len(active) == 0
    all_pkgs = list_packages(conn, include_delivered=True)
    assert len(all_pkgs) == 1


def test_multiple_packages_tracked(tmp_path):
    conn = _temp_db(tmp_path)
    add_package(conn, "UPS123", "ups", "Package 1")
    add_package(conn, "FEDEX456", "fedex", "Package 2")
    add_package(conn, "USPS789", "usps", "Package 3")
    assert len(list_packages(conn)) == 3


# ---------------------------------------------------------------------------
# Embed builder tests (no Ollama needed)
# ---------------------------------------------------------------------------

def test_build_update_embed_active():
    pkg = {
        "tracking_number": "1Z999AA10123456784",
        "carrier": "ups",
        "description": "New keyboard",
        "last_status": "Label Created",
        "delivered": 0,
    }
    embed = build_update_embed(pkg, "In Transit", "Memphis, TN", "2026-04-02", False)
    assert "keyboard" in embed["title"].lower() or "keyboard" in embed["title"]
    assert embed["color"] == 0x5865F2  # blurple for active


def test_build_update_embed_delivered():
    pkg = {
        "tracking_number": "1Z999AA10123456784",
        "carrier": "ups",
        "description": "New keyboard",
        "last_status": "Out for Delivery",
        "delivered": 0,
    }
    embed = build_update_embed(pkg, "Delivered", "Front Door", "", True)
    assert embed["color"] == 0x57F287  # green for delivered
    assert "Delivered" in embed["description"]


def test_build_embed_uses_tracking_number_when_no_description():
    pkg = {
        "tracking_number": "1Z999AA10123456784",
        "carrier": "ups",
        "description": "",
        "last_status": "",
        "delivered": 0,
    }
    embed = build_update_embed(pkg, "In Transit", "", "", False)
    assert "1Z999AA10123456784" in embed["title"]


# ---------------------------------------------------------------------------
# LLM structural test (needs Ollama)
# ---------------------------------------------------------------------------

def test_llm_parses_unstructured_status():
    """LLM must return valid PackageStatusResponse from raw carrier text."""
    from agents.package_tracker.agent import parse_status_with_llm
    raw = (
        "Your item arrived at the MEMPHIS, TN DISTRIBUTION CENTER on March 30, 2026 "
        "at 3:45 AM. The expected delivery date is April 2, 2026."
    )
    result = parse_status_with_llm(raw)
    assert result is not None
    assert isinstance(result.status, str)
    assert len(result.status) > 0
