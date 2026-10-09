"""Recording transactions under the double-entry rules.

Rules checked by ``add_transaction``:

1. At least two legs; no zero amounts; no more decimals than the unit allows.
2. Asset and liability accounts hold one commodity; their legs must use it.
3. In every commodity the legs add up to exactly zero. One leg may leave its
   amount empty; it then takes the balancing amount.
4. Accounts cannot be used before they are opened or after they are closed.

Exchanges between commodities (buying dollars or gold with lira) go through
per-commodity trading accounts under ``Özkaynak:Takas`` (Selinger's method), so
rule 3 holds in each commodity separately. See docs/YONTEM.md.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from hane_finans.core.kinds import ASSET, EXPENSE, INCOME, LIABILITY
from hane_finans.core.money import check_decimals, format_amount
from hane_finans.errors import DuplicateTransaction, LedgerError, UnbalancedTransaction
from hane_finans.ledger.accounts import (
    find_account,
    get_or_create_account,
    trading_account,
)
from hane_finans.ledger.db import OPENING_ACCOUNT
from hane_finans.ledger.models import Account, Commodity, Posting, Transaction


@dataclass(frozen=True, slots=True)
class Leg:
    account: str | Account
    amount: Decimal | None
    """Signed amount; ``None`` lets this leg balance the transaction."""
    commodity: str | None = None
    memo: str | None = None


def _commodity_for(session: Session, account: Account, requested: str | None) -> Commodity:
    if account.commodity_code:
        if requested and requested.upper() != account.commodity_code:
            raise LedgerError(
                f"{account.path} yalnızca {account.commodity_code} tutar; {requested.upper()} verilemez."
            )
        code = account.commodity_code
    else:
        code = (requested or "TRY").upper()
    commodity = session.get(Commodity, code)
    if commodity is None:
        raise LedgerError(f"Bilinmeyen birim: {code}")
    return commodity


def add_transaction(
    session: Session,
    on: date,
    description: str,
    legs: list[Leg],
    *,
    payee: str | None = None,
    note: str | None = None,
    source: str = "elle",
    import_key: str | None = None,
) -> Transaction:
    if not description or not description.strip():
        raise LedgerError("Açıklama boş olamaz.")
    if len(legs) < 2:
        raise LedgerError("Bir işlemin en az iki satırı olmalı.")

    resolved: list[list] = []
    for leg in legs:
        account = find_account(session, leg.account)
        if account.opened_on and on < account.opened_on:
            raise LedgerError(f"{account.path} {account.opened_on} tarihinde açıldı; {on} tarihli işlem eklenemez.")
        if account.closed_on and on > account.closed_on:
            raise LedgerError(f"{account.path} {account.closed_on} tarihinde kapatıldı; {on} tarihli işlem eklenemez.")
        commodity = _commodity_for(session, account, leg.commodity)
        if leg.amount is not None:
            if leg.amount == 0:
                raise LedgerError(f"{account.path} için tutar sıfır olamaz.")
            check_decimals(leg.amount, commodity.decimals, commodity.code)
        resolved.append([account, commodity.code, leg.amount, leg.memo])

    sums: dict[str, Decimal] = defaultdict(Decimal)
    for _, code, amount, _ in resolved:
        if amount is not None:
            sums[code] += amount
    open_legs = [r for r in resolved if r[2] is None]
    if len(open_legs) > 1:
        raise LedgerError("En fazla bir satırın tutarı boş bırakılabilir.")
    if open_legs:
        leg = open_legs[0]
        unbalanced = {c for c, s in sums.items() if s != 0}
        if unbalanced != {leg[1]}:
            raise UnbalancedTransaction(
                f"Boş tutarlı satır ({leg[0].path}, {leg[1]}) işlemi dengeleyemiyor; "
                f"dengesiz birimler: {', '.join(sorted(unbalanced)) or 'yok'}."
            )
        leg[2] = -sums[leg[1]]
        sums[leg[1]] = Decimal(0)

    unbalanced = {c: s for c, s in sums.items() if s != 0}
    if unbalanced:
        detail = ", ".join(f"{c} {format_amount(s, 6, sign=True)}" for c, s in sorted(unbalanced.items()))
        raise UnbalancedTransaction(f"İşlem dengede değil (satırların toplamı sıfır olmalı): {detail}")

    if import_key and session.scalar(select(Transaction.id).where(Transaction.import_key == import_key)):
        raise DuplicateTransaction("Bu satır daha önce içe aktarılmış.")

    tx = Transaction(
        date=on,
        description=description.strip(),
        payee=payee,
        note=note,
        source=source,
        import_key=import_key,
    )
    tx.postings = [
        Posting(account=account, commodity_code=code, amount=amount, memo=memo)
        for account, code, amount, memo in resolved
    ]
    session.add(tx)
    session.flush()
    return tx


def _balance_sheet_account(session: Session, query: str | Account, role: str) -> Account:
    account = find_account(session, query)
    if account.type not in (ASSET, LIABILITY):
        raise LedgerError(f"{role} bir varlık ya da borç hesabı olmalı: {account.path}")
    return account


def _positive(amount: Decimal, what: str = "Tutar") -> None:
    if amount <= 0:
        raise LedgerError(f"{what} pozitif olmalı.")


def signed_balance(account: Account, balance: Decimal) -> Decimal:
    """Natural balance → ledger sign: a debt of 5 000 is −5 000 on a liability."""
    return -balance if account.type == LIABILITY else balance


def opening_balance(
    session: Session, account: str | Account, balance: Decimal, on: date, description: str | None = None
) -> Transaction:
    """Starting balance of an account against ``Özkaynak:Açılış Bakiyeleri``.

    ``balance`` has the natural sign: what you hold for an asset, what you owe
    for a liability.
    """
    acc = _balance_sheet_account(session, account, "Açılış bakiyesi verilen hesap")
    opening = get_or_create_account(session, OPENING_ACCOUNT)
    return add_transaction(
        session,
        on,
        description or f"Açılış bakiyesi: {acc.path}",
        [Leg(acc, signed_balance(acc, balance)), Leg(opening, None, commodity=acc.commodity_code)],
        source="acilis",
    )


def record_expense(
    session: Session,
    amount: Decimal,
    expense_account: str | Account,
    paid_from: str | Account,
    on: date,
    description: str,
    payee: str | None = None,
) -> Transaction:
    _positive(amount)
    expense = find_account(session, expense_account)
    if expense.type != EXPENSE:
        raise LedgerError(f"Gider hesabı 'Gider:' ile başlamalı: {expense.path}")
    source = _balance_sheet_account(session, paid_from, "Ödemenin yapıldığı hesap")
    return add_transaction(
        session,
        on,
        description,
        [Leg(expense, amount, commodity=source.commodity_code), Leg(source, -amount)],
        payee=payee,
    )


def record_income(
    session: Session,
    amount: Decimal,
    income_account: str | Account,
    paid_to: str | Account,
    on: date,
    description: str,
    payee: str | None = None,
) -> Transaction:
    _positive(amount)
    income = find_account(session, income_account)
    if income.type != INCOME:
        raise LedgerError(f"Gelir hesabı 'Gelir:' ile başlamalı: {income.path}")
    target = _balance_sheet_account(session, paid_to, "Gelirin girdiği hesap")
    return add_transaction(
        session,
        on,
        description,
        [Leg(target, amount), Leg(income, -amount, commodity=target.commodity_code)],
        payee=payee,
    )


def record_transfer(
    session: Session,
    amount: Decimal,
    from_account: str | Account,
    to_account: str | Account,
    on: date,
    description: str | None = None,
) -> Transaction:
    """Move money between two accounts of the same unit, e.g. pay a card from the bank."""
    _positive(amount)
    src = _balance_sheet_account(session, from_account, "Kaynak hesap")
    dst = _balance_sheet_account(session, to_account, "Hedef hesap")
    if src.id == dst.id:
        raise LedgerError("Kaynak ve hedef aynı hesap.")
    if src.commodity_code != dst.commodity_code:
        raise LedgerError(
            f"Birimler farklı ({src.commodity_code} → {dst.commodity_code}); döviz/altın alım-satımı için 'degisim' kullanın."
        )
    return add_transaction(
        session,
        on,
        description or f"Transfer: {src.path} → {dst.path}",
        [Leg(dst, amount), Leg(src, -amount)],
    )


def record_exchange(
    session: Session,
    from_account: str | Account,
    from_amount: Decimal,
    to_account: str | Account,
    to_amount: Decimal,
    on: date,
    description: str | None = None,
) -> Transaction:
    """Pay ``from_amount`` from one account and receive ``to_amount`` of another unit.

    Four legs, two per commodity, through the trading accounts.
    """
    _positive(from_amount, "Verilen tutar")
    _positive(to_amount, "Alınan miktar")
    src = _balance_sheet_account(session, from_account, "Kaynak hesap")
    dst = _balance_sheet_account(session, to_account, "Hedef hesap")
    if src.commodity_code == dst.commodity_code:
        raise LedgerError("Birimler aynı; bunun için 'transfer' kullanın.")
    rate = from_amount / to_amount
    note = f"Kur: 1 {dst.commodity_code} = {format_amount(rate, 6)} {src.commodity_code}"
    return add_transaction(
        session,
        on,
        description or f"{dst.commodity_code} alımı ({src.commodity_code} ile)",
        [
            Leg(src, -from_amount),
            Leg(trading_account(session, src.commodity_code), from_amount),
            Leg(trading_account(session, dst.commodity_code), -to_amount),
            Leg(dst, to_amount),
        ],
        note=note,
    )


def delete_transaction(session: Session, transaction_id: int) -> Transaction:
    tx = session.get(Transaction, transaction_id)
    if tx is None:
        raise LedgerError(f"İşlem bulunamadı: #{transaction_id}")
    session.delete(tx)
    session.flush()
    return tx
