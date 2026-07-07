"""SEC EDGAR bulk mode: the whole XBRL corpus in one pass, no per-company HTTP.

The SEC's companyfacts.zip (~1.4 GB) carries every reporter's full XBRL
history (20k+ entities). ``iter_bulk`` streams (cik, doc) pairs from the
archive without extracting it; ``latest_core_facts`` reduces one document to
the FinField core pack — the latest observation per core concept, each with
its accession provenance. ``core_history_facts`` keeps the trailing quarters
and the trailing fiscal-year durations instead, so TTM derives have a full
window to sum over (FinField/feed #1): SEC reports Q4 inside the FY duration
rather than as a standalone quarter, so without the annuals the fourth
quarter — and therefore every TTM point — is unreachable.

Field-tested on the full 2026-07 archive: 6,500 tickered reporters →
93,813 records, zero errors (the other ~13.5k entries are funds/trusts
without a listed ticker). See FinField/feed for the resulting corpus.
"""
from __future__ import annotations

import json
import zipfile
from datetime import date
from pathlib import Path
from typing import Iterator, Optional

from finfacts.model import Entity, FactSet, FinFact, Period, Source, to_scaled

BULK_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"

# The core pack: identity, scale, income, balance, cashflow, per-share, float.
# dei:EntityPublicFloat is the free-float market cap (Fama-French base);
# shares outstanding are integers tied to their observation date.
CORE_CONCEPTS = frozenset(
    {
        "dei:EntityCommonStockSharesOutstanding",
        "dei:EntityPublicFloat",
        "us-gaap:CommonStockSharesOutstanding",
        "us-gaap:CommonStockSharesIssued",
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "us-gaap:Revenues",
        "us-gaap:SalesRevenueNet",
        "us-gaap:NetIncomeLoss",
        "us-gaap:OperatingIncomeLoss",
        "us-gaap:OperatingExpenses",
        "us-gaap:CostOfRevenue",
        "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
        "us-gaap:Assets",
        "us-gaap:Liabilities",
        "us-gaap:StockholdersEquity",
        "us-gaap:CashAndCashEquivalentsAtCarryingValue",
        "us-gaap:EarningsPerShareBasic",
        "us-gaap:EarningsPerShareDiluted",
    }
)

# canonical CBOR (the knitweb weave path) encodes an int in one 64-bit head:
# unsigned 0..2**64-1, negative -2**64..-1. One absurd filing value outside
# that range used to abort the WHOLE company at weave time (EBAY, CIK
# 1065088), so both reducers skip the observation instead.
CBOR_INT_MIN = -(1 << 64)
CBOR_INT_MAX = (1 << 64) - 1
MAX_SCALE = 30

# a fiscal quarter is at most ~92 days; must match the finfacts.derive
# quarterly guard so every kept duration is usable by the TTM window
MAX_QUARTER_DAYS = 100

# a fiscal year spans ~365 days (52/53-week calendars run 364/371); must
# match the finfacts.derive annual window so every kept FY duration is
# usable by its Q4 synthesis (FY - Q1 - Q2 - Q3)
MIN_FY_DAYS = 340
MAX_FY_DAYS = 380


def iter_bulk(zip_path: Path) -> Iterator[tuple[int, dict]]:
    """Stream (cik, companyfacts_doc) from the SEC bulk archive."""
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if not name.startswith("CIK") or not name.endswith(".json"):
                continue
            try:
                cik = int(name[3:-5])
                doc = json.loads(z.read(name))
            except (ValueError, json.JSONDecodeError):
                continue
            yield cik, doc


def _scaled_or_none(val) -> Optional[tuple[int, int]]:
    """(value, scale) for one filing value, or None when corrupt/unencodable.

    The bignum guard both reducers share: a scaled int outside the 64-bit
    CBOR range can never weave, so the observation is dropped here instead
    of killing the company's whole factset downstream (EBAY, CIK 1065088).
    """
    try:
        value, scale = to_scaled(val)
    except Exception:
        return None
    if not CBOR_INT_MIN <= value <= CBOR_INT_MAX or scale > MAX_SCALE:
        return None
    return value, scale


def latest_core_facts(entity: Entity, doc: dict, fetched: str) -> FactSet:
    """Latest observation per (core concept, unit) from one companyfacts doc."""
    fs = FactSet(entity=entity)
    for taxonomy, concepts in doc.get("facts", {}).items():
        for concept, body in concepts.items():
            qname = f"{taxonomy}:{concept}"
            if qname not in CORE_CONCEPTS:
                continue
            for unit, observations in body.get("units", {}).items():
                best = None
                for ob in observations:
                    if ob.get("val") is None or not ob.get("end"):
                        continue
                    key = (ob["end"], ob.get("accn", ""))
                    if best is None or key > (best["end"], best.get("accn", "")):
                        best = ob
                if best is None:
                    continue
                scaled = _scaled_or_none(best["val"])
                if scaled is None:
                    continue
                value, scale = scaled
                fs.add(
                    FinFact(
                        entity_id=entity.entity_id,
                        concept=qname,
                        value=value,
                        scale=scale,
                        unit=unit,
                        period=Period(
                            end=best["end"],
                            start=best.get("start"),
                            fiscal_year=best.get("fy"),
                            fiscal_period=best.get("fp"),
                        ),
                        source=Source(kind="sec-companyfacts", ref=best.get("accn", ""), fetched=fetched),
                    )
                )
    return fs


