"""CoinGecko supply adapter — coin supply as integers, always date-coupled.

Supply is the crypto analogue of shares outstanding and changes over time
(emission, burns), so every observation is coupled to its date: the fact's
``period.end`` is the UTC day of CoinGecko's ``last_updated`` stamp, never
"now". Values go through ``to_scaled`` (JSON parsed with
``parse_float=Decimal``) so fractional supplies survive without any float
round-trip; the unit is the coin's own symbol.

Like prices, supply snapshots from different moments legitimately differ;
the field converges through vank voting (``finknit.vote``), not by
pretending a moving number was exact.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

from finfacts.model import Entity, FactSet, FinFact, Period, Source, to_scaled

from .base import FactSource
from .coingecko import coin_id
from .http import get

URL = ("https://api.coingecko.com/api/v3/coins/{id}"
       "?localization=false&tickers=false&market_data=true"
       "&community_data=false&developer_data=false&sparkline=false")

CONCEPTS = (
    ("circulating_supply", "finfield:circulating_supply"),
    ("total_supply", "finfield:total_supply"),
    ("max_supply", "finfield:max_supply"),
)


class CoinGeckoSupplySource(FactSource):
    kind = "coingecko-supply"

    def __init__(self, cache_dir: Optional[Path] = None, id_map: Optional[dict] = None):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.id_map = id_map or {}

    def covers(self, entity: Entity) -> bool:
        return coin_id(entity, self.id_map) is not None

    def fetch(self, entity: Entity) -> Optional[FactSet]:
        cg_id = coin_id(entity, self.id_map)
        if cg_id is None:
            return None
        url = URL.format(id=cg_id)
        # cache per coin per day: supply moves daily, snapshots must not go stale
        stamp = date.today().isoformat()
        cache = self.cache_dir / f"{cg_id}-{stamp}.json" if self.cache_dir else None
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
        return self.normalize(entity, json.loads(raw, parse_float=Decimal), url)

    def normalize(self, entity: Entity, doc: dict, ref: str) -> Optional[FactSet]:
        md = doc.get("market_data") or {}
        updated = str(doc.get("last_updated") or "")
        day = updated.split("T", 1)[0] if "T" in updated else date.today().isoformat()
        sym = entity.ticker.split()[0].upper()
        src = Source(kind=self.kind, ref=ref, fetched=date.today().isoformat())
        fs = FactSet(entity=entity)
        for key, concept in CONCEPTS:
            raw = md.get(key)
            if raw is None:
                continue  # e.g. ETH has no max_supply
            try:
                value, scale = to_scaled(raw)
            except Exception:
                continue
            fs.add(FinFact(
                entity_id=entity.entity_id, concept=concept,
                value=value, scale=scale, unit=sym,
                period=Period(end=day), source=src,
            ))
        return fs.dedupe() if fs.facts else None
