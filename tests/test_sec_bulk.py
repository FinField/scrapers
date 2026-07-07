"""SEC bulk-mode tests: streaming, core-pack reduction, corrupt-value guards."""
import json
import zipfile

from finfacts.derive import derive_all, synthesize_q4
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


# 8 — YTD frames (Q-tagged but >100 days) never crowd out quarters; FY durations
# land as annuals without displacing a single quarter row
def test_core_history_excludes_ytd_and_keeps_fy_durations():
    ytd = {"val": 777, "start": "2024-01-01", "end": "2024-06-30", "fy": 2024, "fp": "Q2", "accn": "acc-yt"}
    fy = {"val": 888, "start": "2024-01-01", "end": "2024-12-31", "fy": 2024, "fp": "FY", "accn": "acc-fy"}
    obs = quarterly_obs(8)
    fs = core_history_facts(Entity(ticker="TST US"), revenue_doc(obs + [ytd, fy]), "2026-07-06")
    q = [f for f in fs.facts if f.period.fiscal_period != "FY"]
    assert sorted(f.value for f in q) == sorted(ob["val"] for ob in obs)  # quarters unchanged
    assert 777 not in {f.value for f in fs.facts}  # the YTD frame stays out
    assert [f.value for f in fs.facts if f.period.fiscal_period == "FY"] == [888]


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


def annual_obs(n, start_year=2016, base_val=1000):
    """n consecutive calendar fiscal-year duration observations."""
    return [{"val": base_val + i, "start": f"{start_year + i}-01-01",
             "end": f"{start_year + i}-12-31", "fy": start_year + i, "fp": "FY",
             "accn": f"fy-{i:02d}"} for i in range(n)]


def _span(f):
    from datetime import date
    return (date.fromisoformat(f.period.end) - date.fromisoformat(f.period.start)).days


# 12 — annuals keep the TRAILING years (12 in -> the last 8 out), annuals= honored
def test_core_history_annuals_trailing_cut():
    obs = annual_obs(12, start_year=2010)
    fs = core_history_facts(Entity(ticker="TST US"), revenue_doc(obs), "2026-07-06")
    rows = sorted(fs.facts, key=lambda f: f.period.end)
    assert [f.period.end for f in rows] == [ob["end"] for ob in obs[4:]]
    assert [f.value for f in rows] == [ob["val"] for ob in obs[4:]]
    assert all(f.period.fiscal_period == "FY" and f.period.fiscal_year for f in rows)
    fs4 = core_history_facts(Entity(ticker="TST US"), revenue_doc(obs), "2026-07-06", annuals=4)
    assert len(fs4.facts) == 4  # annuals= is honored


# 13 — quarters and annuals coexist: neither displaces the other; a year-span
# comparative tagged with the filing's quarter still counts as an annual, and
# a quarter-length frame tagged FY never does
def test_core_history_fy_and_quarters_coexist():
    q_obs = quarterly_obs(8)  # 2023Q1..2024Q4
    fy_obs = annual_obs(2, start_year=2023)
    comparative = {"val": 555, "start": "2022-01-01", "end": "2022-12-31",
                   "fy": 2022, "fp": "Q2", "accn": "cmp-1"}  # prior-year total in a 10-Q
    q4_frame = {"val": 444, "start": "2024-10-01", "end": "2024-12-31",
                "fy": 2024, "fp": "FY", "accn": "q4f-1"}  # quarter-length, FY-tagged
    doc = revenue_doc(q_obs + fy_obs + [comparative, q4_frame])
    fs = core_history_facts(Entity(ticker="TST US"), doc, "2026-07-06")
    quarters = [f for f in fs.facts if _span(f) <= 100]
    years = [f for f in fs.facts if _span(f) > 100]
    assert sorted(f.value for f in quarters) == sorted(ob["val"] for ob in q_obs)
    assert sorted(f.value for f in years) == [555] + [ob["val"] for ob in fy_obs]
    assert 444 not in {f.value for f in fs.facts}


