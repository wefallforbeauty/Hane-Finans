"""Exact amounts: parsing (Turkish and international formats) and formatting.

Amounts are ``decimal.Decimal`` everywhere in the ledger. Binary floats are
refused on input because 0.1 + 0.2 != 0.3 in floating point.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from hane_finans.errors import AmountError

_NOISE = re.compile(r"[\s  '₺$€£]|(?<![A-Za-z])(?:TL|TRY)(?![A-Za-z])", re.IGNORECASE)
_PLAIN = re.compile(r"\d+(?:\.\d+)?|\.\d+")


def parse_decimal(text: str | int | Decimal, decimal_sep: str | None = None) -> Decimal:
    """Parse an amount such as ``"1.234,56"``, ``"1,234.56"``, ``"-450"`` or ``"(12,5)"``.

    With ``decimal_sep`` given (``","`` or ``"."``) the other character is taken as
    the thousands separator. Without it:

    - if both appear, the one that comes last is the decimal separator;
    - a separator that appears more than once is a thousands separator;
    - a single ``,`` or ``.`` is a decimal separator, so ``"1.234"`` is 1.234.
      Pass ``decimal_sep=","`` for Turkish files where it means 1234.
    """
    if isinstance(text, bool):
        raise AmountError(f"Geçersiz tutar: {text!r}")
    if isinstance(text, Decimal):
        if not text.is_finite():
            raise AmountError(f"Geçersiz tutar: {text!r}")
        return text
    if isinstance(text, int):
        return Decimal(text)
    if isinstance(text, float):
        raise AmountError("Tutarlar float olarak verilemez; metin ya da Decimal kullanın.")
    if decimal_sep not in (None, ",", "."):
        raise AmountError(f"Ondalık ayırıcı ',' ya da '.' olmalı, '{decimal_sep}' verildi.")

    raw = str(text)
    s = raw.strip()
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1]
    s = _NOISE.sub("", s)
    if s.endswith("-"):
        negative, s = not negative, s[:-1]
    if s.startswith("+"):
        s = s[1:]
    elif s.startswith("-"):
        negative, s = not negative, s[1:]
    if not s:
        raise AmountError(f"Geçersiz tutar: {raw!r}")

    if decimal_sep is None:
        dots, commas = s.count("."), s.count(",")
        if dots and commas:
            decimal_sep = "," if s.rfind(",") > s.rfind(".") else "."
        elif commas:
            decimal_sep = "," if commas == 1 else None
        elif dots:
            decimal_sep = "." if dots == 1 else None
        else:
            decimal_sep = "."

    if decimal_sep is None:
        s = s.replace(",", "").replace(".", "")
    else:
        thousands = "." if decimal_sep == "," else ","
        s = s.replace(thousands, "").replace(decimal_sep, ".")

    if not _PLAIN.fullmatch(s):
        raise AmountError(f"Geçersiz tutar: {raw!r}")
    try:
        value = Decimal(s)
    except InvalidOperation as exc:  # pragma: no cover - the regex already guards this
        raise AmountError(f"Geçersiz tutar: {raw!r}") from exc
    return -value if negative else value


def quantum(decimals: int) -> Decimal:
    """``Decimal('0.01')`` for 2 decimals, ``Decimal('1')`` for 0."""
    return Decimal(1).scaleb(-decimals)


def check_decimals(value: Decimal, decimals: int, unit: str = "") -> None:
    """Refuse amounts with more decimal places than the unit allows."""
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and -exponent > decimals and value != value.quantize(quantum(decimals)):
        where = f" ({unit})" if unit else ""
        raise AmountError(f"{value} için en fazla {decimals} ondalık basamak kullanılabilir{where}.")


def round_half_up(value: Decimal, decimals: int = 2) -> Decimal:
    return value.quantize(quantum(decimals), rounding=ROUND_HALF_UP)


def format_amount(value: Decimal | float | int | None, decimals: int = 2, sign: bool = False) -> str:
    """Turkish number format: ``1234567.891`` → ``"1.234.567,89"``. Missing (None, NaN) → ``"—"``."""
    if value is None:
        return "—"
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    if not d.is_finite():
        return "—"
    q = round_half_up(d, decimals)
    body = f"{abs(q):,.{decimals}f}".replace(",", "\0").replace(".", ",").replace("\0", ".")
    if q < 0:
        return "-" + body
    if sign and q > 0:
        return "+" + body
    return body
