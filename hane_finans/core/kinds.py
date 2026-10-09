"""Account types, account kinds and liquidity tiers.

Account paths are colon-separated; the first segment is one of the five roots
below and fixes the account type. The kind (``vadesiz``, ``kredi_karti``…) of an
asset or liability account gives its default liquidity tier or debt term.
"""

from __future__ import annotations

from dataclasses import dataclass

from hane_finans.core.text import fold

ASSET, LIABILITY, EQUITY, INCOME, EXPENSE = "varlik", "borc", "ozkaynak", "gelir", "gider"

ROOTS = {
    "Varlık": ASSET,
    "Borç": LIABILITY,
    "Özkaynak": EQUITY,
    "Gelir": INCOME,
    "Gider": EXPENSE,
}
ROOT_OF_TYPE = {t: name for name, t in ROOTS.items()}
_ROOT_BY_FOLD = {fold(name): name for name in ROOTS} | {"varliklar": "Varlık", "borclar": "Borç"}

BALANCE_SHEET_TYPES = (ASSET, LIABILITY)

TIERS = {
    0: "Anında",
    1: "Kısa sürede (≤ 1 hafta)",
    2: "Kısıtlı",
    3: "Likit değil",
}
SHORT, LONG = "kisa", "uzun"
TERMS = {SHORT: "Kısa vadeli", LONG: "Uzun vadeli"}


@dataclass(frozen=True, slots=True)
class Kind:
    key: str
    label: str
    type: str
    tier: int | None = None  # assets: default liquidity tier
    term: str | None = None  # liabilities: short or long term
    default_commodity: str | None = "TRY"


KINDS: dict[str, Kind] = {
    k.key: k
    for k in (
        Kind("nakit", "Nakit", ASSET, tier=0),
        Kind("vadesiz", "Vadesiz mevduat", ASSET, tier=0),
        Kind("vadeli", "Vadeli mevduat", ASSET, tier=1),
        Kind("doviz", "Döviz (hesap ya da nakit)", ASSET, tier=0, default_commodity=None),
        Kind("altin", "Altın", ASSET, tier=1, default_commodity="XAU_G"),
        Kind("fon", "Yatırım fonu", ASSET, tier=1, default_commodity=None),
        Kind("hisse", "Hisse senedi", ASSET, tier=1, default_commodity=None),
        Kind("bes", "Bireysel emeklilik (BES)", ASSET, tier=2),
        Kind("alacak", "Alacak", ASSET, tier=2),
        Kind("gayrimenkul", "Gayrimenkul", ASSET, tier=3),
        Kind("arac", "Araç", ASSET, tier=3),
        Kind("diger_varlik", "Diğer varlık", ASSET, tier=3),
        Kind("kredi_karti", "Kredi kartı", LIABILITY, term=SHORT),
        Kind("kmh", "Kredili mevduat hesabı (KMH)", LIABILITY, term=SHORT),
        Kind("kisisel_borc", "Kişilere borç", LIABILITY, term=SHORT),
        Kind("ihtiyac_kredisi", "İhtiyaç kredisi", LIABILITY, term=LONG),
        Kind("konut_kredisi", "Konut kredisi", LIABILITY, term=LONG),
        Kind("tasit_kredisi", "Taşıt kredisi", LIABILITY, term=LONG),
        Kind("diger_borc", "Diğer borç", LIABILITY, term=LONG),
    )
}

DEFAULT_KIND = {ASSET: "diger_varlik", LIABILITY: "diger_borc"}


def canonical_root(segment: str) -> str | None:
    """``"varlik"`` → ``"Varlık"``; ``None`` if the segment is not a root."""
    return _ROOT_BY_FOLD.get(fold(segment))


def kinds_for(account_type: str) -> list[Kind]:
    return [k for k in KINDS.values() if k.type == account_type]


def effective_tier(account_type: str, kind: str | None, override: int | None) -> int | None:
    """Liquidity tier of an asset account: the override, else the kind's default."""
    if account_type != ASSET:
        return None
    if override is not None:
        return override
    k = KINDS.get(kind or "")
    return k.tier if k else 3


def debt_term(kind: str | None) -> str:
    k = KINDS.get(kind or "")
    return k.term if k and k.term else LONG
