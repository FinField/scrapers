"""finscrapers — ready-to-run FinField scrapers, pure Python (stdlib only).

Every scraper implements the FactSource contract (covers/fetch) and
normalizes one upstream into provenance-carrying FinFacts. Deployed on
5mart.ml/finfield they run as knitting agents via finagents.
"""
from .base import FactSource  # noqa: F401
from .coingecko import CoinGeckoSource  # noqa: F401
from .coingecko_supply import CoinGeckoSupplySource  # noqa: F401
from .registry import READY, all_sources  # noqa: F401
from .sec_edgar import SecEdgarSource  # noqa: F401
from .stooq import StooqSource  # noqa: F401
from .wikidata import WikidataSource  # noqa: F401

__version__ = "0.1.0"
