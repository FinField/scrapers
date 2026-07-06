"""Offline normalize-tests for the SEC submissions adapter (no network)."""
from datetime import date

from finfacts.model import Entity
from finscrapers.sec_submissions import SecSubmissionsSource

AAPL = Entity(ticker="AAPL US", cik="320193")
BTC = Entity(ticker="BTC CRYPTO", asset="crypto")

REF = "https://data.sec.gov/submissions/CIK0000320193.json"

DOC = {
    "cik": 320193,
    "name": "Apple Inc.",
    "sic": "3571",
    "sicDescription": "Electronic Computers",
    "tickers": ["AAPL"],
    "exchanges": ["Nasdaq"],
}


def test_normalize_emits_exactly_one_sic_fact():
    fs = SecSubmissionsSource().normalize(AAPL, DOC, REF)
    assert [f.concept for f in fs.facts] == ["finfield:sic"]
    fact = fs.facts[0]
    assert fact.value == 3571 and fact.scale == 0 and fact.unit == "pure"
    assert fact.period.end == date.today().isoformat()  # observation-dated
    assert fact.source.kind == "sec-submissions" and fact.source.ref == REF
    assert isinstance(fact.value, int)  # never floats, never text (sicDescription)


def test_invalid_or_missing_sic_yields_no_fact():
    src = SecSubmissionsSource()
    for bad in ("", "0000", "39A9", None):
        assert src.normalize(AAPL, dict(DOC, sic=bad), REF) is None
    missing = dict(DOC)
    del missing["sic"]
    assert src.normalize(AAPL, missing, REF) is None


def test_determinism_same_fixture_same_cids():
    src = SecSubmissionsSource()
    a = src.normalize(AAPL, DOC, REF)
    b = src.normalize(AAPL, DOC, REF)
    assert [f.cid for f in a.facts] == [f.cid for f in b.facts]


def test_covers_requires_resolvable_cik():
    src = SecSubmissionsSource()
    assert src.kind == "sec-submissions"
    assert src.covers(AAPL)  # explicit CIK, no network
    assert not src.covers(BTC)  # composite non-US ticker -> no CIK
