"""Scraper registry — the ready-to-run sources, one place.

``finagents`` enumerates this to run every scraper as a knitting agent;
anyone extending the field registers a new FactSource here (or passes their
own dict to the runner).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .base import FactSource
from .coingecko import CoinGeckoSource
from .coingecko_supply import CoinGeckoSupplySource
from .ecb import EcbSource
from .esef import EsefSource
from .onchain import OnchainSource
from .sec_edgar import SecEdgarSource
from .sec_submissions import SecSubmissionsSource
from .stooq import StooqSource
from .wikidata import WikidataSource

READY: dict[str, type[FactSource]] = {
    SecEdgarSource.kind: SecEdgarSource,      # US fundamentals, audited XBRL
    SecSubmissionsSource.kind: SecSubmissionsSource,  # SIC classification — feeds the sector/industry lenses
    EsefSource.kind: EsefSource,              # EU/EEA fundamentals, audited IFRS (by LEI)
    StooqSource.kind: StooqSource,            # equity end-of-day prices
    CoinGeckoSource.kind: CoinGeckoSource,    # crypto daily close/volume
    CoinGeckoSupplySource.kind: CoinGeckoSupplySource,  # crypto supply — int, date-coupled
    WikidataSource.kind: WikidataSource,      # founded (jdn) + employees — global identity facts
    EcbSource.kind: EcbSource,                # euro-area macro (rates, FX, HICP) — feeds the MacroLens
    OnchainSource.kind: OnchainSource,        # chain-native BTC/ETH facts — block-time-coupled integers
}


def all_sources(cache_dir: Optional[Path] = None) -> dict[str, FactSource]:
    """Instantiate every ready scraper, each with its own cache subdirectory."""
    base = Path(cache_dir) if cache_dir else None
    return {
        kind: cls(cache_dir=(base / kind if base else None))
        for kind, cls in READY.items()
    }