# 14 — annual restatements dedupe fuzzily: same fy + mostly-overlapping ranges
# collapse to the latest accession; same fy tag WITHOUT overlap (a prior-year
# comparative carrying the filing's fy) stays a separate year
def test_core_history_fy_fuzzy_restatement_dedupe():
    restated = [
        {"val": 1000, "start": "2016-01-03", "end": "2016-12-31", "fy": 2016, "fp": "FY", "accn": "fy-a"},
        {"val": 1010, "start": "2016-01-02", "end": "2016-12-30", "fy": 2016, "fp": "FY", "accn": "fy-b"},
        {"val": 2000, "start": "2017-01-01", "end": "2017-12-31", "fy": 2017, "fp": "FY", "accn": "fy-c"},
    ]
    fs = core_history_facts(Entity(ticker="TST US"), revenue_doc(restated), "2026-07-06")
    assert sorted((f.value, f.source.ref) for f in fs.facts) == [(1010, "fy-b"), (2000, "fy-c")]
    comparatives = [
        {"val": 999, "start": "2016-01-01", "end": "2016-12-31", "fy": 2018, "fp": "FY", "accn": "fy-z"},
        {"val": 3000, "start": "2018-01-01", "end": "2018-12-31", "fy": 2018, "fp": "FY", "accn": "fy-y"},
    ]
    fs = core_history_facts(Entity(ticker="TST US"), revenue_doc(comparatives), "2026-07-06")
    assert sorted(f.value for f in fs.facts) == [999, 3000]


# 15 — bignum guard: a scaled int outside the 64-bit CBOR head is skipped in
# BOTH reducers (EBAY, CIK 1065088 died at weave time); the exact boundary
# values still weave and are kept
def test_bignum_guard_skips_cbor_unencodable():
    obs = quarterly_obs(8)
    obs[-1]["val"] = 1 << 64  # one past the unsigned CBOR boundary
    obs[-2]["val"] = (1 << 64) - 1  # the boundary itself is encodable
    obs[4]["val"] = -(1 << 64)  # negative boundary, also encodable
    fs = core_history_facts(Entity(ticker="TST US"), revenue_doc(obs), "2026-07-06")
    assert obs[-1]["end"] not in [f.period.end for f in fs.facts]  # row skipped, company lives
    assert {(1 << 64) - 1, -(1 << 64)} <= {f.value for f in fs.facts}
    latest = latest_core_facts(Entity(ticker="TST US"), revenue_doc([obs[-1]]), "2026-07-06")
    assert not latest.facts  # identical guard on the freshness pack


# 16 — feed #1 end-to-end on the real SEC shape: NO Q4 rows anywhere (Q4 lives
# inside the FY duration); history pack + finfacts.derive Q4 synthesis still
# close the TTM window, with the full derived_from chain intact
def test_core_history_fy_only_enables_revenue_ttm():
    obs = [ob for ob in quarterly_obs(8, start_year=2024) if ob["fp"] != "Q4"]  # Q1-Q3 only
    obs += [
        {"val": 1000, "start": "2024-01-01", "end": "2024-12-31", "fy": 2024, "fp": "FY", "accn": "fy-24"},
        {"val": 2000, "start": "2025-01-01", "end": "2025-12-31", "fy": 2025, "fp": "FY", "accn": "fy-25"},
    ]
    fs = core_history_facts(Entity(ticker="TST US"), revenue_doc(obs), "2026-07-06")
    assert not any(f.period.fiscal_period == "Q4" for f in fs.facts)  # SEC shape holds
    derived = {f.concept: f for f in derive_all(fs)}
    rev_ttm = derived["finfield:revenue_ttm"]
    assert rev_ttm.value == 2000  # Q1+Q2+Q3 + (FY - Q1 - Q2 - Q3) = FY-2025 exactly
    assert rev_ttm.period.start == "2025-01-01" and rev_ttm.period.end == "2025-12-31"
    synth = synthesize_q4(fs, ("us-gaap:Revenues",))[-1]
    assert rev_ttm.derived_from[-1] == synth.cid  # TTM links the synthesized Q4
    assert set(synth.derived_from) <= {f.cid for f in fs.facts}  # ... which links the filings
