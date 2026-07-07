"""On-chain adapter — chain-native facts, date-coupled to the block itself.

Reads public chain endpoints (no API keys):

  BTC  mempool.space `/api/v1/blocks` (tip height, difficulty, block time)
       blockchain.info `/q/totalbc` (mined supply in satoshi — the supply
       integer straight from the chain, not an aggregator's estimate)
  ETH  public JSON-RPC (`eth_getBlockByNumber "latest"`): height, base fee

Every fact's ``period.end`` is the **tip block's own timestamp date** — the
chain's clock, not ours. Values are integers by nature (satoshi, wei, block
count); difficulty goes through ``to_scaled``. This is the owner rule again:
supply-like quantities are integers, always coupled to a date.

Facts:
  finfield:block_height     blocks     BTC + ETH
  finfield:onchain_supply   BTC        satoshi-exact (scale 8)
  finfield:difficulty       pure       BTC
  finfield:base_fee         wei        ETH (EIP-1559 base fee of the tip)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from finfacts.model import Entity, FactSet, FinFact, Period, Source, to_scaled

from .base import FactSource
from .http import USER_AGENT, get, ssl_context

MEMPOOL_BLOCKS = "https://mempool.space/api/v1/blocks"
TOTALBC = "https://blockchain.info/q/totalbc"
ETH_RPC = "https://ethereum-rpc.publicnode.com"

CHAINS = ("BTC", "ETH")


def _day(unix_ts: int) -> str:
    return datetime.fromtimestamp(int(unix_ts), tz=timezone.utc).date().isoformat()


def _rpc(method: str, params: list) -> dict:
    import urllib.request

    req = urllib.request.Request(
        ETH_RPC,
        data=json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=30, context=ssl_context()) as resp:
        return json.loads(resp.read())["result"]


class OnchainSource(FactSource):
    kind = "onchain"

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = Path(cache_dir) if cache_dir else None

    def _chain(self, entity: Entity) -> Optional[str]:
        parts = entity.ticker.split()
        if len(parts) == 2 and parts[1] == "CRYPTO" and parts[0].upper() in CHAINS:
            return parts[0].upper()
        return None

    def covers(self, entity: Entity) -> bool:
        return self._chain(entity) is not None

    def fetch(self, entity: Entity) -> Optional[FactSet]:
        chain = self._chain(entity)
        if chain is None:
            return None
        try:
            if chain == "BTC":
                raw = {"blocks": json.loads(get(MEMPOOL_BLOCKS), parse_float=Decimal),
                       "totalbc": int(get(TOTALBC))}
            else:
                raw = {"block": _rpc("eth_getBlockByNumber", ["latest", False])}
        except Exception:
            return None
        return self.normalize(entity, chain, raw)

    def normalize(self, entity: Entity, chain: str, raw: dict) -> Optional[FactSet]:
        from datetime import date

        fs = FactSet(entity=entity)
        today = date.today().isoformat()

        def add(concept, value, scale, unit, day, ref):
            fs.add(FinFact(
                entity_id=entity.entity_id, concept=concept,
                value=value, scale=scale, unit=unit,
                period=Period(end=day),
                source=Source(kind=self.kind, ref=ref, fetched=today),
            ))

        if chain == "BTC":
            tip = (raw.get("blocks") or [{}])[0]
            if not tip.get("height"):
                return None
            day = _day(tip["timestamp"])
            ref = f"mempool.space/api/v1/blocks@{tip['height']}"
            add("finfield:block_height", int(tip["height"]), 0, "blocks", day, ref)
            if tip.get("difficulty") is not None:
                dv, ds = to_scaled(tip["difficulty"])
                add("finfield:difficulty", dv, ds, "pure", day, ref)
            if raw.get("totalbc"):
                # satoshi integer from the chain: value×10⁻⁸ BTC, exact
                add("finfield:onchain_supply", int(raw["totalbc"]), 8, "BTC", day,
                    f"blockchain.info/q/totalbc@{tip['height']}")
        else:
            blk = raw.get("block") or {}
            if not blk.get("number"):
                return None
            height = int(blk["number"], 16)
            day = _day(int(blk["timestamp"], 16))
            ref = f"publicnode eth_getBlockByNumber@{height}"
            add("finfield:block_height", height, 0, "blocks", day, ref)
            if blk.get("baseFeePerGas"):
                add("finfield:base_fee", int(blk["baseFeePerGas"], 16), 0, "wei", day, ref)
        return fs.dedupe() if fs.facts else None
