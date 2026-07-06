"""SEC adapter tests on a canned companyfacts document (no network)."""
from finfacts.model import Entity
from finscrapers.sec_edgar import SecEdgarSource

DOC = {
    "cik": 320193,
    "facts": {
        "us-gaap": {
            "Revenues": {
                "units": {
                    "USD": [
                        {"val": 1000, "start": "2025-01-01", "end": "2025-03-31", "fy": 2025, "fp": "Q1", "accn": "a1"},
                        {"val": 1000, "start": "2025-01-01", "end": "2025-03-31", "fy": 2025, "fp": "Q1", "accn": "a1"},
                        {"val": None, "end": "2025-03-31"},
                    ]
                }
            },
            "EarningsPerShareBasic": {
                "units": {"USD/shares": [{"val": 1.23, "start": "2025-01-01", "end": "2025-03-31", "fy": 2025, "fp": "Q1", "accn": "a1"}]}
            },
        }
    },
}


def test_normalize():
    src = SecEdgarSource()
    fs = src.normalize(Entity(ticker="AAPL US"), DOC)
    by_concept = {}
    for f in fs.facts:
        by_concept.setdefault(f.concept, []).append(f)
    # duplicates collapsed, None dropped
    assert len(by_concept["us-gaap:Revenues"]) == 1
    rev = by_concept["us-gaap:Revenues"][0]
    assert rev.value == 1000 and rev.scale == 0 and rev.unit == "USD"
    assert rev.source.ref == "a1"
    # floats become exact scaled integers
    eps = by_concept["us-gaap:EarningsPerShareBasic"][0]
    assert (eps.value, eps.scale) == (123, 2)


def test_covers_us_only():
    src = SecEdgarSource()
    src._ticker_to_cik = {"AAPL": 320193}
    assert src.covers(Entity(ticker="AAPL US"))
    assert not src.covers(Entity(ticker="000660 KR"))
