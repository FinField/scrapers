# finscrapers

Source adapters that turn open financial data into deterministic
[finfacts](https://github.com/FinField/facts) — scaled integers, per-fact
provenance, byte-identical CIDs on every node that reads the same upstream
bytes.

## Ready scrapers

| kind | coverage | rate / ToS notes |
| --- | --- | --- |
| `sec-companyfacts` | US fundamentals — every XBRL fact filed by every US-listed reporter (audited, full history, per-fact accession provenance), public-domain JSON from data.sec.gov | SEC asks for <=10 req/s and a descriptive User-Agent (both built in: default throttle 0.12 s, UA with contact); `companyfacts.zip` bulk mode for full-universe ingest |
| `stooq-eod` | Equity end-of-day close/volume — US, DE, GB, JP, HU, PL composite tickers, plain CSV with exact decimal strings | stooq.com free tier, no API key; be polite, cache aggressively. **Currently blocked**: stooq fronts its CSV endpoint with a JS proof-of-work browser-verification wall — we do not circumvent bot walls, so this source returns nothing until stooq lifts it or publishes an API. See `alphavantage-eod` below for a compliant fallback. |
| `alphavantage-eod` | Equity end-of-day close/volume, **US-listed tickers only** — unadjusted daily close, exact decimal strings | Alpha Vantage free tier, **requires an API key** (`ALPHA_VANTAGE_API_KEY` env var or `api_key=` kwarg) under [documented terms](https://www.alphavantage.co/terms_of_service/); free-tier throughput is small (single digits-to-dozens of requests/day per key) — opt-in only, `covers()` returns `False` with no key configured so a run without one simply skips it |
| `coingecko-market` | Crypto daily close/volume (USD) for the liquid core (`BTC CRYPTO`, `ETH CRYPTO`, …; pass `id_map` for the long tail); JSON parsed with `parse_float=Decimal`, no float round-trip | CoinGecko free tier, no API key, low rate limits — cache and space out calls |

Crypto (and any cross-venue) prices are continuous quantities: two nodes
scraping at different moments legitimately mint different facts for the same
day. Each observation is published as-is; the field converges through vank
voting in [finknit.vote](https://github.com/FinField/knit), never by
pretending the number was exact.

## Adding a scraper: the FactSource contract

```python
from finscrapers import FactSource

class MySource(FactSource):
    kind = "my-source"                                  # unique source id

    def covers(self, entity) -> bool: ...               # can I supply facts for it?
    def fetch(self, entity):                            # -> FactSet | None
        ...  # normalize upstream numbers via to_scaled — never floats
```

Register it in `finscrapers.registry.READY` (or pass your own dict to the
runner). `all_sources(cache_dir)` instantiates every ready scraper, each with
its own cache subdirectory.

## Install & fetch

```bash
pip install "finscrapers @ git+https://github.com/FinField/scrapers"   # pulls finfacts
```

```python
from pathlib import Path
from finscrapers import SecEdgarSource
from finfacts.model import Entity

src = SecEdgarSource(cache_dir=Path("~/.cache/finfield/sec").expanduser())
fs = src.fetch(Entity(ticker="AAPL US", cik="320193"))
print(len(fs.facts), fs.facts[0].cid)   # audited XBRL facts, ff1:… CIDs
```

On [5mart.ml](https://5mart.ml)/finfield these scrapers run unattended as
knitting agents via [agents](https://github.com/FinField/agents): each agent
fetches, derives, and weaves signed facts into the pulse fabric on a schedule.

Part of the [FinField](https://github.com/FinField) field: [facts](https://github.com/FinField/facts) ·
[knit](https://github.com/FinField/knit) · [agents](https://github.com/FinField/agents) ·
[signals](https://github.com/FinField/signals) · [crypto](https://github.com/FinField/crypto)
