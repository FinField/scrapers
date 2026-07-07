"""Offline normalize-tests for the ESEF (filings.xbrl.org) adapter."""
from decimal import Decimal

from finfacts.model import Entity, FactSet
from finscrapers.esef import EsefSource, _inclusive

ASML = Entity(ticker="ASML NA", lei="724500Y6DUVHQD6OXN27")

DOC = {"documentInfo": {}, "facts": {
    "f1": {"value": "27558500000.0", "decimals": -5, "dimensions": {
        "concept": "ifrs-full:RevenueFromContractsWithCustomers",
        "entity": "scheme:724500Y6DUVHQD6OXN27",
        "period": "2025-01-01T00:00:00/2026-01-01T00:00:00",
        "unit": "iso4217:EUR"}},
    "f2": {"value": "1234.5", "dimensions": {
        "concept": "ifrs-full:Assets",
        "entity": "scheme:724500Y6DUVHQD6OXN27",
        "period": "2026-01-01T00:00:00",  # instant
        "unit": "iso4217:EUR"}},
    "f3-segment": {"value": "999", "dimensions": {
        "concept": "ifrs-full:RevenueFromContractsWithCustomers",
        "entity": "scheme:724500Y6DUVHQD6OXN27",
        "period": "2025-01-01T00:00:00/2026-01-01T00:00:00",
        "unit": "iso4217:EUR",
        "asml:GeographicalAxis": "asml:NetherlandsMember"}},  # geo segment -> captured
    "f5-product": {"value": "555", "dimensions": {
        "concept": "ifrs-full:RevenueFromContractsWithCustomers",
        "entity": "scheme:724500Y6DUVHQD6OXN27",
        "period": "2025-01-01T00:00:00/2026-01-01T00:00:00",
        "unit": "iso4217:EUR",
        "asml:ProductsAndServicesAxis": "asml:SystemsMember"}},  # non-geo -> skip
    "f6-mixed": {"value": "111", "dimensions": {
        "concept": "ifrs-full:RevenueFromContractsWithCustomers",
        "entity": "scheme:724500Y6DUVHQD6OXN27",
        "period": "2025-01-01T00:00:00/2026-01-01T00:00:00",
        "unit": "iso4217:EUR",
        "asml:GeographicalAxis": "asml:NetherlandsMember",
        "asml:ProductsAndServicesAxis": "asml:SystemsMember"}},  # mixed axes -> skip
    "f4-text": {"value": "Long accounting policy prose", "dimensions": {
        "concept": "ifrs-full:DisclosureOfAccountingPolicies",
        "entity": "scheme:724500Y6DUVHQD6OXN27",
        "period": "2025-01-01T00:00:00/2026-01-01T00:00:00",
        "language": "en"}},  # no unit -> skip
}}


def test_inclusive_end_dates():
    assert _inclusive("2026-01-01T00:00:00") == "2025-12-31"
    assert _inclusive("2025-07-01T12:30:00") == "2025-07-01"  # intraday stays


def test_normalize_consolidated_and_geo_segments():
    fs = FactSet(entity=ASML)
    EsefSource().normalize_into(fs, ASML, DOC, "724500Y6DUVHQD6OXN27-2025-12-31-ESEF-NL-0")
    by = {(f.concept, f.dimensions): f for f in fs.facts}
    assert len(fs.facts) == 3  # consolidated pair + geo segment; product/mixed/text skipped
    geo = by[("ifrs-full:RevenueFromContractsWithCustomers",
              (("asml:GeographicalAxis", "asml:NetherlandsMember"),))]
    assert geo.value == 999 and geo.period.end == "2025-12-31"
    assert geo.cid != by[("ifrs-full:RevenueFromContractsWithCustomers", ())].cid
    rev = by[("ifrs-full:RevenueFromContractsWithCustomers", ())]
    assert rev.decimal == Decimal("27558500000") and rev.unit == "EUR"
    assert rev.period.start == "2025-01-01" and rev.period.end == "2025-12-31"
    assert rev.period.fiscal_year == 2025 and rev.period.fiscal_period == "FY"
    assets = by[("ifrs-full:Assets", ())]
    assert assets.period.start is None and assets.period.end == "2025-12-31"
    assert assets.decimal == Decimal("1234.5")
    assert all(isinstance(f.value, int) for f in fs.facts)  # never floats
    assert all(f.source.kind == "esef-filings" and "ESEF-NL" in f.source.ref
               for f in fs.facts)


def test_covers_requires_lei():
    assert EsefSource().covers(ASML)
    assert not EsefSource().covers(Entity(ticker="AAPL US", cik="320193"))
