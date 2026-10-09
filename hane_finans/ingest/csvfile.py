"""CSV import: the Hane-Finans standard layout or any bank layout via column mapping.

Standard layout (``;`` separated, decimal comma, header names as below)::

    tarih;aciklama;tutar;hesap;karsi_hesap;birim;karsi_taraf;not
    2026-10-01;MİGROS KONYA;-450,00;Borç:Kredi Kartı:Bonus;;;;

``tutar`` is the signed change of ``hesap`` from the holder's point of view:
negative = money out (a purchase, including one made with a credit card),
positive = money in (salary, a card payment received). Both legs follow from
that sign, for asset and for liability accounts alike.

``karsi_hesap`` (the other side) may be empty: the categorisation rules then
pick it, and if none matches the row goes to ``Gider:Kategorisiz`` or
``Gelir:Kategorisiz``.

Every row gets an import key (hash of account, date, amount, description and
the row's occurrence number among identical rows), so importing the same or an
overlapping statement again adds nothing twice. An import is all or nothing:
if any row is invalid, nothing is written and all errors are listed.
"""

from __future__ import annotations

import csv
import hashlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from hane_finans.core.dates import parse_date
from hane_finans.core.money import parse_decimal
from hane_finans.core.text import fold
from hane_finans.errors import FinansError
from hane_finans.ingest.rules import load_rules, match
from hane_finans.ledger.accounts import find_account
from hane_finans.ledger.db import UNCATEGORISED_EXPENSE, UNCATEGORISED_INCOME
from hane_finans.ledger.models import Transaction
from hane_finans.ledger.transactions import Leg, add_transaction

FIELDS = ("tarih", "aciklama", "tutar", "giris", "cikis", "hesap", "karsi_hesap", "birim", "karsi_taraf", "not")
FALLBACK_ENCODINGS = ("utf-8-sig", "cp1254", "iso-8859-9")


@dataclass
class CsvFormat:
    delimiter: str | None = ";"
    """``None``: detect from the file."""
    decimal_sep: str | None = ","
    date_format: str | None = None
    """``strptime`` pattern such as ``%d.%m.%Y``; ``None`` accepts 2026-10-09 and 09.10.2026."""
    encoding: str | None = None
    """``None``: UTF-8, else Windows-1254 / ISO-8859-9 (common in Turkish bank exports)."""
    columns: dict[str, str] = field(default_factory=dict)
    """Field name → header in the file, for fields whose header differs."""
    skip_rows: int = 0
    """Lines before the header row (account details at the top of many exports)."""
    invert_sign: bool = False
    """For statements that list spending as positive amounts."""


def parse_column_map(text: str) -> dict[str, str]:
    """``"tarih=İşlem Tarihi,tutar=Tutar"`` → ``{"tarih": "İşlem Tarihi", "tutar": "Tutar"}``."""
    out: dict[str, str] = {}
    for part in filter(None, (p.strip() for p in text.split(","))):
        if "=" not in part:
            raise FinansError(f"Sütun eşlemesi 'alan=Başlık' biçiminde olmalı: {part!r}")
        name, header = (s.strip() for s in part.split("=", 1))
        if fold(name) not in FIELDS:
            raise FinansError(f"Bilinmeyen alan: {name!r}. Alanlar: {', '.join(FIELDS)}")
        out[fold(name)] = header
    return out


@dataclass(frozen=True, slots=True)
class Row:
    line: int
    date: date
    description: str
    amount: Decimal
    account: str | None
    counter_account: str | None
    commodity: str | None
    payee: str | None
    note: str | None


def _decode(data: bytes, encoding: str | None) -> tuple[str, str]:
    if encoding:
        return data.decode(encoding), encoding
    for enc in FALLBACK_ENCODINGS:
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise FinansError("Dosyanın karakter kodlaması anlaşılamadı; --kodlama ile belirtin.")  # pragma: no cover


