"""CoinGecko crypto market adapter (free tier, no API key).

Daily close and volume per coin, normalized to per-day FinFacts. JSON is
parsed with ``parse_float=Decimal`` so upstream numbers reach the scaled-
integer model without any float round-trip.

Crypto prices are continuous, venue-dependent quantities: two nodes scraping
at different moments legitimately mint *different* facts for the same day.
That is by design — each observation is signed and published as-is, and the
field converges on a canonical value through vank voting
(``finknit.vote``), never by pretending the number was exact.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from finfacts.model import Entity, FactSet, FinFact, Period, Source, to_scaled

from .base import FactSource
from .http import get

URL = "https://api.coingecko.com/api/v3/coins/{id}/market_chart?vs_currency=usd&days={days}&interval=daily"

# symbol -> coingecko id for the liquid core; pass extra mappings for the long tail
BUILTIN_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "BNB": "binancecoin",
    "XRP": "ripple", "ADA": "cardano", "DOGE": "dogecoin", "AVAX": "avalanche-2",
    "DOT": "polkadot", "LINK": "chainlink", "TRX": "tron", "MATIC": "matic-network",
    "LTC": "litecoin", "UNI": "uniswap", "ATOM": "cosmos", "XLM": "stellar",
    "NEAR": "near", "APT": "aptos", "ARB": "arbitrum", "OP": "optimism",
}


def coin_id(entity: Entity, extra: Optional[dict] = None) -> Optional[str]:
    parts = entity.ticker.split()
    if len(parts) != 2 or parts[1] != "CRYPTO":
        return None
    sym = parts[0].upper()
    return {**BUILTIN_IDS, **(extra or {})}.get(sym)


class CoinGeckoSource(FactSource):
    kind = "coingecko-market"

    def __init__(self, cache_dir: Optional[Path] = None, days: int = 365,
                 id_map: Optional[dict] = None):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.days = days
        self.id_map = id_map or {}

    def covers(self, entity: Entity) -> bool:
        return coin_id(entity, self.id_map) is not None

    def fetch(self, entity: Entity) -> Optional[FactSet]:
        cg_id = coin_id(entity, self.id_map)
        if cg_id is None:
            return None
        url = URL.format(id=cg_id, days=self.days)
        cache = self.cache_dir / f"{cg_id}.json" if self.cache_dir else None
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
        doc = json.loads(raw, parse_float=Decimal)
        return self.normalize(entity, doc, url)

    def normalize(self, entity: Entity, doc: dict, ref: str) -> Optional[FactSet]:
        prices = doc.get("prices") or []
        volumes = {row[0]: row[1] for row in doc.get("total_volumes") or [] if len(row) == 2}
        if not prices:
            return None
        today = date.today().isoformat()
        fs = FactSet(entity=entity)
        seen_days: set[str] = set()
        for row in prices:
            if len(row) != 2 or row[1] is None:
                continue
            ts_ms, price = row
            day = datetime.fromtimestamp(int(ts_ms) // 1000, tz=timezone.utc).date().isoformat()
            if day in seen_days:  # market_chart appends a current-moment point
                continue          # for today; keep only the 00:00 UTC daily row
            seen_days.add(day)
            value, scale = to_scaled(price)
            fs.add(FinFact(
                entity_id=entity.entity_id, concept="finfield:close",
                value=value, scale=scale, unit="USD",
                period=Period(end=day),
                source=Source(kind=self.kind, ref=ref, fetched=today),
            ))
            vol = volumes.get(ts_ms)
            if vol:
                v, s = to_scaled(vol)
                fs.add(FinFact(
                    entity_id=entity.entity_id, concept="finfield:volume",
                    value=v, scale=s, unit="USD",
                    period=Period(end=day),
                    source=Source(kind=self.kind, ref=ref, fetched=today),
                ))
        return fs.dedupe() if fs.facts else None
