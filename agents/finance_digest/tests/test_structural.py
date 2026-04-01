"""
finance_digest — structural tests.

Validates schema conformance, embed building, and data processing.
Tests marked with LLM comments require Ollama running.
"""

import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))

from lib.schemas import FinanceDigestResponse
from agents.finance_digest.agent import (
    identify_alerts,
    build_market_embed,
    ALERT_PCT_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

SAMPLE_MARKET_DATA = [
    {"ticker": "SPY", "label": "S&P 500", "price": 520.50, "prev_close": 515.00, "change": 5.50, "change_pct": 1.07},
    {"ticker": "QQQ", "label": "Nasdaq 100", "price": 440.20, "prev_close": 445.00, "change": -4.80, "change_pct": -1.08},
    {"ticker": "NVDA", "label": "NVIDIA", "price": 890.00, "prev_close": 860.00, "change": 30.00, "change_pct": 3.49},
    {"ticker": "XBI", "label": "Biotech ETF", "price": 92.30, "prev_close": 90.00, "change": 2.30, "change_pct": 2.56},
    {"ticker": "ASML", "label": "ASML", "price": 950.00, "prev_close": 955.00, "change": -5.00, "change_pct": -0.52},
    {"ticker": "NFLX", "label": "Netflix", "price": 625.00, "prev_close": 620.00, "change": 5.00, "change_pct": 0.81},
]


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

def test_finance_digest_schema_valid():
    """FinanceDigestResponse accepts valid data."""
    data = {
        "summary": "Markets closed higher today.",
        "alerts": ["NVDA up +3.49%"],
        "watchlist": [{"ticker": "SPY", "price": 520.50, "change_pct": 1.07}],
    }
    result = FinanceDigestResponse.model_validate(data)
    assert result.summary == "Markets closed higher today."
    assert len(result.alerts) == 1


def test_finance_digest_schema_minimal():
    """FinanceDigestResponse works with only required fields."""
    data = {"summary": "Quiet day in the markets."}
    result = FinanceDigestResponse.model_validate(data)
    assert result.alerts == []
    assert result.watchlist == []


def test_finance_digest_schema_empty_alerts():
    """Schema accepts empty alerts list."""
    data = {"summary": "No notable moves today.", "alerts": [], "watchlist": []}
    result = FinanceDigestResponse.model_validate(data)
    assert result.alerts == []


# ---------------------------------------------------------------------------
# Alert identification tests (no Ollama needed)
# ---------------------------------------------------------------------------

def test_identify_alerts_flags_big_movers():
    """Tickers moving >= threshold should be flagged."""
    alerts = identify_alerts(SAMPLE_MARKET_DATA)
    alert_text = " ".join(alerts)
    assert "NVDA" in alert_text
    assert "XBI" in alert_text


def test_identify_alerts_skips_small_moves():
    """Tickers moving < threshold should not be flagged."""
    alerts = identify_alerts(SAMPLE_MARKET_DATA)
    alert_text = " ".join(alerts)
    assert "ASML" not in alert_text
    assert "NFLX" not in alert_text


def test_identify_alerts_empty_data():
    """Empty data should return no alerts."""
    alerts = identify_alerts([])
    assert alerts == []


def test_identify_alerts_custom_threshold():
    """Custom threshold works."""
    alerts = identify_alerts(SAMPLE_MARKET_DATA, threshold=1.0)
    alert_text = " ".join(alerts)
    assert "SPY" in alert_text  # 1.07% >= 1.0%
    assert "NVDA" in alert_text


def test_identify_alerts_direction():
    """Alerts should indicate up/down direction."""
    alerts = identify_alerts(SAMPLE_MARKET_DATA)
    for alert in alerts:
        assert "up" in alert or "down" in alert


# ---------------------------------------------------------------------------
# Embed builder tests (no Ollama needed)
# ---------------------------------------------------------------------------

def test_build_embed_has_title():
    embed = build_market_embed(SAMPLE_MARKET_DATA, "Test summary.", [])
    assert "Market Digest" in embed["title"]


def test_build_embed_has_summary():
    embed = build_market_embed(SAMPLE_MARKET_DATA, "Markets rallied hard.", [])
    assert embed["description"] == "Markets rallied hard."


def test_build_embed_green_when_spy_up():
    embed = build_market_embed(SAMPLE_MARKET_DATA, "Up day.", [])
    assert embed["color"] == 0x57F287  # green


def test_build_embed_red_when_spy_down():
    data = [{"ticker": "SPY", "label": "S&P 500", "price": 510.00, "prev_close": 515.00, "change": -5.00, "change_pct": -0.97}]
    embed = build_market_embed(data, "Down day.", [])
    assert embed["color"] == 0xED4245  # red


def test_build_embed_grey_when_no_spy():
    data = [{"ticker": "NVDA", "label": "NVIDIA", "price": 890.00, "prev_close": 860.00, "change": 30.00, "change_pct": 3.49}]
    embed = build_market_embed(data, "No SPY data.", [])
    assert embed["color"] == 0x95A5A6  # grey


def test_build_embed_includes_alert_field():
    alerts = ["NVDA up +3.49%"]
    embed = build_market_embed(SAMPLE_MARKET_DATA, "Summary.", alerts)
    field_names = [f["name"] for f in embed["fields"]]
    assert any("Notable" in name for name in field_names)


def test_build_embed_categories():
    """Embed should group tickers into categories."""
    embed = build_market_embed(SAMPLE_MARKET_DATA, "Summary.", [])
    field_names = [f["name"] for f in embed["fields"]]
    assert "Broad Market" in field_names
    assert "AI / Semis" in field_names


def test_build_embed_uncategorized_tickers():
    """Tickers not in predefined categories should appear in 'Other'."""
    data = [{"ticker": "PLTR", "label": "Palantir", "price": 25.00, "prev_close": 24.00, "change": 1.00, "change_pct": 4.17}]
    embed = build_market_embed(data, "Summary.", [])
    field_names = [f["name"] for f in embed["fields"]]
    assert "Other" in field_names


# ---------------------------------------------------------------------------
# LLM structural test (needs Ollama)
# ---------------------------------------------------------------------------

def test_llm_generates_summary():
    """LLM must return valid FinanceDigestResponse from market data."""
    from agents.finance_digest.agent import generate_summary
    result = generate_summary(SAMPLE_MARKET_DATA, ["NVDA up +3.49%"])
    assert result is not None
    assert isinstance(result.summary, str)
    assert len(result.summary) > 10
