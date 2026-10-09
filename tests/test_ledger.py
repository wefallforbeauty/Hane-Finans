"""Double-entry rules, accounts, helpers and balance queries."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from conftest import simple_book
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import select

from hane_finans.core.kinds import ASSET, EQUITY, EXPENSE, INCOME, LIABILITY
from hane_finans.core.valuation import value_positions
from hane_finans.errors import (
    AccountError,
    AccountNotFound,
    AmbiguousAccount,
    AmountError,
    DuplicateTransaction,
    LedgerError,
    UnbalancedTransaction,
)
from hane_finans.ledger.accounts import create_account, find_account, normalize_path
from hane_finans.ledger.db import init_db, make_engine, session_scope
from hane_finans.ledger.models import Account, Commodity, Transaction
from hane_finans.ledger.queries import (
    commodity_totals,
    positions_at,
    positions_over_time,
)
from hane_finans.ledger.transactions import (
    Leg,
    add_transaction,
    delete_transaction,
    opening_balance,
    record_exchange,
    record_expense,
    record_income,
    record_transfer,
)

D = date(2026, 3, 2)


@pytest.fixture
def household(session):
    create_account(session, "Varlık:Banka:Vadesiz", kind="vadesiz", owner="Ben", institution="Banka A")
    create_account(session, "Varlık:Döviz:USD", kind="doviz", commodity="USD", owner="Ben")
    create_account(session, "Varlık:Altın:Gram", kind="altin", owner="Babam")
    create_account(session, "Varlık:Banka:Ortak", kind="vadesiz")
    create_account(session, "Borç:Kredi Kartı:Bonus", kind="kredi_karti", owner="Ben")
    create_account(session, "Gider:Gıda:Market")
    create_account(session, "Gider:Gıda:Restoran")
    create_account(session, "Gelir:Maaş")
    return session


def test_init_db_seeds_and_is_idempotent(engine):
    init_db(engine)
    with session_scope(engine) as s:
        codes = set(s.scalars(select(Commodity.code)))
        paths = set(s.scalars(select(Account.path)))
    assert {"TRY", "USD", "EUR", "XAU_G"} <= codes
    assert {"Özkaynak:Açılış Bakiyeleri", "Gider:Kategorisiz", "Gelir:Kategorisiz"} <= paths


def test_normalize_path():
    assert normalize_path("varlik : Banka :  Ziraat  Vadesiz") == "Varlık:Banka:Ziraat Vadesiz"
    assert normalize_path("BORC:Kart") == "Borç:Kart"
    for bad in ("Banka:X", "Varlık", "Varlık::X", "Gider: "):
        with pytest.raises(AccountError):
            normalize_path(bad)


def test_create_account_rules(session):
    a = create_account(session, "varlik:altin:kulce", kind="altin")
    assert (a.path, a.type, a.commodity_code) == ("Varlık:altin:kulce", ASSET, "XAU_G")
    with pytest.raises(AccountError, match="zaten var"):
        create_account(session, "Varlık:ALTIN:Külçe")  # same after folding
    with pytest.raises(AccountError, match="birim gerekli"):
        create_account(session, "Varlık:Döviz:Euro", kind="doviz")
    with pytest.raises(AccountError, match="geçerli bir tür"):
        create_account(session, "Varlık:Kart", kind="kredi_karti")
    with pytest.raises(AccountError, match="Bilinmeyen birim"):
        create_account(session, "Varlık:Fon:X", kind="fon", commodity="TEFAS.X")
    with pytest.raises(AccountError, match="Likidite"):
        create_account(session, "Borç:Kredi", kind="konut_kredisi", tier=1)
    with pytest.raises(AccountError, match="Tür yalnızca"):
        create_account(session, "Gider:Kira", kind="vadesiz")
    g = create_account(session, "Gider:Kira")
    assert g.type == EXPENSE and g.commodity_code is None


def test_find_account(household):
    s = household
    assert find_account(s, "varlik:banka:vadesiz").path == "Varlık:Banka:Vadesiz"
    assert find_account(s, "market").path == "Gider:Gıda:Market"
    assert find_account(s, "bonus").path == "Borç:Kredi Kartı:Bonus"
    with pytest.raises(AmbiguousAccount) as exc:
        find_account(s, "gida")
    assert exc.value.candidates == ["Gider:Gıda:Market", "Gider:Gıda:Restoran"]
    with pytest.raises(AccountNotFound):
        find_account(s, "yok böyle")


def test_balanced_transaction_and_elided_leg(household):
    s = household
    tx = add_transaction(s, D, "Market", [Leg("Market", Decimal("450")), Leg("Bonus", None)])
    amounts = {p.account.path: p.amount for p in tx.postings}
    assert amounts == {"Gider:Gıda:Market": Decimal("450"), "Borç:Kredi Kartı:Bonus": Decimal("-450")}


def test_rules_reject_bad_transactions(household):
    s = household
    with pytest.raises(UnbalancedTransaction):
        add_transaction(s, D, "x", [Leg("Market", Decimal("450")), Leg("Vadesiz", Decimal("-400"))])
    with pytest.raises(LedgerError, match="en az iki"):
        add_transaction(s, D, "x", [Leg("Market", Decimal("1"))])
    with pytest.raises(LedgerError, match="En fazla bir"):
        add_transaction(s, D, "x", [Leg("Market", None), Leg("Vadesiz", None)])
    with pytest.raises(LedgerError, match="sıfır"):
        add_transaction(s, D, "x", [Leg("Market", Decimal(0)), Leg("Vadesiz", Decimal(0))])
    with pytest.raises(AmountError):
        add_transaction(s, D, "x", [Leg("Market", Decimal("1.001")), Leg("Vadesiz", None)])
    with pytest.raises(LedgerError, match="yalnızca USD"):
        add_transaction(s, D, "x", [Leg("Varlık:Döviz:USD", Decimal("1"), "TRY"), Leg("Vadesiz", None)])
    with pytest.raises(UnbalancedTransaction, match="dengeleyemiyor"):
        add_transaction(
            s, D, "x", [Leg("Varlık:Döviz:USD", Decimal("10")), Leg("Market", Decimal("5")), Leg("Vadesiz", None)]
        )
    with pytest.raises(LedgerError, match="Açıklama"):
        add_transaction(s, D, " ", [Leg("Market", Decimal("1")), Leg("Vadesiz", None)])


def test_open_and_closed_dates(household):
    s = household
    a = create_account(s, "Varlık:Banka:Yeni", kind="vadesiz", opened_on=D)
    with pytest.raises(LedgerError, match="açıldı"):
        add_transaction(s, D - timedelta(days=1), "x", [Leg(a, Decimal("1")), Leg("Maaş", None)])
    add_transaction(s, D, "x", [Leg(a, Decimal("1")), Leg("Maaş", None)])
    a.closed_on = D
    with pytest.raises(LedgerError, match="kapatıldı"):
        add_transaction(s, D + timedelta(days=1), "x", [Leg(a, Decimal("1")), Leg("Maaş", None)])


def test_duplicate_import_key(household):
    s = household
    add_transaction(s, D, "a", [Leg("Market", Decimal("1")), Leg("Vadesiz", None)], import_key="k1")
    with pytest.raises(DuplicateTransaction):
        add_transaction(s, D, "a", [Leg("Market", Decimal("1")), Leg("Vadesiz", None)], import_key="k1")


def test_opening_balance_signs(household):
    s = household
    opening_balance(s, "Vadesiz", Decimal("10000"), D)
    opening_balance(s, "Bonus", Decimal("2500"), D)  # a debt of 2 500
    opening_balance(s, "USD", Decimal("100"), D)
    pos = {(p.account, p.commodity): p.quantity for p in positions_at(s, D, types=(ASSET, LIABILITY, EQUITY))}
    assert pos[("Varlık:Banka:Vadesiz", "TRY")] == Decimal("10000")
    assert pos[("Borç:Kredi Kartı:Bonus", "TRY")] == Decimal("-2500")
    assert pos[("Özkaynak:Açılış Bakiyeleri", "TRY")] == Decimal("-7500")
    assert pos[("Özkaynak:Açılış Bakiyeleri", "USD")] == Decimal("-100")
    with pytest.raises(LedgerError):
        opening_balance(s, "Market", Decimal("1"), D)


def test_helpers_card_cycle_and_exchange(household):
    s = household
    opening_balance(s, "Vadesiz", Decimal("100000"), D)
    record_income(s, Decimal("50000"), "Maaş", "Vadesiz", D, "Maaş")
    record_expense(s, Decimal("1200"), "Market", "Bonus", D, "Market")  # card spending: debt grows
    record_transfer(s, Decimal("1200"), "Vadesiz", "Bonus", D, "Kart ödemesi")  # paying it off
    tx = record_exchange(s, "Vadesiz", Decimal("40000"), "Varlık:Döviz:USD", Decimal("1000"), D)
    assert len(tx.postings) == 4
    assert tx.note == "Kur: 1 USD = 40,000000 TRY"
    by_code = {}
    for p in tx.postings:
        by_code[p.commodity_code] = by_code.get(p.commodity_code, 0) + p.amount
    assert by_code == {"TRY": 0, "USD": 0}
    pos = {p.account: p.quantity for p in positions_at(s, D)}
    assert pos["Varlık:Banka:Vadesiz"] == Decimal("100000") + 50000 - 1200 - 40000
    assert "Borç:Kredi Kartı:Bonus" not in pos  # paid off: zero balances are dropped
    assert pos["Varlık:Döviz:USD"] == Decimal("1000")
    with pytest.raises(LedgerError, match="degisim"):
        record_transfer(s, Decimal("1"), "Vadesiz", "Varlık:Döviz:USD", D)
    with pytest.raises(LedgerError, match="transfer"):
        record_exchange(s, "Vadesiz", Decimal("1"), "Ortak", Decimal("1"), D)
    with pytest.raises(LedgerError, match="pozitif"):
        record_expense(s, Decimal("-5"), "Market", "Vadesiz", D, "x")
    with pytest.raises(LedgerError, match="Gider"):
        record_expense(s, Decimal("5"), "Maaş", "Vadesiz", D, "x")
    assert all(v == 0 for v in commodity_totals(s).values())


def test_positions_owner_filter_and_dates(household):
    s = household
    opening_balance(s, "Vadesiz", Decimal("100"), D)
    opening_balance(s, "Gram", Decimal("2"), D)
    opening_balance(s, "Ortak", Decimal("7"), D + timedelta(days=5))
    assert {p.account for p in positions_at(s, D, owner="babam")} == {"Varlık:Altın:Gram"}
    assert {p.account for p in positions_at(s, D + timedelta(days=5), owner="ortak")} == {"Varlık:Banka:Ortak"}
    assert {p.account for p in positions_at(s, D, owner="ortak")} == set()
    assert positions_at(s, D - timedelta(days=1)) == []


def test_delete_transaction(household):
    s = household
    tx = record_income(s, Decimal("5"), "Maaş", "Vadesiz", D, "x")
    delete_transaction(s, tx.id)
    assert s.get(Transaction, tx.id) is None
    assert positions_at(s, D) == []
    with pytest.raises(LedgerError):
        delete_transaction(s, 999)


amounts = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("50000"), places=2)
operations = st.lists(
    st.tuples(st.sampled_from(["gelir", "harcama", "kart", "transfer", "doviz", "altin"]), amounts, st.integers(0, 60)),
    min_size=1,
    max_size=25,
)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(ops=operations, probe=st.integers(0, 61))
def test_random_ledgers_stay_consistent(ops, probe):
    """For any sequence of operations:

    1. every commodity sums to zero over the whole ledger;
    2. balances read in one pass over many dates equal balances read per date;
    3. valued at the same prices, all accounts together are worth exactly zero,
       so net worth equals minus the value of equity, income and expenses.
    """
    engine = make_engine(":memory:")
    init_db(engine)
    start = date(2026, 1, 1)
    with session_scope(engine) as s:
        create_account(s, "Varlık:Banka", kind="vadesiz")
        create_account(s, "Varlık:USD", kind="doviz", commodity="USD")
        create_account(s, "Varlık:Altın", kind="altin")
        create_account(s, "Borç:Kart", kind="kredi_karti")
        create_account(s, "Gider:Genel")
        create_account(s, "Gelir:Genel")
        for kind, value, offset in ops:
            on = start + timedelta(days=offset)
            if kind == "gelir":
                record_income(s, value, "Gelir:Genel", "Varlık:Banka", on, "g")
            elif kind == "harcama":
                record_expense(s, value, "Gider:Genel", "Varlık:Banka", on, "h")
            elif kind == "kart":
                record_expense(s, value, "Gider:Genel", "Borç:Kart", on, "k")
            elif kind == "transfer":
                record_transfer(s, value, "Varlık:Banka", "Borç:Kart", on, "t")
            elif kind == "doviz":
                record_exchange(s, "Varlık:Banka", value, "Varlık:USD", (value / 40).quantize(Decimal("0.01")) or Decimal("0.01"), on)
            else:
                grams = (value / 4000).quantize(Decimal("0.0001")) or Decimal("0.0001")
                record_exchange(s, "Varlık:Banka", value, "Varlık:Altın", grams, on)

        assert all(v == 0 for v in commodity_totals(s).values())

        dates = [start + timedelta(days=k) for k in range(0, 62, 7)] + [start + timedelta(days=probe)]
        all_types = (ASSET, LIABILITY, EQUITY, INCOME, EXPENSE)
        series = positions_over_time(s, dates, types=all_types)
        for d in dates:
            assert series[d] == positions_at(s, d, types=all_types)

        at = start + timedelta(days=probe)
        book = simple_book()
        valued = value_positions(positions_at(s, at, types=all_types), book, at, max_age_days=10_000)
        total = sum((v.value for v in valued), Decimal(0))
        assert total == 0
        balance_sheet = sum((v.value for v in valued if v.position.account_type in (ASSET, LIABILITY)), Decimal(0))
        others = sum((v.value for v in valued if v.position.account_type not in (ASSET, LIABILITY)), Decimal(0))
        assert balance_sheet == -others
    engine.dispose()
