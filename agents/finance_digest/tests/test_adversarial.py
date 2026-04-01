"""
finance_digest — adversarial / edge-case tests.
"""

import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from lib.schemas import FinanceDigestResponse
from agents.finance_digest.agent import (
    identify_alerts,
    build_market_embed,
    generate_summary,
    fetch_watchlist_data,
)


# ---------------------------------------------------------------------------
# Data edge cases (no Ollama needed)
# ---------------------------------------------------------------------------

def test_identify_alerts_zero_change():
    """Zero percent change should not trigger an alert."""
    data = [{"ticker": "SPY", "label": "S&P 500", "price": 515.00, "prev_close": 515.00, "change": 0.0, "change_pct": 0.0}]
    alerts = identify_alerts(data)
    assert alerts == []


def test_identify_alerts_exactly_at_threshold():
    """Move exactly at threshold should be flagged."""
    data = [{"ticker": "SPY", "label": "S&P 500", "price": 525.30, "prev_close": 515.00, "change": 10.30, "change_pct": 2.0}]
    alerts = identify_alerts(data, threshold=2.0)
    assert len(alerts) == 1


def test_identify_alerts_negative_big_move():
    """Large negative move should say 'down'."""
    data = [{"ticker": "NVDA", "label": "NVIDIA", "price": 800.00, "prev_close": 860.00, "change": -60.00, "change_pct": -6.98}]
    alerts = identify_alerts(data)
    assert len(alerts) == 1
    assert "down" in alerts[0]


def test_build_embed_single_ticker():
    """Embed should work with just one ticker."""
    data = [{"ticker": "SPY", "label": "S&P 500", "price": 520.00, "prev_close": 515.00, "change": 5.00, "change_pct": 0.97}]
    embed = build_market_embed(data, "Just SPY.", [])
    assert "Market Digest" in embed["title"]
    assert len(embed["fields"]) >= 1


def test_build_embed_empty_data():
    """Embed should handle empty market data gracefully."""
    embed = build_market_embed([], "No data.", [])
    assert embed["title"] is not None
    assert embed["color"] == 0x95A5A6  # grey, no SPY


def test_build_embed_no_alerts():
    """Embed without alerts should have no Notable Moves field."""
    data = [{"ticker": "SPY", "label": "S&P 500", "price": 516.00, "prev_close": 515.00, "change": 1.00, "change_pct": 0.19}]
    embed = build_market_embed(data, "Quiet.", [])
    field_names = [f["name"] for f in embed["fields"]]
    assert not any("Notable" in name for name in field_names)


def test_build_embed_many_alerts():
    """Embed should handle many alerts without crashing."""
    alerts = [f"TICKER{i} up +{i}.00%" for i in range(20)]
    embed = build_market_embed([], "Lots of alerts.", alerts)
    alert_fields = [f for f in embed["fields"] if "Notable" in f["name"]]
    assert len(alert_fields) == 1


def test_fetch_watchlist_invalid_ticker():
    """Invalid tickers should be skipped gracefully, not crash."""
    result = fetch_watchlist_data([{"ticker": "ZZZZNOTREAL123", "label": "Fake"}])
    # Should return empty list or skip the bad ticker — just don't crash
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Schema edge cases
# ---------------------------------------------------------------------------

def test_schema_very_long_summary():
    """Schema should accept a very long summary."""
    data = {"summary": "A" * 5000, "alerts": [], "watchlist": []}
    result = FinanceDigestResponse.model_validate(data)
    assert len(result.summary) == 5000


def test_schema_special_chars_in_summary():
    """Schema should accept special characters."""
    data = {"summary": "Markets up! $SPY +2.5% — strong close 📈", "alerts": []}
    result = FinanceDigestResponse.model_validate(data)
    assert "$SPY" in result.summary


# ---------------------------------------------------------------------------
# LLM adversarial (needs Ollama)
# ---------------------------------------------------------------------------

def test_llm_empty_market_data():
    """Empty market data should not crash the LLM call."""
    result = generate_summary([], [])
    # Accept None (graceful failure) or a valid response
    if result is not None:
        assert isinstance(result.summary, str)


def test_llm_single_ticker():
    """LLM should handle a single ticker."""
    data = [{"ticker": "SPY", "label": "S&P 500", "price": 520.00, "prev_close": 515.00, "change": 5.00, "change_pct": 0.97}]
    result = generate_summary(data, [])
    if result is not None:
        assert isinstance(result.summary, str)
        assert len(result.summary) > 0
