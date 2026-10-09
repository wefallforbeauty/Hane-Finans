"""A made-up household for trying the program and for tests. No real data.

``build_demo`` writes accounts, opening balances and about twenty transactions a
month: salary, rent, card spending, bills, card and loan payments, savings in
dollars and gold. Nominal amounts grow with the CPI when it is loaded, otherwise
2.5 % a month. ``synthetic_prices`` gives offline USD/TRY and gold prices.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from hane_finans.core.dates import add_months, month_end, month_start
from hane_finans.core.inflation import Deflator
from hane_finans.core.money import quantum
from hane_finans.core.prices import PriceBook, Quote
from hane_finans.ledger.accounts import create_account
from hane_finans.ledger.transactions import (
    Leg,
    add_transaction,
    opening_balance,
    record_exchange,
    record_expense,
    record_income,
    record_transfer,
)

ACCOUNTS = (
    # path, kind, commodity, owner, institution
    ("Varlık:Banka:Vadesiz TL", "vadesiz", "TRY", "Ben", "Örnek Bankası"),
    ("Varlık:Banka:Vadeli TL", "vadeli", "TRY", "Ben", "Örnek Bankası"),
    ("Varlık:Döviz:USD Hesabı", "doviz", "USD", "Ben", "Örnek Bankası"),
    ("Varlık:Altın:Gram Altın", "altin", "XAU_G", "Ben", "Örnek Bankası"),
    ("Varlık:Nakit:Cüzdan", "nakit", "TRY", "Ben", None),
    ("Varlık:BES:Emeklilik", "bes", "TRY", "Ben", "Örnek Emeklilik"),
    ("Varlık:Banka:Babamın Vadesizi", "vadesiz", "TRY", "Babam", "Başka Banka"),
    ("Borç:Kredi Kartı:Örnek Kart", "kredi_karti", "TRY", "Ben", "Örnek Bankası"),
    ("Borç:Kredi:İhtiyaç Kredisi", "ihtiyac_kredisi", "TRY", "Ben", "Örnek Bankası"),
)
CATEGORIES = (
    "Gider:Kira",
    "Gider:Gıda:Market",
    "Gider:Gıda:Restoran",
    "Gider:Fatura:Elektrik",
    "Gider:Fatura:Doğalgaz",
    "Gider:Ulaşım",
    "Gider:Giyim",
    "Gider:Sağlık",
    "Gider:Ev",
    "Gider:Faiz ve Masraflar",
    "Gelir:Maaş",
    "Gelir:Faiz",
)
OPENING = (
    ("Varlık:Banka:Vadesiz TL", "40000"),
    ("Varlık:Banka:Vadeli TL", "150000"),
    ("Varlık:Döviz:USD Hesabı", "1500"),
    ("Varlık:Altın:Gram Altın", "20"),
    ("Varlık:Nakit:Cüzdan", "3000"),
    ("Varlık:BES:Emeklilik", "60000"),
    ("Varlık:Banka:Babamın Vadesizi", "25000"),
    ("Borç:Kredi Kartı:Örnek Kart", "8000"),
    ("Borç:Kredi:İhtiyaç Kredisi", "60000"),
)


@dataclass
class DemoSummary:
    start: date
    end: date
    transactions: int
    skipped_exchanges: int


def synthetic_prices(start: date, end: date, seed: int = 7) -> list[Quote]:
    """Daily USD/TRY and gold (USD per gram) on weekdays: smooth drift plus noise."""
    rng = random.Random(seed)
    quotes: list[Quote] = []
    usd, gold = 35.0, 85.0
    d = start
    while d <= end:
        if d.weekday() < 5:
            usd *= math.exp(0.0009 + rng.gauss(0, 0.003))
            gold *= math.exp(0.0006 + rng.gauss(0, 0.009))
            quotes.append(Quote("USD", "TRY", d, Decimal(f"{usd:.4f}"), "sentetik"))
            quotes.append(Quote("XAU_G", "USD", d, Decimal(f"{gold:.6f}"), "sentetik"))
        d += timedelta(days=1)
    return quotes


def _money(value: float) -> Decimal:
    return Decimal(str(round(value, 2))).quantize(quantum(2))


def build_demo(
    session: Session,
    start: date,
    end: date,
    book: PriceBook,
    deflator: Deflator | None = None,
    seed: int = 7,
) -> DemoSummary:
    rng = random.Random(seed)
    for path, kind, commodity, owner, institution in ACCOUNTS:
        create_account(session, path, kind=kind, commodity=commodity, owner=owner, institution=institution)
    for path in CATEGORIES:
        create_account(session, path)
    count = 0
    for path, balance in OPENING:
        opening_balance(session, path, Decimal(balance), start)
        count += 1

    def scale(d: date) -> float:
        if deflator is not None:
            return deflator.level(d) / deflator.level(start)
        months = (d.year - start.year) * 12 + (d.month - start.month)
        return 1.025**months

    skipped = 0
    card_spent = Decimal(dict(OPENING)["Borç:Kredi Kartı:Örnek Kart"])
    loan_left = Decimal(dict(OPENING)["Borç:Kredi:İhtiyaç Kredisi"])
    month = month_start(start)
    while month <= end:
        def on(day: int) -> date:
            return min(month + timedelta(days=day - 1), month_end(month))

        def due(day: int) -> bool:
            return start <= on(day) <= end

        f = scale(on(1))
        if due(1):
            record_income(session, _money(70000 * f), "Gelir:Maaş", "Vadesiz TL", on(1), "Maaş")
            count += 1
        if due(3):
            record_expense(session, _money(25000 * f), "Gider:Kira", "Vadesiz TL", on(3), "Kira", payee="Ev sahibi")
            count += 1
        if due(15) and card_spent > 0:
            record_transfer(session, card_spent, "Vadesiz TL", "Örnek Kart", on(15), "Kredi kartı ödemesi")
            card_spent = Decimal(0)
            count += 1
        for _ in range(rng.randint(7, 11)):
            day = rng.randint(1, 28)
            if not due(day):
                continue
            category, base = rng.choice(
                (
                    ("Gider:Gıda:Market", 2200),
                    ("Gider:Gıda:Market", 2200),
                    ("Gider:Gıda:Restoran", 1100),
                    ("Gider:Ulaşım", 800),
                    ("Gider:Giyim", 2500),
                    ("Gider:Sağlık", 1500),
                    ("Gider:Ev", 2000),
                )
            )
            amount = _money(base * f * rng.uniform(0.6, 1.6))
            record_expense(session, amount, category, "Örnek Kart", on(day), category.split(":")[-1])
            card_spent += amount
            count += 1
        winter = month.month in (12, 1, 2, 3)
        for category, base, day in (("Gider:Fatura:Elektrik", 1200, 10), ("Gider:Fatura:Doğalgaz", 3500 if winter else 500, 12)):
            if due(day):
                record_expense(session, _money(base * f * rng.uniform(0.85, 1.15)), category, "Vadesiz TL", on(day), category.split(":")[-1])
                count += 1
        if due(20) and loan_left > 0:
            principal = min(Decimal(3000), loan_left)
            interest = _money(float(loan_left) * 0.03)
            add_transaction(
                session,
                on(20),
                "Kredi taksiti",
                [
                    Leg("İhtiyaç Kredisi", principal),
                    Leg("Gider:Faiz ve Masraflar", interest),
                    Leg("Vadesiz TL", -(principal + interest)),
                ],
            )
            loan_left -= principal
            count += 1
        if due(25):
            record_transfer(session, _money(2000 * f), "Vadesiz TL", "BES:Emeklilik", on(25), "BES katkı payı")
            count += 1
        if due(28):
            record_income(session, _money(150000 * 0.035 * f), "Gelir:Faiz", "Vadeli TL", on(28), "Vadeli mevduat faizi")
            count += 1
        if due(5):
            d = on(5)
            if month.month % 2:
                target, commodity, decimals, spend = "USD Hesabı", "USD", 2, 8000 * f
            else:
                target, commodity, decimals, spend = "Gram Altın", "XAU_G", 4, 8000 * f
            price = book.resolve(commodity, d)
            if price is None:
                skipped += 1
            else:
                paid = _money(spend)
                got = (paid / price.value).quantize(quantum(decimals))
                record_exchange(session, "Vadesiz TL", paid, target, got, d, f"{commodity} birikimi")
                count += 1
        month = add_months(month, 1)
    return DemoSummary(start, end, count, skipped)
