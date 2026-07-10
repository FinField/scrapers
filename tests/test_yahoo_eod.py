"""Offline normalize-tests for the Yahoo Finance EOD scraper (no network)."""
import json
from decimal import Decimal

from finfacts.model import Entity
from finscrapers.yahoo_eod import YahooEodSource, yahoo_symbol


# Fixture: minimal Yahoo Finance chart API response (real structure)
# Note: Yahoo returns 'close' or 'adjClose' depending on parameters
YAHOO_RESPONSE = {
    "chart": {
        "result": [
            {
                "timestamp": [1719907200, 1719993600, 1720080000],  # 2026-07-02, 03, 04
                "indicators": {
                    "quote": [
                        {
                            "close": [213.55, 214.05, 212.80],  # Real Yahoo API returns 'close'
                            "volume": [48210000, 39120000, 41550000],
                        }
                    ]
                },
            }
        ]
    }
}


def test_yahoo_symbol():
    """Test ticker-to-yahoo-symbol conversion."""
    assert yahoo_symbol(Entity(ticker="AAPL US")) == "AAPL"
    assert yahoo_symbol(Entity(ticker="BRK.B US")) == "BRK.B"
    assert yahoo_symbol(Entity(ticker="BARC GB")) == "BARC.L"
    assert yahoo_symbol(Entity(ticker="BMW DE")) == "BMW.DE"
    assert yahoo_symbol(Entity(ticker="BTC CRYPTO")) is None
    assert yahoo_symbol(Entity(ticker="UNKNOWN XX")) is None


def test_yahoo_normalize_exact():
    """Test normalization of Yahoo JSON response to FinFacts."""
    json_text = json.dumps(YAHOO_RESPONSE)
    fs = YahooEodSource().normalize(Entity(ticker="AAPL US"), json_text, "test")

    assert fs is not None, "Should normalize non-None FactSet"
    closes = [f for f in fs.facts if f.concept == "finfield:close"]
    vols = [f for f in fs.facts if f.concept == "finfield:volume"]

    assert len(closes) == 3, f"Expected 3 closes, got {len(closes)}"
    assert len(vols) == 3, f"Expected 3 volumes, got {len(vols)}"

    # Verify exact decimal values
    close_values = sorted([f.decimal for f in closes])
    assert close_values == [
        Decimal("212.80"),
        Decimal("213.55"),
        Decimal("214.05"),
    ]

    # Verify unit and currency
    assert all(f.unit == "USD" for f in closes)
    assert all(f.unit == "shares" for f in vols)

    # Verify no floats (all scaled-integer)
    assert all(isinstance(f.value, int) for f in fs.facts)


def test_yahoo_rejects_malformed_json():
    """Test that malformed JSON returns None."""
    assert YahooEodSource().normalize(Entity(ticker="AAPL US"), "not json", "test") is None


def test_yahoo_rejects_empty_results():
    """Test that missing results/timestamps returns None."""
    empty = {"chart": {"result": []}}
    json_text = json.dumps(empty)
    assert (
        YahooEodSource().normalize(Entity(ticker="AAPL US"), json_text, "test")
        is None
    )


def test_yahoo_rejects_missing_quotes():
    """Test that missing quote data returns None."""
    bad_response = {
        "chart": {
            "result": [
                {
                    "timestamp": [1719907200],
                    "indicators": {"quote": []},  # empty quote
                }
            ]
        }
    }
    json_text = json.dumps(bad_response)
    assert (
        YahooEodSource().normalize(Entity(ticker="AAPL US"), json_text, "test")
        is None
    )


def test_yahoo_handles_none_values():
    """Test that None close values are skipped."""
    response = {
        "chart": {
            "result": [
                {
                    "timestamp": [1719907200, 1719993600, 1720080000],
                    "indicators": {
                        "quote": [
                            {
                                "adjClose": [213.55, None, 212.80],  # middle value is None
                                "volume": [48210000, 39120000, 41550000],
                            }
                        ]
                    },
                }
            ]
        }
    }
    json_text = json.dumps(response)
    fs = YahooEodSource().normalize(Entity(ticker="AAPL US"), json_text, "test")
    closes = [f for f in fs.facts if f.concept == "finfield:close"]
    assert len(closes) == 2, "Should skip the None value"


def test_yahoo_same_input_same_cid():
    """Test deterministic CID generation (same input → same output)."""
    json_text = json.dumps(YAHOO_RESPONSE)
    a = YahooEodSource().normalize(Entity(ticker="AAPL US"), json_text, "test")
    b = YahooEodSource().normalize(Entity(ticker="AAPL US"), json_text, "test")
    assert [f.cid for f in a.facts] == [f.cid for f in b.facts]


def test_yahoo_currency_mapping():
    """Test that correct currency is assigned per country."""
    json_text = json.dumps(YAHOO_RESPONSE)

    # GBX for UK stocks (pence)
    fs_gb = YahooEodSource().normalize(Entity(ticker="BARC GB"), json_text, "test")
    assert all(f.unit == "GBX" for f in fs_gb.facts if f.concept == "finfield:close")

    # EUR for German stocks
    fs_de = YahooEodSource().normalize(Entity(ticker="BMW DE"), json_text, "test")
    assert all(f.unit == "EUR" for f in fs_de.facts if f.concept == "finfield:close")
