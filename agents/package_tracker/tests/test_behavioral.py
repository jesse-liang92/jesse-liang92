"""
package_tracker — behavioral tests.

Tests LLM reasoning quality with known inputs. Requires Ollama running.
"""

import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from agents.package_tracker.agent import parse_status_with_llm


def test_delivered_status_detected():
    """LLM must identify 'delivered' from natural language."""
    raw = (
        "Your package was delivered to the front door at 2:15 PM on March 31, 2026 "
        "in LOS ANGELES, CA 90025."
    )
    result = parse_status_with_llm(raw)
    assert result is not None
    assert result.delivered is True
    assert result.location is not None


def test_in_transit_status_detected():
    """LLM must identify in-transit status."""
    raw = (
        "Your package departed the FedEx facility in INDIANAPOLIS, IN on March 30, 2026 "
        "at 11:30 PM. Estimated delivery: April 2, 2026."
    )
    result = parse_status_with_llm(raw)
    assert result is not None
    assert result.delivered is False
    assert "transit" in result.status.lower() or "departed" in result.status.lower() or "shipping" in result.status.lower()


def test_estimated_delivery_extracted():
    """LLM must extract estimated delivery date when present."""
    raw = (
        "Package is in transit. Currently at MEMPHIS, TN hub. "
        "Expected delivery by end of day April 3, 2026."
    )
    result = parse_status_with_llm(raw)
    assert result is not None
    assert result.estimated_delivery is not None
    assert "april" in result.estimated_delivery.lower() or "2026-04-03" in result.estimated_delivery


def test_out_for_delivery_detected():
    """LLM must identify out-for-delivery status."""
    raw = "Out for delivery - your package is on the vehicle for delivery today in Los Angeles, CA."
    result = parse_status_with_llm(raw)
    assert result is not None
    assert result.delivered is False
    assert "delivery" in result.status.lower()


def test_exception_status_detected():
    """LLM must handle exception/delay statuses."""
    raw = (
        "DELIVERY EXCEPTION: Package delayed due to weather conditions in the destination area. "
        "New estimated delivery: April 5, 2026. Last scan: Chicago, IL."
    )
    result = parse_status_with_llm(raw)
    assert result is not None
    assert result.delivered is False
    assert result.location is not None
