"""SEC bulk-mode tests: streaming, core-pack reduction, corrupt-value guards."""
import json
import zipfile

from finfacts.model import Entity
from finscrapers.sec_bulk import CORE_CONCEPTS, iter_bulk, latest_core_facts

DOC = {
    "entityName": "Test Corp",
    "facts": {
        "dei": {
            "EntityCommonStockSharesOutstanding": {
                "units": {
                    "shares": [
                        {"val": 900, "end": "2025-03-31", "fy": 2025, "fp": "Q1", "accn": "a1"},
                        {"val": 1000, "end": "2025-06-30", "fy": 2025, "fp": "Q2", "accn": "a2"},
                    ]
                }
            }
        },
        "us-gaap": {
            "Revenues": {
                "units": {
                    "USD": [
                        {"val": 5, "start": "2025-01-01", "end": "2025-03-31", "accn": "a1"},
                        {"val": None, "end": "2025-06-30"},
                    ]
                }
            },
            "SomeIrrelevantConcept": {"units": {"USD": [{"val": 1, "end": "2025-03-31", "accn": "a1"}]}},
            "Assets": {"units": {"USD": [{"val": "1e400", "end": "2025-03-31", "accn": "a1"}]}},
        },
    },
}


# 1 — reduction keeps the latest observation per core concept, drops the rest
def test_latest_core_facts():
    fs = latest_core_facts(Entity(ticker="TST US"), DOC, "2026-07-06")
    by_concept = {f.concept: f for f in fs.facts}
    shares = by_concept["dei:EntityCommonStockSharesOutstanding"]
    assert shares.value == 1000 and shares.period.end == "2025-06-30"  # latest wins
    assert shares.source.ref == "a2"
    assert by_concept["us-gaap:Revenues"].value == 5  # None obs skipped
    assert "us-gaap:SomeIrrelevantConcept" not in by_concept  # not core
    assert "us-gaap:Assets" not in by_concept  # 1e400: corrupt-value guard


# 2 — shares outstanding is an integer tied to a date
def test_shares_are_dated_integers():
    fs = latest_core_facts(Entity(ticker="TST US"), DOC, "2026-07-06")
    shares = next(f for f in fs.facts if f.concept == "dei:EntityCommonStockSharesOutstanding")
    assert isinstance(shares.value, int) and shares.scale == 0
    assert shares.period.end


# 3 — iter_bulk streams valid CIK entries and skips junk
def test_iter_bulk(tmp_path):
    zp = tmp_path / "bulk.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("CIK0000000007.json", json.dumps(DOC))
        z.writestr("CIK0000000bad.json", "{}")
        z.writestr("notacik.txt", "x")
        z.writestr("CIK0000000009.json", "{invalid json")
    out = list(iter_bulk(zp))
    assert [cik for cik, _ in out] == [7]
    assert out[0][1]["entityName"] == "Test Corp"


# 4 — the free-float concept is part of the core pack
def test_core_pack_has_free_float():
    assert "dei:EntityPublicFloat" in CORE_CONCEPTS
