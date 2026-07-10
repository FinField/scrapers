"""Yahoo Finance end-of-day price adapter.

Yahoo Finance serves free daily OHLCV history via public chart API, no auth required.
Replaces stooq.py (which is now behind a JS PoW verification wall).
FinField normalizes adjusted close and volume into per-day FinFacts, same as stooq.

API: https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5y&interval=1d
Returns JSON with adjclose, volume per day (timestamp + metadata).
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from finfacts.model import Entity, FactSet, FinFact, Period, Source, to_scaled

from .base import FactSource
from .http import get

BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
RANGE = "5y"  # Keep trailing 5 years of history
INTERVAL = "1d"

# Country code → Yahoo ticker suffix mapping
YAHOO_SUFFIX = {
    "US": "",          # AAPL (no suffix)
    "GB": ".L",        # e.g. BARC.L (LSE)
    "DE": ".DE",       # e.g. BMW.DE (XETRA)
    "JP": ".T",        # e.g. 7203.T (Tokyo)
    "FR": ".PA",       # e.g. MC.PA (Euronext Paris)
    "NL": ".AS",       # e.g. ASML.AS (Euronext Amsterdam)
    "CH": ".VX",       # e.g. NOVN.VX (SIX Swiss)
    "AU": ".AX",       # e.g. CBA.AX (ASX)
    "CA": ".TO",       # e.g. RCI.TO (TSX)
    "SG": ".SI",       # e.g. C38U.SI (SGX)
}

CURRENCY = {
    "US": "USD", "GB": "GBX", "DE": "EUR", "JP": "JPY", "FR": "EUR",
    "NL": "EUR", "CH": "CHF", "AU": "AUD", "CA": "CAD", "SG": "SGD"
}


def yahoo_symbol(entity: Entity) -> Optional[str]:
    """Convert FinField entity ticker (e.g. 'AAPL US') to Yahoo symbol (e.g. 'AAPL')."""
    parts = entity.ticker.split()
    if len(parts) != 2:
        return None
    sym, cc = parts[0].upper(), parts[1]
    if cc not in YAHOO_SUFFIX:
        return None
    suffix = YAHOO_SUFFIX[cc]
    return f"{sym}{suffix}"


class YahooEodSource(FactSource):
    kind = "yahoo-eod"

    def __init__(self, cache_dir: Optional[Path] = None, days: int = 1800, rate_limit_s: float = 0.5):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.days = days  # keep trailing N days
        self.rate_limit_s = rate_limit_s  # pause between API calls (be nice to Yahoo)
        self.last_fetch_time = 0.0

    def covers(self, entity: Entity) -> bool:
        return yahoo_symbol(entity) is not None

    def fetch(self, entity: Entity) -> Optional[FactSet]:
        symbol = yahoo_symbol(entity)
        if symbol is None:
            return None

        # Respect rate limit
        elapsed = time.time() - self.last_fetch_time
        if elapsed < self.rate_limit_s:
            time.sleep(self.rate_limit_s - elapsed)

        url = f"{BASE_URL}/{symbol}?range={RANGE}&interval={INTERVAL}"
        cache = self.cache_dir / f"{symbol}.json" if self.cache_dir else None

        if cache and cache.exists():
            raw = cache.read_bytes()
        else:
            try:
                raw = get(url)
            except Exception:
                return None
            finally:
                self.last_fetch_time = time.time()

            if cache:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(raw)

        return self.normalize(entity, raw.decode("utf-8"), url)

    def normalize(self, entity: Entity, json_text: str, ref: str) -> Optional[FactSet]:
        """Parse Yahoo Finance JSON response and emit FinFacts."""
        try:
            data = json.loads(json_text)
        except json.JSONDecodeError:
            return None

        # Navigate Yahoo API response structure
        chart = data.get("chart", {})
        results = chart.get("result", [])
        if not results or not isinstance(results, list) or len(results) == 0:
            return None

        result = results[0]
        timestamps = result.get("timestamp", [])
        quotes = result.get("indicators", {}).get("quote", [])

        if not timestamps or not quotes or len(quotes) == 0:
            return None

        quote = quotes[0]
        # Yahoo may return 'close' or 'adjClose' depending on parameters
        closes = quote.get("adjClose") or quote.get("close") or []
        volumes = quote.get("volume", [])

        if not closes or len(closes) != len(timestamps):
            return None

        currency = CURRENCY.get(entity.ticker.split()[-1], "USD")
        today = date.today().isoformat()
        fs = FactSet(entity=entity)

        # Process timestamps in chronological order, keep trailing N days
        for i, ts in enumerate(timestamps[-self.days:]):
            if ts is None or i >= len(closes):
                continue

            close_val = closes[i]
            volume_val = volumes[i] if i < len(volumes) else None

            if close_val is None or close_val == 0:
                continue

            # Convert timestamp (seconds since epoch) to ISO date
            try:
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                day = dt.date().isoformat()
            except (ValueError, OSError):
                continue

            # Close price (required)
            try:
                value, scale = to_scaled(str(close_val))
            except Exception:
                continue

            fs.add(FinFact(
                entity_id=entity.entity_id, concept="finfield:close",
                value=value, scale=scale, unit=currency,
                period=Period(end=day),
                source=Source(kind=self.kind, ref=ref, fetched=today),
            ))

            # Volume (optional)
            if volume_val is not None and volume_val > 0:
                try:
                    v, s = to_scaled(str(int(volume_val)))
                except Exception:
                    continue

                fs.add(FinFact(
                    entity_id=entity.entity_id, concept="finfield:volume",
                    value=v, scale=s, unit="shares",
                    period=Period(end=day),
                    source=Source(kind=self.kind, ref=ref, fetched=today),
                ))

        return fs.dedupe() if fs.facts else None
