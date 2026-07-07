"""SEC EDGAR bulk mode: the whole XBRL corpus in one pass, no per-company HTTP.

The SEC's companyfacts.zip (~1.4 GB) carries every reporter's full XBRL
history (20k+ entities). ``iter_bulk`` streams (cik, doc) pairs from the
archive without extracting it; ``latest_core_facts`` reduces one document to
the FinField core pack — the latest observation per core concept, each with
its accession provenance. ``core_history_facts`` keeps the trailing quarters
instead, so TTM derives have a full window to sum over (FinField/feed #1).

Field-tested on the full 2026-07 archive: 6,500 tickered reporters →
93,813 records, zero errors (the other ~13.5k entries are funds/trusts
without a listed ticker). See FinField/feed for the resulting corpus.
"""
from __future__ import annotations

import json
import zipfile
from datetime import date
from pathlib import Path
from typing import Iterator

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

# a real financial number never needs more than this; anything beyond is a
# corrupt filing value that would bloat the canonical CBOR path
MAX_VALUE_BITS = 256
MAX_SCALE = 30

# a fiscal quarter is at most ~92 days; must match the finfacts.derive
# quarterly guard so every kept duration is usable by the TTM window
MAX_QUARTER_DAYS = 100


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
                try:
                    value, scale = to_scaled(best["val"])
                except Exception:
                    continue
                if abs(value).bit_length() > MAX_VALUE_BITS or scale > MAX_SCALE:
                    continue
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


def core_history_facts(entity: Entity, doc: dict, fetched: str, quarters: int = 8) -> FactSet:
    """Trailing quarterly history per (core concept, unit) from one doc.

    Complements ``latest_core_facts`` (the freshness pack) with the derive
    pack: per (concept, unit) the trailing ``quarters`` fiscal-quarter
    duration observations — restatements deduped per (start, end), latest
    accession wins — plus the trailing ``quarters`` distinct instant dates
    (observation dates feed downstream staleness guards). YTD frames carry
    the same Q1..Q4 tags but span 6-12 months, so durations are also gated
    on quarter length; only observations that survive the corrupt-value
    guard compete for trailing slots.
    """
    fs = FactSet(entity=entity)
    for taxonomy, concepts in doc.get("facts", {}).items():
        for concept, body in concepts.items():
            qname = f"{taxonomy}:{concept}"
            if qname not in CORE_CONCEPTS:
                continue
            for unit, observations in body.get("units", {}).items():
                durations: dict[tuple[str, str], tuple] = {}
                instants: dict[str, tuple] = {}
                for ob in observations:
                    if ob.get("val") is None or not ob.get("end"):
                        continue
                    try:
                        value, scale = to_scaled(ob["val"])
                    except Exception:
                        continue
                    if abs(value).bit_length() > MAX_VALUE_BITS or scale > MAX_SCALE:
                        continue
                    row = (ob, value, scale)
                    if ob.get("start"):
                        if ob.get("fp") not in ("Q1", "Q2", "Q3", "Q4"):
                            continue
                        if not 0 < _duration_days(ob["start"], ob["end"]) <= MAX_QUARTER_DAYS:
                            continue
                        key = (ob["start"], ob["end"])
                        kept = durations.get(key)
                        if kept is None or ob.get("accn", "") > kept[0].get("accn", ""):
                            durations[key] = row
                    else:
                        kept = instants.get(ob["end"])
                        if kept is None or ob.get("accn", "") > kept[0].get("accn", ""):
                            instants[ob["end"]] = row
                trailing = sorted(durations.values(), key=lambda r: (r[0]["end"], r[0]["start"]))[-quarters:]
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
