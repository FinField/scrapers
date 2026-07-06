"""Offline normalize-tests for the entity-fact scrapers (no network)."""
from decimal import Decimal

from finfacts.model import Entity
from finscrapers.coingecko_supply import CoinGeckoSupplySource
from finscrapers.wikidata import WikidataSource, julian_day, _iso

BTC = Entity(ticker="BTC CRYPTO", asset="crypto")
AAPL = Entity(ticker="AAPL US", cik="320193")


def test_julian_day_anchors():
    assert julian_day("2000-01-01") == 2451545
    assert julian_day("1970-01-01") == 2440588
    assert julian_day("2025-12-31") + 1 == julian_day("2026-01-01")


def test_iso_handles_wikidata_precision():
    assert _iso("+1976-04-01T00:00:00Z") == "1976-04-01"
    assert _iso("+1902-00-00T00:00:00Z") == "1902-01-01"  # year precision
    assert _iso("-0050-01-01T00:00:00Z") is None  # BCE: no jdn fact


def _wd_doc():
    return {"results": {"bindings": [
        {"item": {"value": "http://www.wikidata.org/entity/Q312"},
         "founded": {"value": "+1976-04-01T00:00:00Z"},
         "employees": {"value": "164000"},
         "employeesDate": {"value": "+2024-09-28T00:00:00Z"}},
        {"item": {"value": "http://www.wikidata.org/entity/Q312"},
         "founded": {"value": "+1976-04-01T00:00:00Z"},
         "employees": {"value": "161000"},
         "employeesDate": {"value": "+2023-09-30T00:00:00Z"}},
    ]}}


def test_wikidata_normalize_founded_and_employees():
    fs = WikidataSource().normalize(AAPL, _wd_doc())
    founded = [f for f in fs.facts if f.concept == "wikidata:founded"]
    emps = sorted((f for f in fs.facts if f.concept == "wikidata:employees"),
                  key=lambda f: f.period.end)
    assert len(founded) == 1
    assert founded[0].value == julian_day("1976-04-01") and founded[0].unit == "jdn"
    assert founded[0].period.end == "1976-04-01"
    # one employees fact per dated statement — headcounts stay date-coupled
    assert [(f.value, f.period.end) for f in emps] == [
        (161000, "2023-09-30"), (164000, "2024-09-28")]
    assert all(f.source.kind == "wikidata-entity" and "Q312" in f.source.ref
               for f in fs.facts)
    assert all(isinstance(f.value, int) for f in fs.facts)  # never floats


def test_wikidata_covers_and_empty():
    assert WikidataSource().covers(AAPL)
    assert not WikidataSource().covers(Entity(ticker="000020 KR"))
    assert WikidataSource().normalize(AAPL, {"results": {"bindings": []}}) is None


def test_coingecko_supply_normalize():
    doc = {
        "last_updated": "2026-07-06T21:00:11.032Z",
        "market_data": {
            "circulating_supply": Decimal("19892345.5"),
            "total_supply": Decimal("19892345.5"),
            "max_supply": Decimal("21000000"),
        },
    }
    fs = CoinGeckoSupplySource().normalize(BTC, doc, "test")
    by = {f.concept: f for f in fs.facts}
    circ = by["finfield:circulating_supply"]
    assert circ.decimal == Decimal("19892345.5") and circ.unit == "BTC"
    assert circ.period.end == "2026-07-06"  # date-coupled, from last_updated
    assert by["finfield:max_supply"].value == 21000000
    assert all(isinstance(f.value, int) for f in fs.facts)


def test_coingecko_supply_skips_null_max():
    doc = {"last_updated": "2026-07-06T21:00:11Z",
           "market_data": {"circulating_supply": Decimal("120000000"),
                           "total_supply": Decimal("120450000"), "max_supply": None}}
    fs = CoinGeckoSupplySource().normalize(Entity(ticker="ETH CRYPTO"), doc, "test")
    assert {f.concept for f in fs.facts} == {
        "finfield:circulating_supply", "finfield:total_supply"}
