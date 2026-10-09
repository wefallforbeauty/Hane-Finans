"""Balance sheet, net worth series, chart and the demo household."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from hane_finans.core.prices import PriceBook
from hane_finans.demo import build_demo, synthetic_prices
from hane_finans.ledger.accounts import create_account
from hane_finans.ledger.queries import commodity_totals
from hane_finans.ledger.transactions import (
    opening_balance,
    record_exchange,
    record_expense,
)
from hane_finans.prices.store import upsert_price
from hane_finans.prices.update import load_bundled_cpi
from hane_finans.reports.balance_sheet import build_balance_sheet
from hane_finans.reports.chart import save_networth_chart
from hane_finans.reports.context import load_market_data
from hane_finans.reports.networth import networth_series


@pytest.fixture
def small(session):
    s = session
    load_bundled_cpi(s)
    for code, quote, day, value in (
        ("USD", "TRY", date(2026, 8, 31), "48"),
        ("USD", "TRY", date(2026, 9, 30), "49"),
        ("XAU_G", "USD", date(2026, 8, 31), "130"),
        ("XAU_G", "USD", date(2026, 9, 30), "135"),
    ):
        upsert_price(s, code, quote, day, Decimal(value), "test")
    create_account(s, "Varlık:Banka:Vadesiz", kind="vadesiz", owner="Ben")
    create_account(s, "Varlık:Döviz:USD", kind="doviz", commodity="USD", owner="Ben")
    create_account(s, "Varlık:Altın:Gram", kind="altin", owner="Babam")
    create_account(s, "Borç:Kredi Kartı:Bonus", kind="kredi_karti", owner="Ben")
    create_account(s, "Gider:Market")
    opening_balance(s, "Vadesiz", Decimal("100000"), date(2026, 8, 31))
    opening_balance(s, "Gram", Decimal("10"), date(2026, 8, 31))
    record_exchange(s, "Vadesiz", Decimal("48000"), "Varlık:Döviz:USD", Decimal("1000"), date(2026, 8, 31))
    record_expense(s, Decimal("2000"), "Market", "Bonus", date(2026, 9, 15), "Market")
    return s


def test_balance_sheet(small):
    bs = build_balance_sheet(small, date(2026, 9, 30))
    nw = bs.net_worth
    # 52 000 TL + 1 000 USD × 49 + 10 g × 135 × 49 − 2 000 TL card debt
    expected = Decimal(52000) + 49000 + Decimal(10 * 135 * 49) - 2000
    assert nw.net.try_ == expected
    assert nw.net.usd == expected / 49
    assert nw.net.gold_g == expected / (135 * 49)
    assert nw.converter.cpi_base == date(2026, 9, 1)
    assert nw.net.real_try == expected  # September 30 is after the latest CPI month's middle
    assert bs.by_owner == {"Ben": Decimal(52000 + 49000 - 2000), "Babam": Decimal(10 * 135 * 49)}
    assert bs.liquidity.short_term_debt == 2000
    assert [v.position.account for v in bs.liabilities] == ["Borç:Kredi Kartı:Bonus"]
    only_father = build_balance_sheet(small, date(2026, 9, 30), owner="Babam")
    assert only_father.net_worth.net.try_ == Decimal(10 * 135 * 49)


def test_real_try_deflates_older_dates(small):
    bs = build_balance_sheet(small, date(2026, 8, 31))
    market = bs.market
    factor = market.deflator.factor(date(2026, 8, 31))
    assert 1.005 < factor < 1.0184  # between August and September prices (+1.84 % in September)
    assert float(bs.net_worth.net.real_try) == pytest.approx(float(bs.net_worth.net.try_) * factor)


def test_networth_series_matches_balance_sheet(small):
    frame = networth_series(small, date(2026, 8, 31), date(2026, 9, 30), "ay")
    assert list(frame["tarih"]) == [date(2026, 8, 31), date(2026, 9, 30)]
    last = build_balance_sheet(small, date(2026, 9, 30)).net_worth.net
    row = frame.iloc[-1]
    assert row["tl"] == pytest.approx(float(last.try_))
    assert row["usd"] == pytest.approx(float(last.usd))
    assert row["altin_g"] == pytest.approx(float(last.gold_g))
    assert (frame["eksik_fiyat"] == 0).all()


def test_networth_series_flags_missing_prices(session):
    create_account(session, "Varlık:Döviz:USD", kind="doviz", commodity="USD")
    opening_balance(session, "USD", Decimal("10"), date(2026, 1, 1))
    frame = networth_series(session, date(2026, 1, 1), date(2026, 2, 1))
    assert (frame["eksik_fiyat"] == 1).all()
    assert frame["usd"].isna().all()


def test_empty_ledger_gives_empty_series(session):
    assert networth_series(session).empty


def test_chart_is_written(small, tmp_path):
    frame = networth_series(small, date(2026, 8, 1), date(2026, 9, 30), "hafta")
    path = save_networth_chart(frame, tmp_path / "grafik" / "net.png", date(2026, 9, 1), "deneme")
    assert path.exists() and path.stat().st_size > 10_000


def test_demo_household_is_consistent(session):
    start, end = date(2025, 1, 1), date(2025, 12, 31)
    load_bundled_cpi(session)
    for q in synthetic_prices(start - timedelta(days=7), end):
        upsert_price(session, q.commodity, q.quote, q.on, q.value, q.source)
    market = load_market_data(session)
    summary = build_demo(session, start, end, market.book, market.deflator)
    assert summary.transactions > 200 and summary.skipped_exchanges == 0
    assert all(v == 0 for v in commodity_totals(session).values())
    bs = build_balance_sheet(session, end)
    assert bs.net_worth.complete and not bs.net_worth.stale
    assert bs.net_worth.net.try_ > 0
    assert {"Ben", "Babam"} <= set(bs.by_owner)


def test_demo_without_prices_skips_exchanges(session):
    summary = build_demo(session, date(2025, 1, 1), date(2025, 3, 31), PriceBook())
    assert summary.skipped_exchanges == 3
