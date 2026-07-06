"""Stooq end-of-day price adapter.

Stooq (stooq.com) serves free daily OHLCV history as plain CSV, no API key.
FinField normalizes close and volume into per-day FinFacts. CSV carries exact
decimal strings, so scaled-integer conversion is lossless; nodes fetching on
the same day mint identical CIDs (Source.fetched is part of the record), and
cross-day disagreement resolves through vank consensus, not CID equality.
Note: LSE ("GB") rows follow the exchange convention and are quoted in pence
(GBX), not pounds.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from finfacts.model import Entity, FactSet, FinFact, Period, Source, to_scaled

from .base import FactSource
from .http import get

URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"

# stooq suffix per FinField country code (extend as coverage grows)
SUFFIX = {"US": "us", "DE": "de", "GB": "uk", "JP": "jp", "HU": "hu", "PL": ""}
CURRENCY = {"US": "USD", "DE": "EUR", "GB": "GBX", "JP": "JPY", "HU": "HUF", "PL": "PLN"}


def stooq_symbol(entity: Entity) -> Optional[str]:
    parts = entity.ticker.split()
    if len(parts) != 2:
        return None
    sym, cc = parts[0].lower().replace("/", "-").replace(".", "-"), parts[1]
    if cc not in SUFFIX:
        return None
    suffix = SUFFIX[cc]
    return f"{sym}.{suffix}" if suffix else sym


class StooqSource(FactSource):
    kind = "stooq-eod"

    def __init__(self, cache_dir: Optional[Path] = None, days: int = 400):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.days = days  # keep only the trailing N daily rows

    def covers(self, entity: Entity) -> bool:
        return stooq_symbol(entity) is not None

    def fetch(self, entity: Entity) -> Optional[FactSet]:
        symbol = stooq_symbol(entity)
        if symbol is None:
            return None
        url = URL.format(symbol=symbol)
        cache = self.cache_dir / f"{symbol}.csv" if self.cache_dir else None
        if cache and cache.exists():
            raw = cache.read_bytes()
        else:
            try:
                raw = get(url)
            except Exception:
                return None
            if cache:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(raw)
        return self.normalize(entity, raw.decode("utf-8", "replace"), url)

    def normalize(self, entity: Entity, csv_text: str, ref: str) -> Optional[FactSet]:
        lines = [ln for ln in csv_text.strip().splitlines() if ln]
        if not lines or not lines[0].startswith("Date,"):
            return None  # stooq answers errors in HTML/prose
        currency = CURRENCY.get(entity.ticker.split()[-1], "USD")
        today = date.today().isoformat()
        fs = FactSet(entity=entity)
        for line in lines[1:][-self.days:]:
            cols = line.split(",")
            if len(cols) < 5:
                continue
            day, close = cols[0], cols[4]
            volume = cols[5] if len(cols) > 5 else ""
            try:
                value, scale = to_scaled(close)
            except Exception:
                continue
            fs.add(FinFact(
                entity_id=entity.entity_id, concept="finfield:close",
                value=value, scale=scale, unit=currency,
                period=Period(end=day),
                source=Source(kind=self.kind, ref=ref, fetched=today),
            ))
            if volume and volume != "0":
                try:
                    v, s = to_scaled(volume)
                except Exception:
                    continue
                fs.add(FinFact(
                    entity_id=entity.entity_id, concept="finfield:volume",
                    value=v, scale=s, unit="shares",
                    period=Period(end=day),
                    source=Source(kind=self.kind, ref=ref, fetched=today),
                ))
        return fs.dedupe() if fs.facts else None
