"""SEC bulk-mode tests: streaming, core-pack reduction, corrupt-value guards."""
import json
import zipfile

from finfacts.derive import derive_all
from finfacts.model import Entity
from finscrapers.sec_bulk import CORE_CONCEPTS, core_history_facts, iter_bulk, latest_core_facts

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


_CALENDAR_QUARTERS = (("01-01", "03-31", "Q1"), ("04-01", "06-30", "Q2"),
                      ("07-01", "09-30", "Q3"), ("10-01", "12-31", "Q4"))


def quarterly_obs(n, start_year=2023, base_val=100):
    """n contiguous calendar-quarter duration observations."""
    obs = []
    for i in range(n):
        year, (s, e, fp) = start_year + i // 4, _CALENDAR_QUARTERS[i % 4]
        obs.append({"val": base_val + i, "start": f"{year}-{s}", "end": f"{year}-{e}",
                    "fy": year, "fp": fp, "accn": f"acc-{i:02d}"})
    return obs


def revenue_doc(obs):
    return {"facts": {"us-gaap": {"Revenues": {"units": {"USD": obs}}}}}


# 5 — history keeps the TRAILING quarters (12 in -> the last 8 out)
def test_core_history_trailing_cut():
    obs = quarterly_obs(12)
    fs = core_history_facts(Entity(ticker="TST US"), revenue_doc(obs), "2026-07-06")
    rows = sorted(fs.facts, key=lambda f: f.period.end)
    assert [f.period.end for f in rows] == [ob["end"] for ob in obs[4:]]
    assert [f.value for f in rows] == [ob["val"] for ob in obs[4:]]
    fs4 = core_history_facts(Entity(ticker="TST US"), revenue_doc(obs), "2026-07-06", quarters=4)
    assert len(fs4.facts) == 4  # quarters= is honored


# 6 — restatements per (start, end): the latest accession wins
def test_core_history_restatement_dedupe():
    obs = quarterly_obs(8)
    restated = dict(obs[-1], val=999, accn="acc-99")
    fs = core_history_facts(Entity(ticker="TST US"), revenue_doc(obs + [restated]), "2026-07-06")
    rows = [f for f in fs.facts if f.period.end == obs[-1]["end"]]
    assert len(rows) == 1 and rows[0].value == 999 and rows[0].source.ref == "acc-99"


# 7 — instants: trailing distinct dates, latest accession per date
def test_core_history_instants_keep_distinct_dates():
    obs = [{"val": 100 + i, "end": f"{2020 + i}-12-31", "fy": 2020 + i, "fp": "FY",
            "accn": f"i-{i:02d}"} for i in range(10)]
    obs.append(dict(obs[-1], val=555, accn="i-99"))  # same date, later accession
    doc = {"facts": {"dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": obs}}}}}
    fs = core_history_facts(Entity(ticker="TST US"), doc, "2026-07-06")
    rows = sorted(fs.facts, key=lambda f: f.period.end)
    assert len(rows) == 8 and all(f.period.is_instant for f in rows)  # 10 dates -> trailing 8
    assert rows[0].period.end == "2022-12-31"
    assert rows[-1].value == 555 and rows[-1].source.ref == "i-99"


# 8 — YTD frames (Q-tagged but >100 days) and FY durations don't crowd out quarters
def test_core_history_excludes_ytd_and_fy_durations():
    ytd = {"val": 777, "start": "2024-01-01", "end": "2024-06-30", "fy": 2024, "fp": "Q2", "accn": "acc-yt"}
    fy = {"val": 888, "start": "2024-01-01", "end": "2024-12-31", "fy": 2024, "fp": "FY", "accn": "acc-fy"}
    obs = quarterly_obs(8)
    fs = core_history_facts(Entity(ticker="TST US"), revenue_doc(obs + [ytd, fy]), "2026-07-06")
    assert sorted(f.value for f in fs.facts) == sorted(ob["val"] for ob in obs)


# 9 — corrupt values never claim a trailing slot
def test_core_history_corrupt_value_guard():
    obs = quarterly_obs(8)
    obs[-1]["val"] = "1e400"
    fs = core_history_facts(Entity(ticker="TST US"), revenue_doc(obs), "2026-07-06")
    assert len(fs.facts) == 7 and obs[-1]["end"] not in [f.period.end for f in fs.facts]


# 10 — determinism: the same doc mints byte-identical facts (same CIDs)
def test_core_history_deterministic():
    doc = revenue_doc(quarterly_obs(10))
    a = core_history_facts(Entity(ticker="TST US"), doc, "2026-07-06")
    b = core_history_facts(Entity(ticker="TST US"), doc, "2026-07-06")
    assert [f.cid for f in a.facts] == [f.cid for f in b.facts]


# 11 — the property that fixes feed #1: the history pack satisfies the TTM guard
def test_core_history_enables_revenue_ttm():
    obs = quarterly_obs(10)
    fs = core_history_facts(Entity(ticker="TST US"), revenue_doc(obs), "2026-07-06")
    derived = {f.concept: f for f in derive_all(fs)}
    assert "finfield:revenue_ttm" in derived
    assert derived["finfield:revenue_ttm"].value == sum(ob["val"] for ob in obs[-4:])
