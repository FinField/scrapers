"""Alpha Vantage end-of-day price adapter (US equities, requires a free API key).

Fallback for ``stooq-eod`` (#7): stooq.com now fronts a JS proof-of-work
browser-verification wall that blocks plain HTTP clients, and FinField does
not circumvent bot walls. Alpha Vantage publishes ``TIME_SERIES_DAILY`` under
documented API terms (https://www.alphavantage.co/terms_of_service/) with a
free, self-service API key — unlike stooq/CoinGecko this source needs a key,
so it is opt-in: with no key configured, ``covers()`` returns False and the
source is simply skipped rather than failing the run.

Coverage is US-listed tickers only (the free tier's non-US symbol coverage is
inconsistent and unverified, so it is deliberately left out rather than
guessed at). Unadjusted daily close is used — ``TIME_SERIES_DAILY_ADJUSTED``
moved behind Alpha Vantage's premium tier in 2024.

Free-tier throughput is small (single digits to dozens of requests per day,
per-key) — this is not a bulk-universe substitute for stooq, only a
compliant path to keep a ticker's series moving while stooq is blocked.
"""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Optional

from finfacts.model import Entity, FactSet, FinFact, Period, Source, to_scaled

from .base import FactSource
from .http import get

URL = (
    "https://www.alphavantage.co/query"
    "?function=TIME_SERIES_DAILY&symbol={symbol}&outputsize={outputsize}&apikey={api_key}"
)


def av_symbol(entity: Entity) -> Optional[str]:
    parts = entity.ticker.split()
    if len(parts) != 2 or parts[1] != "US":
        return None
    return parts[0]


def public_ref(symbol: str, outputsize: str) -> str:
    """Provenance URL with the API key redacted — never persist it in a fact's source.ref."""
    return URL.format(symbol=symbol, outputsize=outputsize, api_key="***")


class AlphaVantageSource(FactSource):
    kind = "alphavantage-eod"

    def __init__(self, cache_dir: Optional[Path] = None, days: int = 100,
                 api_key: Optional[str] = None):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.days = days
        self.api_key = api_key or os.environ.get("ALPHA_VANTAGE_API_KEY")

    def covers(self, entity: Entity) -> bool:
        return bool(self.api_key) and av_symbol(entity) is not None

    def fetch(self, entity: Entity) -> Optional[FactSet]:
        if not self.api_key:
            return None
        symbol = av_symbol(entity)
        if symbol is None:
            return None
        outputsize = "compact" if self.days <= 100 else "full"  # compact = last 100 rows
        url = URL.format(symbol=symbol, outputsize=outputsize, api_key=self.api_key)
        cache = self.cache_dir / f"{symbol}.json" if self.cache_dir else None
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
        doc = json.loads(raw)
        return self.normalize(entity, doc, public_ref(symbol, outputsize))

    def normalize(self, entity: Entity, doc: dict, ref: str) -> Optional[FactSet]:
        series = doc.get("Time Series (Daily)")
        if not series:
            return None  # "Error Message" / "Note" / "Information" (bad symbol, rate limit) — no data, not a crash
        today = date.today().isoformat()
        fs = FactSet(entity=entity)
        for day in sorted(series)[-self.days:]:
            row = series[day]
            close, volume = row.get("4. close"), row.get("5. volume")
            if close is None:
                continue
            try:
                value, scale = to_scaled(close)
            except Exception:
                continue
            fs.add(FinFact(
                entity_id=entity.entity_id, concept="finfield:close",
                value=value, scale=scale, unit="USD",
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
