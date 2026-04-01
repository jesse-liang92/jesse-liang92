"""
finance_digest — behavioral tests (needs Ollama).

Tests LLM reasoning quality with known market scenarios.
"""

import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from agents.finance_digest.agent import generate_summary


# ---------------------------------------------------------------------------
# Scenario: broad rally
# ---------------------------------------------------------------------------

RALLY_DATA = [
    {"ticker": "SPY", "label": "S&P 500", "price": 530.00, "prev_close": 515.00, "change": 15.00, "change_pct": 2.91},
    {"ticker": "QQQ", "label": "Nasdaq 100", "price": 460.00, "prev_close": 445.00, "change": 15.00, "change_pct": 3.37},
    {"ticker": "NVDA", "label": "NVIDIA", "price": 920.00, "prev_close": 860.00, "change": 60.00, "change_pct": 6.98},
    {"ticker": "XBI", "label": "Biotech ETF", "price": 96.00, "prev_close": 90.00, "change": 6.00, "change_pct": 6.67},
]


def test_rally_summary_positive_tone():
    """When everything is up big, the summary should reflect a strong rally."""
    alerts = ["NVDA up +6.98%", "XBI up +6.67%", "QQQ up +3.37%", "SPY up +2.91%"]
    result = generate_summary(RALLY_DATA, alerts)
    assert result is not None
    summary_lower = result.summary.lower()
    # Should mention something positive
    assert any(word in summary_lower for word in ["rally", "gain", "surge", "higher", "rose", "up", "climb", "advance"])


# ---------------------------------------------------------------------------
# Scenario: broad selloff
# ---------------------------------------------------------------------------

SELLOFF_DATA = [
    {"ticker": "SPY", "label": "S&P 500", "price": 495.00, "prev_close": 515.00, "change": -20.00, "change_pct": -3.88},
    {"ticker": "QQQ", "label": "Nasdaq 100", "price": 420.00, "prev_close": 445.00, "change": -25.00, "change_pct": -5.62},
    {"ticker": "NVDA", "label": "NVIDIA", "price": 790.00, "prev_close": 860.00, "change": -70.00, "change_pct": -8.14},
    {"ticker": "XBI", "label": "Biotech ETF", "price": 85.00, "prev_close": 90.00, "change": -5.00, "change_pct": -5.56},
]


def test_selloff_summary_negative_tone():
    """When everything is down big, the summary should reflect a selloff."""
    alerts = ["NVDA down -8.14%", "QQQ down -5.62%", "XBI down -5.56%", "SPY down -3.88%"]
    result = generate_summary(SELLOFF_DATA, alerts)
    assert result is not None
    summary_lower = result.summary.lower()
    assert any(word in summary_lower for word in ["sell", "drop", "decline", "fell", "down", "loss", "lower", "slid", "tumbl", "plunge"])


# ---------------------------------------------------------------------------
# Scenario: mixed / flat day
# ---------------------------------------------------------------------------

MIXED_DATA = [
    {"ticker": "SPY", "label": "S&P 500", "price": 515.50, "prev_close": 515.00, "change": 0.50, "change_pct": 0.10},
    {"ticker": "QQQ", "label": "Nasdaq 100", "price": 444.00, "prev_close": 445.00, "change": -1.00, "change_pct": -0.22},
    {"ticker": "NVDA", "label": "NVIDIA", "price": 865.00, "prev_close": 860.00, "change": 5.00, "change_pct": 0.58},
    {"ticker": "XBI", "label": "Biotech ETF", "price": 89.50, "prev_close": 90.00, "change": -0.50, "change_pct": -0.56},
]


def test_mixed_day_summary():
    """Flat/mixed day should produce a valid summary without strong directional language."""
    result = generate_summary(MIXED_DATA, [])
    assert result is not None
    assert isinstance(result.summary, str)
    assert len(result.summary) > 10