def _duration_days(start: str, end: str) -> int:
    """Days between two ISO dates; -1 when either date is malformed."""
    try:
        return (date.fromisoformat(end) - date.fromisoformat(start)).days
    except ValueError:
        return -1


def _keep_latest(kept: dict, key, row: tuple) -> None:
    """Latest accession wins per key (restatements refile the same period)."""
    prev = kept.get(key)
    if prev is None or row[0].get("accn", "") > prev[0].get("accn", ""):
        kept[key] = row


def _same_fiscal_year(a: dict, b: dict) -> bool:
    """True when two FY-duration rows are the same fiscal year restated.

    Same ``fy`` tag plus ranges overlapping by more than half the longer
    row: refiled annuals shift start/end by a day or two (52/53-week
    calendars), while a different year never overlaps its neighbour by
    half, and a sub-year FY-tagged comparative never swallows the real
    annual. Both rows' dates already parsed in ``_duration_days``.
    """
    if a.get("fy") != b.get("fy"):
        return False
    overlap = (
        min(date.fromisoformat(a["end"]), date.fromisoformat(b["end"]))
        - max(date.fromisoformat(a["start"]), date.fromisoformat(b["start"]))
    ).days
    return 2 * overlap > max(
        _duration_days(a["start"], a["end"]), _duration_days(b["start"], b["end"])
    )


def _trailing_years(rows, annuals: int) -> list[tuple]:
    """Fuzzy-dedupe FY rows to one per fiscal year, then the trailing N."""
    years: list[tuple] = []
    for row in sorted(rows, key=lambda r: (r[0]["end"], r[0]["start"], r[0].get("accn", ""))):
        for i, kept in enumerate(years):
            if _same_fiscal_year(kept[0], row[0]):
                if row[0].get("accn", "") > kept[0].get("accn", ""):
                    years[i] = row
                break
        else:
            years.append(row)
    return sorted(years, key=lambda r: (r[0]["end"], r[0]["start"]))[-annuals:]


def core_history_facts(
    entity: Entity, doc: dict, fetched: str, quarters: int = 8, annuals: int = 8
) -> FactSet:
    """Trailing quarterly + annual history per (core concept, unit) from one doc.

    Complements ``latest_core_facts`` (the freshness pack) with the derive
    pack: per (concept, unit) the trailing ``quarters`` fiscal-quarter
    duration observations — restatements deduped per (start, end), latest
    accession wins — plus the trailing ``annuals`` fiscal-year durations,
    plus the trailing ``quarters`` distinct instant dates (observation dates
    feed downstream staleness guards). SEC reports Q4 as the FY duration,
    not a standalone quarter, so the annuals are what let finfacts.derive
    synthesize Q4 and close the TTM window. A duration is an annual when it
    is FY-tagged or spans a fiscal year (year-long comparatives in 10-Qs
    carry the filing's Q1..Q3 tag) — but never when it is quarter-length,
    so a Q4-frame filed under an FY tag cannot masquerade as a year; annual
    restatements dedupe fuzzily per fiscal year (same ``fy`` + overlap over
    half, latest accession wins). YTD frames carry the same Q1..Q4 tags but
    span 6-9 months, so they fail both the quarter and the annual gate; only
    observations that survive the corrupt-value guard compete for trailing
    slots.
    """
    fs = FactSet(entity=entity)
    for taxonomy, concepts in doc.get("facts", {}).items():
        for concept, body in concepts.items():
            qname = f"{taxonomy}:{concept}"
            if qname not in CORE_CONCEPTS:
                continue
            for unit, observations in body.get("units", {}).items():
                durations: dict[tuple[str, str], tuple] = {}
                fy_durations: dict[tuple[str, str], tuple] = {}
                instants: dict[str, tuple] = {}
                for ob in observations:
                    if ob.get("val") is None or not ob.get("end"):
                        continue
                    scaled = _scaled_or_none(ob["val"])
                    if scaled is None:
                        continue
                    row = (ob, *scaled)
                    if ob.get("start"):
                        days = _duration_days(ob["start"], ob["end"])
                        if ob.get("fp") in ("Q1", "Q2", "Q3", "Q4") and 0 < days <= MAX_QUARTER_DAYS:
                            _keep_latest(durations, (ob["start"], ob["end"]), row)
                        elif days > MAX_QUARTER_DAYS and (
                            ob.get("fp") == "FY" or MIN_FY_DAYS <= days <= MAX_FY_DAYS
                        ):
                            _keep_latest(fy_durations, (ob["start"], ob["end"]), row)
                    else:
                        _keep_latest(instants, ob["end"], row)
                trailing = sorted(durations.values(), key=lambda r: (r[0]["end"], r[0]["start"]))[-quarters:]
                trailing += _trailing_years(fy_durations.values(), annuals)
                trailing += sorted(instants.values(), key=lambda r: r[0]["end"])[-quarters:]
                for ob, value, scale in trailing:
                    fs.add(
                        FinFact(
                            entity_id=entity.entity_id,
                            concept=qname,
                            value=value,
                            scale=scale,
                            unit=unit,
                            period=Period(
                                end=ob["end"],
                                start=ob.get("start"),
                                fiscal_year=ob.get("fy"),
                                fiscal_period=ob.get("fp"),
                            ),
                            source=Source(kind="sec-companyfacts", ref=ob.get("accn", ""), fetched=fetched),
                        )
                    )
    return fs.dedupe()
