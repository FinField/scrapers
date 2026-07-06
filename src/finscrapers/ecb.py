"""ECB SDMX macro adapter (euro-area rates, FX, inflation — open, no key).

The ECB Data Portal serves every euro-area statistical series over a plain
SDMX REST API (data-api.ecb.europa.eu) as ``csvdata``: CSV rows carrying
KEY, TIME_PERIOD and OBS_VALUE. FinField normalizes three headline series —
the main refinancing (policy) rate, the daily USD/EUR reference rate, and
HICP year-on-year inflation — into per-period FinFacts for the macro
pseudo-entity ``EA MACRO`` that the MacroLens in FinField/lens reads.

CSV carries exact decimal strings, so scaled-integer conversion is lossless
and nodes fetching the same data mint identical CIDs. One failed series must
never kill the rest: each series is fetched, cached and normalized
independently; fetch returns None only when every series failed or was empty.
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Callable, Optional

from finfacts.model import Entity, FactSet, FinFact, Period, Source, to_scaled

from .base import FactSource
from .http import get

URL = "https://data-api.ecb.europa.eu/service/data/{flow}/{key}?format=csvdata"

# concept -> (flowRef, series key, unit)
SERIES = {
    # ECB main refinancing operations rate (fixed rate tenders), level
    "finfield:policy_rate": ("FM", "B.U2.EUR.4F.KR.MRR_FR.LEV", "percent"),
    # USD per 1 EUR, daily reference rate
    "finfield:fx_usd": ("EXR", "D.USD.EUR.SP00.A", "USD"),
    # HICP overall index, annual rate of change
    "finfield:cpi_yoy": ("ICP", "M.U2.N.000000.4.ANR", "percent"),
}

# Macro pseudo-entities this source covers (importable by the agent pool).
# Convention shared with FinField/lens MacroLens:
# Entity(ticker="EA MACRO", asset="macro"), entity_id "ticker:EA MACRO".
MACRO_REGIONS = ("EA MACRO",)


def _iso_period(time_period: str) -> Optional[str]:
    """Normalize an SDMX TIME_PERIOD to an ISO date.

    Daily 'YYYY-MM-DD' passes through; monthly 'YYYY-MM' becomes
    'YYYY-MM-01'. Anything else (quarterly, annual, junk) is skipped.
    """
    parts = time_period.split("-")
    if not all(p.isdigit() for p in parts):
        return None
    if len(parts) == 3 and len(time_period) == 10:
        return time_period
    if len(parts) == 2 and len(time_period) == 7:
        return f"{time_period}-01"
    return None


class EcbSource(FactSource):
    kind = "ecb-sdmx"

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        days: int = 400,
        fetcher: Optional[Callable[[str], bytes]] = None,
    ):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.days = days  # keep only the trailing N periods per series
        self._get = fetcher or get

    def covers(self, entity: Entity) -> bool:
        return entity.ticker in MACRO_REGIONS

    def fetch(self, entity: Entity) -> Optional[FactSet]:
        if not self.covers(entity):
            return None
        fs = FactSet(entity=entity)
        for concept, (flow, key, unit) in SERIES.items():
            url = URL.format(flow=flow, key=key)
            slug = concept.split(":", 1)[-1]
            cache = self.cache_dir / f"{slug}.csv" if self.cache_dir else None
            if cache and cache.exists():
                raw = cache.read_bytes()
            else:
                try:
                    raw = self._get(url)
                except Exception:
                    continue  # one failed series must not kill the rest
                if cache:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_bytes(raw)
            part = self.normalize(entity, concept, raw.decode("utf-8", "replace"), unit, url)
            if part:
                for fact in part.facts:
                    fs.add(fact)
        return fs.dedupe() if fs.facts else None

    def normalize(
        self, entity: Entity, concept: str, csv_text: str, unit: str, ref: str
    ) -> Optional[FactSet]:
        """Turn one csvdata series into a FactSet (offline-testable)."""
        rows = list(csv.DictReader(csv_text.splitlines()))
        today = date.today().isoformat()
        fs = FactSet(entity=entity)
        for row in rows[-self.days:]:
            end = _iso_period((row.get("TIME_PERIOD") or "").strip())
            obs = (row.get("OBS_VALUE") or "").strip()
            if end is None or not obs:
                continue
            try:
                value, scale = to_scaled(obs)
            except Exception:
                continue
            fs.add(FinFact(
                entity_id=entity.entity_id, concept=concept,
                value=value, scale=scale, unit=unit,
                period=Period(end=end),
                source=Source(kind=self.kind, ref=ref, fetched=today),
            ))
        return fs.dedupe() if fs.facts else None
