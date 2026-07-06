"""SEC EDGAR bulk mode: the whole XBRL corpus in one pass, no per-company HTTP.

The SEC's companyfacts.zip (~1.4 GB) carries every reporter's full XBRL
history (20k+ entities). ``iter_bulk`` streams (cik, doc) pairs from the
archive without extracting it; ``latest_core_facts`` reduces one document to
the FinField core pack — the latest observation per core concept, each with
its accession provenance.

Field-tested on the full 2026-07 archive: 6,500 tickered reporters →
93,813 records, zero errors (the other ~13.5k entries are funds/trusts
without a listed ticker). See FinField/feed for the resulting corpus.
"""
from __future__ import annotations

import json
import zipfile
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