def read_rows(path: Path, fmt: CsvFormat) -> tuple[list[Row], list[str], str]:
    """Parse the file. Returns rows, error messages and the encoding used."""
    text, encoding = _decode(Path(path).read_bytes(), fmt.encoding)
    lines = text.splitlines()[fmt.skip_rows :]
    if not lines:
        raise FinansError("Dosya boş.")
    delimiter = fmt.delimiter
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff("\n".join(lines[:20]), delimiters=";,\t|").delimiter
        except csv.Error as exc:
            raise FinansError("Sütun ayırıcı anlaşılamadı; --ayrac ile belirtin.") from exc
    reader = csv.reader(lines, delimiter=delimiter)
    header = next(reader)
    folded_header = {fold(h): i for i, h in enumerate(header)}

    wanted = {name: fmt.columns.get(name, name) for name in FIELDS}
    index: dict[str, int] = {}
    for name, title in wanted.items():
        if (i := folded_header.get(fold(title))) is not None:
            index[name] = i
    for name, title in fmt.columns.items():
        if name not in index:
            raise FinansError(f"'{title}' sütunu dosyada yok. Başlıklar: {', '.join(header)}")
    if "tarih" not in index or "aciklama" not in index:
        raise FinansError(f"Dosyada tarih ve aciklama sütunları gerekli. Başlıklar: {', '.join(header)}")
    if "tutar" not in index and not ({"giris", "cikis"} & index.keys()):
        raise FinansError("Dosyada 'tutar' ya da 'giris'/'cikis' sütunları gerekli.")

    rows: list[Row] = []
    errors: list[str] = []
    for n, cells in enumerate(reader, start=fmt.skip_rows + 2):
        if not any(c.strip() for c in cells):
            continue

        def cell(name: str) -> str | None:
            i = index.get(name)
            if i is None or i >= len(cells):
                return None
            value = cells[i].strip()
            return value or None

        try:
            raw_date = cell("tarih") or ""
            on = (
                datetime.strptime(raw_date, fmt.date_format).date()
                if fmt.date_format
                else parse_date(raw_date)
            )
            if "tutar" in index and cell("tutar") is not None:
                amount = parse_decimal(cell("tutar") or "", fmt.decimal_sep)
            else:
                amount = Decimal(0)
                if cell("giris"):
                    amount += abs(parse_decimal(cell("giris") or "", fmt.decimal_sep))
                if cell("cikis"):
                    amount -= abs(parse_decimal(cell("cikis") or "", fmt.decimal_sep))
            if fmt.invert_sign:
                amount = -amount
            if amount == 0:
                raise FinansError("tutar sıfır ya da boş")
            description = cell("aciklama")
            if not description:
                raise FinansError("açıklama boş")
            rows.append(
                Row(
                    line=n,
                    date=on,
                    description=description,
                    amount=amount,
                    account=cell("hesap"),
                    counter_account=cell("karsi_hesap"),
                    commodity=(cell("birim") or "").upper() or None,
                    payee=cell("karsi_taraf"),
                    note=cell("not"),
                )
            )
        except (FinansError, ValueError) as exc:
            errors.append(f"satır {n}: {exc}")
    return rows, errors, encoding


def import_key(account_path: str, row: Row, occurrence: int) -> str:
    amount = format(row.amount.normalize(), "f")
    raw = f"{fold(account_path)}|{row.date.isoformat()}|{amount}|{fold(row.description)}|{occurrence}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class PreviewRow:
    line: int
    date: date
    description: str
    amount: Decimal
    commodity: str
    account: str
    counter_account: str
    how: str
    """``dosya`` (given in the file), ``kural #n`` or ``kategorisiz``."""
    duplicate: bool


@dataclass
class ImportResult:
    path: Path
    encoding: str
    rows: list[PreviewRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    written: bool = False

    @property
    def added(self) -> int:
        return sum(1 for r in self.rows if not r.duplicate) if self.written else 0

    @property
    def duplicates(self) -> int:
        return sum(1 for r in self.rows if r.duplicate)

    @property
    def uncategorised(self) -> int:
        return sum(1 for r in self.rows if not r.duplicate and r.how == "kategorisiz")


def import_csv(
    session: Session,
    path: Path,
    fmt: CsvFormat | None = None,
    account: str | None = None,
    dry_run: bool = False,
) -> ImportResult:
    """Import ``path``. ``account`` is used for rows without a ``hesap`` value.

    With ``dry_run`` (or when any row is invalid) nothing is written.
    """
    fmt = fmt or CsvFormat()
    rows, errors, encoding = read_rows(Path(path), fmt)
    result = ImportResult(Path(path), encoding, errors=errors)
    rules = load_rules(session)
    default_account = find_account(session, account) if account else None
    uncategorised_expense = find_account(session, UNCATEGORISED_EXPENSE)
    uncategorised_income = find_account(session, UNCATEGORISED_INCOME)
    seen: Counter[tuple] = Counter()
    pending: list[tuple[Row, object, object, str]] = []

    for row in rows:
        try:
            acc = find_account(session, row.account) if row.account else default_account
            if acc is None:
                raise FinansError("hesap yok (dosyada 'hesap' sütunu ya da --hesap gerekli)")
            if row.counter_account:
                counter, how = find_account(session, row.counter_account), "dosya"
            elif rule := match(rules, " ".join(filter(None, [row.description, row.payee]))):
                counter, how = rule.account, f"kural #{rule.rule_id}"
            else:
                counter = uncategorised_expense if row.amount < 0 else uncategorised_income
                how = "kategorisiz"
            base = (acc.id, row.date, row.amount.normalize(), fold(row.description))
            seen[base] += 1
            key = import_key(acc.path, row, seen[base])
            duplicate = session.scalar(select(Transaction.id).where(Transaction.import_key == key)) is not None
            commodity = acc.commodity_code or row.commodity or "TRY"
            result.rows.append(
                PreviewRow(row.line, row.date, row.description, row.amount, commodity, acc.path, counter.path, how, duplicate)
            )
            if not duplicate:
                pending.append((row, acc, counter, key))
        except FinansError as exc:
            result.errors.append(f"satır {row.line}: {exc}")

    if result.errors or dry_run:
        return result

    source = f"csv:{Path(path).name}"[:80]
    for row, acc, counter, key in pending:
        try:
            add_transaction(
                session,
                row.date,
                row.description,
                [
                    Leg(acc, row.amount, commodity=row.commodity),
                    Leg(counter, -row.amount, commodity=acc.commodity_code or row.commodity),
                ],
                payee=row.payee,
                note=row.note,
                source=source,
                import_key=key,
            )
        except FinansError as exc:
            result.errors.append(f"satır {row.line}: {exc}")
    if result.errors:
        session.rollback()
        return result
    result.written = True
    return result
