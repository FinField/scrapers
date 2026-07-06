"""Wikidata entity-facts adapter (CC0, no API key).

Global identity facts the filings sources don't carry: founding date and
employee headcount, for any entity Wikidata can resolve by open identifier
(SEC CIK P5531 or LEI P1278 — so this works worldwide, not US-only).

Dates become integers: a founding date is published as its julian day number
(concept ``wikidata:founded``, unit ``jdn``), so the time axis joins across
datasets on integer arithmetic and the value survives the float-free
canonical path. The ISO date itself rides along as ``period.end``.
Employee counts are per-observation facts: one fact per dated statement
(``P585`` qualifier), because headcounts change over time and must stay
coupled to their date.

String identity (legal name, country) is Entity/universe territory, not
FinFact territory — facts stay integer-only.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from finfacts.model import Entity, FactSet, FinFact, Period, Source, to_scaled

from .base import FactSource
from .http import get

SPARQL_URL = "https://query.wikidata.org/sparql?format=json&query={query}"

QUERY = """SELECT ?item ?founded ?employees ?employeesDate WHERE {{
  ?item wdt:{prop} "{value}" .
  OPTIONAL {{ ?item wdt:P571 ?founded . }}
  OPTIONAL {{ ?item p:P1128 ?empStmt .
             ?empStmt ps:P1128 ?employees .
             OPTIONAL {{ ?empStmt pq:P585 ?employeesDate . }} }}
}} LIMIT 50"""


def julian_day(iso_date: str) -> int:
    """Julian day number of an ISO date (proleptic Gregorian, integer)."""
    y, m, d = (int(p) for p in iso_date.split("-"))
    a = (14 - m) // 12
    y2 = y + 4800 - a
    m2 = m + 12 * a - 3
    return d + (153 * m2 + 2) // 5 + 365 * y2 + y2 // 4 - y2 // 100 + y2 // 400 - 32045


def _iso(wd_time: str) -> Optional[str]:
    """'+1976-04-01T00:00:00Z' -> '1976-04-01' (positive-year dates only)."""
    t = wd_time.lstrip("+")
    if t.startswith("-"):
        return None  # BCE founding dates: out of scope for the jdn axis
    day = t.split("T", 1)[0]
    parts = day.split("-")
    if len(parts) != 3:
        return None
    y, m, d = parts
    # Wikidata pads unknown month/day as 00 at year/month precision
    m = m if m != "00" else "01"
    d = d if d != "00" else "01"
    return f"{y}-{m}-{d}"


class WikidataSource(FactSource):
    kind = "wikidata-entity"

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = Path(cache_dir) if cache_dir else None

    def covers(self, entity: Entity) -> bool:
        return bool(entity.cik or entity.lei)

    def _query(self, entity: Entity) -> tuple[str, str]:
        if entity.cik:
            return "P5531", f"{int(entity.cik):010d}"  # Wikidata stores CIK zero-padded
        return "P1278", entity.lei

    def fetch(self, entity: Entity) -> Optional[FactSet]:
        if not self.covers(entity):
            return None
        prop, value = self._query(entity)
        cache = self.cache_dir / f"{prop}-{value}.json" if self.cache_dir else None
        if cache and cache.exists():
            raw = cache.read_bytes()
        else:
            url = SPARQL_URL.format(query=quote(QUERY.format(prop=prop, value=value)))
            try:
                raw = get(url)
            except Exception:
                return None
            if cache:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(raw)
        return self.normalize(entity, json.loads(raw, parse_float=Decimal))

    def normalize(self, entity: Entity, doc: dict) -> Optional[FactSet]:
        rows = doc.get("results", {}).get("bindings", [])
        if not rows:
            return None
        today = date.today().isoformat()
        qid = rows[0].get("item", {}).get("value", "").rsplit("/", 1)[-1]
        src = Source(kind=self.kind, ref=f"https://www.wikidata.org/wiki/{qid}", fetched=today)
        fs = FactSet(entity=entity)

        founded = next((r["founded"]["value"] for r in rows if "founded" in r), None)
        if founded and (iso := _iso(founded)):
            fs.add(FinFact(
                entity_id=entity.entity_id, concept="wikidata:founded",
                value=julian_day(iso), scale=0, unit="jdn",
                period=Period(end=iso), source=src,
            ))

        for r in rows:
            if "employees" not in r:
                continue
            try:
                value, scale = to_scaled(r["employees"]["value"])
            except Exception:
                continue
            when = r.get("employeesDate", {}).get("value")
            end = (_iso(when) if when else None) or today
            fs.add(FinFact(
                entity_id=entity.entity_id, concept="wikidata:employees",
                value=value, scale=scale, unit="employees",
                period=Period(end=end), source=src,
            ))
        return fs.dedupe() if fs.facts else None
