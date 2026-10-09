"""Price lookup, valuation and net worth in four units (hand-computed examples)."""

from datetime import date
from decimal import Decimal

import pytest
from conftest import simple_book

from hane_finans.core.inflation import Deflator
from hane_finans.core.prices import TROY_OUNCE_GRAMS, PriceBook, Quote
from hane_finans.core.valuation import Position, liquidity, net_worth, value_positions


def test_latest_is_as_of():
    book = simple_book()
    assert book.latest("USD", "TRY", date(2025, 12, 31)) is None
    assert book.latest("USD", "TRY", date(2026, 1, 1)).value == 40
    assert book.latest("USD", "TRY", date(2026, 5, 31)).value == 40
    assert book.latest("USD", "TRY", date(2026, 6, 1)).value == 44


def test_resolve_direct_cross_and_base():
    book = simple_book()
    assert book.resolve("TRY", date(2026, 3, 1)).value == 1
    usd = book.resolve("USD", date(2026, 3, 1))
    assert usd.value == 40 and usd.as_of == date(2026, 1, 1)
    gold = book.resolve("XAU_G", date(2026, 7, 1))
    assert gold.value == 110 * 44
    assert [q.quote for q in gold.path] == ["USD", "TRY"]
    assert book.resolve("XAU_G", date(2026, 7, 1), quote="USD").value == 110
    assert book.resolve("TEFAS.ABC", date(2026, 7, 1)) is None


def test_resolve_prefers_the_freshest_route():
    book = PriceBook(
        [
            Quote("EUR", "TRY", date(2026, 1, 1), Decimal("45"), "eski"),
            Quote("EUR", "USD", date(2026, 3, 1), Decimal("1.2"), "yeni"),
            Quote("USD", "TRY", date(2026, 3, 1), Decimal("40"), "yeni"),
        ]
    )
    r = book.resolve("EUR", date(2026, 3, 5))
    assert r.value == Decimal("48.0") and r.as_of == date(2026, 3, 1)
    assert r.age_days(date(2026, 3, 5)) == 4


def test_troy_ounce():
    assert TROY_OUNCE_GRAMS == Decimal("31.1034768")


def positions():
    return [
        Position(1, "Varlık:Banka:TL", "varlik", "TRY", Decimal("1000"), "vadesiz", "Ben", 0),
        Position(2, "Varlık:Döviz:USD", "varlik", "USD", Decimal("100"), "doviz", "Ben", 0),
        Position(3, "Varlık:Altın", "varlik", "XAU_G", Decimal("10"), "altin", "ortak", 1),
        Position(4, "Borç:Kart", "borc", "TRY", Decimal("-300"), "kredi_karti", "Ben"),
        Position(5, "Borç:Konut", "borc", "TRY", Decimal("-5000"), "konut_kredisi", "Ben"),
    ]


def test_net_worth_in_four_units():
    """Assets 1 000 + 100·40 + 10·(100·40) = 45 000 TL; debts 5 300 TL; net 39 700 TL."""
    book = simple_book()
    at = date(2026, 3, 1)
    valued = value_positions(positions(), book, at, max_age_days=365)
    deflator = Deflator({date(2026, 3, 1): 100.0, date(2026, 4, 1): 110.0}, method="aylik")
    nw = net_worth(valued, book, at, deflator)
    assert nw.assets == Decimal("45000")
    assert nw.liabilities == Decimal("5300")
    assert nw.net.try_ == Decimal("39700")
    assert nw.net.usd == Decimal("992.5")
    assert nw.net.gold_g == Decimal("9.925")
    assert float(nw.net.real_try) == pytest.approx(39700 * 1.1)
    assert nw.converter.cpi_base == date(2026, 4, 1)
    assert nw.complete and not nw.stale


def test_missing_and_stale_prices_are_reported():
    book = simple_book()
    at = date(2026, 3, 1)
    extra = Position(6, "Varlık:Fon:ABC", "varlik", "TEFAS.ABC", Decimal("5"), "fon", "Ben", 1)
    valued = value_positions(positions() + [extra], book, at, max_age_days=7)
    nw = net_worth(valued, book, at)
    assert nw.missing == ("Varlık:Fon:ABC (TEFAS.ABC)",)
    assert not nw.complete
    assert len(nw.stale) == 2  # USD and gold prices are two months old
    assert nw.net.try_ == Decimal("39700")  # the fund is left out, not guessed
    assert nw.net.real_try is None  # no CPI given


def test_liquidity_tiers_and_debt_terms():
    book = simple_book()
    valued = value_positions(positions(), book, date(2026, 3, 1), max_age_days=365)
    liq = liquidity(valued)
    assert liq.by_tier == {0: Decimal("5000"), 1: Decimal("40000")}
    assert liq.short_term_debt == Decimal("300")
    assert liq.long_term_debt == Decimal("5000")
    assert liq.liquid_net == Decimal("44700")
