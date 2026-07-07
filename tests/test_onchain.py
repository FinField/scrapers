"""Offline normalize-tests for the on-chain adapter (no network)."""
from decimal import Decimal

from finfacts.model import Entity
from finscrapers.onchain import OnchainSource

BTC = Entity(ticker="BTC CRYPTO", asset="crypto")
ETH = Entity(ticker="ETH CRYPTO", asset="crypto")

BTC_RAW = {
    "blocks": [{"height": 957009, "timestamp": 1783410954,
                "difficulty": Decimal("133869853540305.4"), "tx_count": 3743}],
    "totalbc": 2005297100000000,
}
ETH_RAW = {"block": {"number": "0x184c8b9", "timestamp": "0x6a4cb2fb",
                     "baseFeePerGas": "0x5b5095a", "gasUsed": "0xd60af0"}}


def test_covers_known_chains_only():
    s = OnchainSource()
    assert s.covers(BTC) and s.covers(ETH)
    assert not s.covers(Entity(ticker="DOGE CRYPTO", asset="crypto"))
    assert not s.covers(Entity(ticker="AAPL US"))


def test_btc_block_coupled_integers():
    fs = OnchainSource().normalize(BTC, "BTC", BTC_RAW)
    by = {f.concept: f for f in fs.facts}
    h = by["finfield:block_height"]
    assert h.value == 957009 and h.scale == 0 and h.unit == "blocks"
    assert h.period.end == "2026-07-07"  # the tip block's own timestamp date
    sup = by["finfield:onchain_supply"]
    assert sup.value == 2005297100000000 and sup.scale == 8 and sup.unit == "BTC"
    assert sup.decimal == Decimal("20052971")  # satoshi-exact
    assert "totalbc@957009" in sup.source.ref
    d = by["finfield:difficulty"]
    assert d.decimal == Decimal("133869853540305.4") and d.unit == "pure"
    assert all(isinstance(f.value, int) for f in fs.facts)  # never floats


def test_eth_hex_decoding():
    fs = OnchainSource().normalize(ETH, "ETH", ETH_RAW)
    by = {f.concept: f for f in fs.facts}
    assert by["finfield:block_height"].value == 0x184C8B9
    fee = by["finfield:base_fee"]
    assert fee.value == 0x5B5095A and fee.unit == "wei" and fee.scale == 0
    assert by["finfield:block_height"].period.end == fee.period.end


def test_empty_payloads_yield_none():
    assert OnchainSource().normalize(BTC, "BTC", {"blocks": [{}]}) is None
    assert OnchainSource().normalize(ETH, "ETH", {"block": {}}) is None
