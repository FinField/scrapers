"""ESEF adapter — European (and UKSEF) annual filings from filings.xbrl.org.

filings.xbrl.org indexes every ESEF filing **by LEI**, so joining against the
FinField universe is free wherever an entity carries one. Each filing ships an
xBRL-JSON (OIM) document whose fact values are exact decimal strings; they
reach the scaled-integer model without any float round-trip.

Coverage: audited IFRS fundamentals (ifrs-full:* concepts) for EU/EEA-listed
reporters — the owner's "not US-only" requirement. Consolidated facts only in
v1: any fact carrying extra dimensions beyond the OIM core four
(concept/entity/period/unit) is a segment breakdown (geographic revenue etc.)
and is skipped until the model grows a dimensions field.

OIM period convention: durations and instants end at T00:00:00 of the *next*
day (end-exclusive). We map to the inclusive calendar date the filing means
(2024-01-01T00:00:00 -> 2023-12-31) so periods line up with SEC facts.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from finfacts.model import Entity, FactSet, FinFact, Period, Source, to_scaled

from .base import FactSource
from .http import get

BASE = "https://filings.xbrl.org"
INDEX_URL = (BASE + "/api/filings?filter={filter}&sort=-date_added&page%5Bsize%5D={n}")

CORE_DIMS = {"concept", "entity", "period", "unit", "language"}


def _inclusive(stamp: str) -> str:
    """OIM end-exclusive T00:00:00 stamp -> inclusive calendar date."""
    dt = datetime.fromisoformat(stamp)
    if (dt.hour, dt.minute, dt.second) == (0, 0, 0):
        dt -= timedelta(days=1)
    return dt.date().isoformat()


class EsefSource(FactSource):
    kind = "esef-filings"

    def __init__(self, cache_dir: Optional[Path] = None, filings: int = 2):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.filings = filings

    def covers(self, entity: Entity) -> bool:
        return bool(entity.lei)

    # -- index ---------------------------------------------------------------
    def latest_filings(self, lei: str) -> list[dict]:
        """Newest filing per period_end (a company may re-file corrections)."""
        flt = json.dumps([{"name": "entity.identifier", "op": "eq", "val": lei}])
        raw = get(INDEX_URL.format(filter=quote(flt), n=max(self.filings * 4, 8)))
        rows = json.loads(raw).get("data", [])
        newest: dict[str, dict] = {}
        for row in rows:
            a = row.get("attributes", {})
            if not a.get("json_url") or not a.get("period_end"):
                continue
            cur = newest.get(a["period_end"])
            if cur is None or a.get("date_added", "") > cur.get("date_added", ""):
                newest[a["period_end"]] = a
        return [newest[k] for k in sorted(newest, reverse=True)[: self.filings]]

    # -- facts ---------------------------------------------------------------
    def fetch(self, entity: Entity) -> Optional[FactSet]:
        if not entity.lei:
            return None
        try:
            filings = self.latest_filings(entity.lei)
        except Exception:
            return None
        fs = FactSet(entity=entity)
        for filing in filings:
            cache = (self.cache_dir / f"{filing['fxo_id']}.json") if self.cache_dir else None
            if cache and cache.exists():
                raw = cache.read_bytes()
            else:
                try:
                    raw = get(BASE + filing["json_url"], timeout=120)
                except Exception:
                    continue
                if cache:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_bytes(raw)
            doc = json.loads(raw, parse_float=Decimal)
            self.normalize_into(fs, entity, doc, filing["fxo_id"])
        return fs.dedupe() if fs.facts else None

    def normalize_into(self, fs: FactSet, entity: Entity, doc: dict, fxo_id: str) -> None:
        today = date.today().isoformat()
        src = Source(kind=self.kind, ref=fxo_id, fetched=today)
        for fact in doc.get("facts", {}).values():
            dims = fact.get("dimensions", {})
            if set(dims) - CORE_DIMS:
                continue  # segment/member breakdowns: not in the v1 model
            unit = dims.get("unit")
            period = dims.get("period")
            if not unit or not period:
                continue  # text blocks and undated facts
            try:
                value, scale = to_scaled(fact["value"])
            except Exception:
                continue
            if "/" in period:
                start_s, end_s = period.split("/", 1)
                start = datetime.fromisoformat(start_s).date().isoformat()
                end = _inclusive(end_s)
            else:
                start, end = None, _inclusive(period)
            fs.add(FinFact(
                entity_id=entity.entity_id,
                concept=dims["concept"],
                value=value,
                scale=scale,
                unit=unit.split(":", 1)[-1].upper(),
                period=Period(start=start, end=end,
                              fiscal_year=int(end[:4]), fiscal_period="FY"),
                source=src,
            ))
