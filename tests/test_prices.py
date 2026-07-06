"""Offline normalize-tests for the price scrapers (no network)."""
from decimal import Decimal

from finfacts.model import Entity
from finscrapers.coingecko import CoinGeckoSource, coin_id
from finscrapers.stooq import StooqSource, stooq_symbol

STOOQ_CSV = """Date,Open,High,Low,Close,Volume
2026-07-02,212.1,214.5,211.0,213.55,48210000
2026-07-03,213.6,215.0,212.8,214.05,39120000
"""


def test_stooq_symbol():
    assert stooq_symbol(Entity(ticker="AAPL US")) == "aapl.us"
    assert stooq_symbol(Entity(ticker="BRK.B US")) == "brk-b.us"
    assert stooq_symbol(Entity(ticker="BTC CRYPTO")) is None


def test_stooq_normalize_exact():
    fs = StooqSource().normalize(Entity(ticker="AAPL US"), STOOQ_CSV, "test")
    closes = [f for f in fs.facts if f.concept == "finfield:close"]
    vols = [f for f in fs.facts if f.concept == "finfield:volume"]
    assert len(closes) == 2 and len(vols) == 2
    last = max(closes, key=lambda f: f.period.end)
    assert last.decimal == Decimal("214.05") and last.unit == "USD"
    assert all(isinstance(f.value, int) for f in fs.facts)  # never floats


def test_stooq_rejects_error_page():
    assert StooqSource().normalize(Entity(ticker="AAPL US"), "No data", "test") is None


def test_coingecko_normalize():
    doc = {
        "prices": [[1751500800000, Decimal("108512.33")], [1751587200000, Decimal("109001.7")]],
        "total_volumes": [[1751500800000, Decimal("31234567890.5")]],
    }
    e = Entity(ticker="BTC CRYPTO")
    assert coin_id(e) == "bitcoin"
    fs = CoinGeckoSource().normalize(e, doc, "test")
    closes = sorted((f for f in fs.facts if f.concept == "finfield:close"),
                    key=lambda f: f.period.end)
    assert [c.decimal for c in closes] == [Decimal("108512.33"), Decimal("109001.7")]
    assert closes[0].period.end == "2025-07-03"
    assert all(isinstance(f.value, int) for f in fs.facts)


def test_same_input_same_cid():
    a = StooqSource().normalize(Entity(ticker="AAPL US"), STOOQ_CSV, "test")
    b = StooqSource().normalize(Entity(ticker="AAPL US"), STOOQ_CSV, "test")
    assert [f.cid for f in a.facts] == [f.cid for f in b.facts]
