"""Offline normalize-tests for the ECB SDMX macro scraper (no network)."""
from decimal import Decimal

from finfacts.model import Entity
from finscrapers.ecb import MACRO_REGIONS, SERIES, EcbSource, _iso_period

EA = Entity(ticker="EA MACRO", asset="macro")

# csvdata shape: header incl. KEY, TIME_PERIOD, OBS_VALUE plus dimension noise
FM_CSV = """KEY,FREQ,PROVIDER_FM,TIME_PERIOD,OBS_VALUE,OBS_STATUS
FM.B.U2.EUR.4F.KR.MRR_FR.LEV,B,4F,2026-07-02,2.15,A
FM.B.U2.EUR.4F.KR.MRR_FR.LEV,B,4F,2026-07-03,2.15,A
"""

EXR_CSV = """KEY,FREQ,CURRENCY,TIME_PERIOD,OBS_VALUE,OBS_STATUS
EXR.D.USD.EUR.SP00.A,D,USD,2026-07-02,1.1734,A
EXR.D.USD.EUR.SP00.A,D,USD,2026-07-03,1.179,A
"""

ICP_CSV = """KEY,FREQ,REF_AREA,TIME_PERIOD,OBS_VALUE,OBS_STATUS
ICP.M.U2.N.000000.4.ANR,M,U2,2026-04,2.2,A
ICP.M.U2.N.000000.4.ANR,M,U2,2026-05,2.4,A
"""

FIXTURE_BY_FLOW = {"FM": FM_CSV, "EXR": EXR_CSV, "ICP": ICP_CSV}


def test_covers_only_ea_macro():
    src = EcbSource()
    assert MACRO_REGIONS == ("EA MACRO",)
    assert src.covers(EA)
    assert not src.covers(Entity(ticker="AAPL US"))
    assert not src.covers(Entity(ticker="US MACRO", asset="macro"))
    assert src.fetch(Entity(ticker="AAPL US")) is None


def test_iso_period_normalization():
    assert _iso_period("2026-07-03") == "2026-07-03"  # daily as-is
    assert _iso_period("2026-05") == "2026-05-01"  # monthly -> first of month
    assert _iso_period("2026-Q2") is None  # quarterly: out of scope
    assert _iso_period("") is None


def test_normalize_monthly_cpi_exact_decimal():
    fs = EcbSource().normalize(EA, "finfield:cpi_yoy", ICP_CSV, "percent", "test")
    facts = sorted(fs.facts, key=lambda f: f.period.end)
    assert [f.period.end for f in facts] == ["2026-04-01", "2026-05-01"]
    last = facts[-1]
    assert (last.value, last.scale) == (24, 1)  # OBS_VALUE 2.4, never a float
    assert last.decimal == Decimal("2.4") and last.unit == "percent"
    assert last.entity_id == "ticker:EA MACRO"
    assert all(isinstance(f.value, int) for f in facts)


def test_normalize_daily_fx():
    fs = EcbSource().normalize(EA, "finfield:fx_usd", EXR_CSV, "USD", "test")
    facts = sorted(fs.facts, key=lambda f: f.period.end)
    assert facts[-1].period.end == "2026-07-03"
    assert [f.decimal for f in facts] == [Decimal("1.1734"), Decimal("1.179")]
    assert all(f.unit == "USD" and f.source.kind == "ecb-sdmx" for f in facts)


def test_days_keeps_trailing_periods():
    fs = EcbSource(days=1).normalize(EA, "finfield:fx_usd", EXR_CSV, "USD", "test")
    assert [f.period.end for f in fs.facts] == ["2026-07-03"]


def test_normalize_rejects_error_page():
    src = EcbSource()
    assert src.normalize(EA, "finfield:fx_usd", "<html>404</html>", "USD", "t") is None
    assert src.normalize(EA, "finfield:fx_usd", "", "USD", "t") is None


def _fake_fetcher(fail_flows=()):
    def fake(url: str) -> bytes:
        flow = url.rsplit("/data/", 1)[-1].split("/", 1)[0]
        if flow in fail_flows:
            raise OSError(f"boom: {flow}")
        return FIXTURE_BY_FLOW[flow].encode()
    return fake


def test_fetch_isolates_failed_series():
    fs = EcbSource(fetcher=_fake_fetcher(fail_flows=("EXR",))).fetch(EA)
    concepts = {f.concept for f in fs.facts}
    assert concepts == {"finfield:policy_rate", "finfield:cpi_yoy"}
    # ref is the per-series request url
    refs = {f.concept: f.source.ref for f in fs.facts}
    flow, key, _ = SERIES["finfield:policy_rate"]
    assert refs["finfield:policy_rate"] == (
        f"https://data-api.ecb.europa.eu/service/data/{flow}/{key}?format=csvdata")


def test_fetch_returns_none_when_all_series_fail():
    assert EcbSource(fetcher=_fake_fetcher(fail_flows=("FM", "EXR", "ICP"))).fetch(EA) is None


def test_fetch_all_series_green():
    fs = EcbSource(fetcher=_fake_fetcher()).fetch(EA)
    assert {f.concept for f in fs.facts} == set(SERIES)
    assert len(fs.facts) == 6
    assert all(isinstance(f.value, int) for f in fs.facts)


def test_same_input_same_cid():
    a = EcbSource(fetcher=_fake_fetcher()).fetch(EA)
    b = EcbSource(fetcher=_fake_fetcher()).fetch(EA)
    assert [f.cid for f in a.facts] == [f.cid for f in b.facts]
