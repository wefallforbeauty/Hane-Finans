"""TCMB EVDS3 data service (free key required).

Since 2025 the service lives at ``https://evds3.tcmb.gov.tr/igmevdsms-dis/`` and
the API key goes in the HTTP header ``key`` (a request without it gets
``403 Required request header 'key' is not present``). A series request is::

    {BASE}/series=CODE1-CODE2&startDate=DD-MM-YYYY&endDate=DD-MM-YYYY&type=json

The answer is ``{"totalCount": n, "items": [{"Tarih": ..., "CODE_WITH_UNDERSCORES": "1.23", ...}]}``.
Dots in series codes become underscores, values are strings or ``null``
(holidays). Daily dates look like ``08-10-2026``; monthly ones ``2026-09`` or
``2026-9``.

Getting a key: sign up at https://evds3.tcmb.gov.tr, then copy the API key from
the profile page. Store it with ``finans ayar evds-anahtari``.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx

from hane_finans.errors import EvdsKeyError, SourceError

BASE_URL = "https://evds3.tcmb.gov.tr/igmevdsms-dis"

CPI_SERIES = "TP.TUKFIY2025.GENEL"
"""TÜFE general index, 2025=100 (TÜİK), monthly from 2005-01."""
CPI_SERIES_OLD = "TP.FG.J0"
"""TÜFE 2003=100: archived after the 2025 rebasing, ends 2026-01."""
FX_BUYING_SERIES = {"USD": "TP.DK.USD.A.YTL", "EUR": "TP.DK.EUR.A.YTL"}
"""Indicative forex buying rates, the same figures as the TCMB XML bulletin."""

_DAILY = re.compile(r"(\d{1,2})-(\d{1,2})-(\d{4})")
_MONTHLY = re.compile(r"(\d{4})-(\d{1,2})")


def series_url(codes: list[str], start: date, end: date, frequency: int | None = None) -> str:
    if not codes:
        raise ValueError("En az bir seri kodu gerekli.")
    url = f"{BASE_URL}/series={'-'.join(codes)}&startDate={start:%d-%m-%Y}&endDate={end:%d-%m-%Y}&type=json"
    if frequency is not None:
        url += f"&frequency={frequency}"
    return url


def parse_period(text: str) -> date:
    """``"08-10-2026"`` → 2026-10-08; ``"2026-9"`` and ``"2026-09"`` → 2026-09-01."""
    s = text.strip()
    if m := _DAILY.fullmatch(s):
        return date(int(m[3]), int(m[2]), int(m[1]))
    if m := _MONTHLY.fullmatch(s):
        return date(int(m[1]), int(m[2]), 1)
    raise SourceError(f"EVDS tarih biçimi tanınmadı: {text!r}")


def parse_series(payload: object, codes: list[str]) -> dict[str, list[tuple[date, Decimal]]]:
    if not isinstance(payload, dict) or "items" not in payload:
        message = payload.get("message") if isinstance(payload, dict) else None
        raise SourceError(f"EVDS beklenmeyen yanıt döndürdü: {message or str(payload)[:200]}")
    out: dict[str, list[tuple[date, Decimal]]] = {c: [] for c in codes}
    for item in payload["items"]:
        when = item.get("Tarih") or item.get("TARIH")
        if not when:
            continue
        period = parse_period(str(when))
        for code in codes:
            raw = item.get(code.replace(".", "_"))
            if raw is None or str(raw).strip() == "":
                continue
            try:
                out[code].append((period, Decimal(str(raw).strip())))
            except InvalidOperation as exc:
                raise SourceError(f"EVDS değeri okunamadı: {raw!r} ({code})") from exc
    for values in out.values():
        values.sort()
    return out


def fetch_series(
    client: httpx.Client,
    api_key: str,
    codes: list[str],
    start: date,
    end: date,
    frequency: int | None = None,
) -> dict[str, list[tuple[date, Decimal]]]:
    if not api_key:
        raise EvdsKeyError("EVDS anahtarı yok. 'finans ayar evds-anahtari ANAHTAR' ile kaydedin.")
    response = client.get(series_url(codes, start, end, frequency), headers={"key": api_key})
    if response.status_code in (401, 403):
        raise EvdsKeyError(f"EVDS anahtarı reddedildi ({response.status_code}): {response.text[:200]}")
    if response.status_code != 200:
        raise SourceError(f"EVDS {response.status_code} döndürdü: {response.text[:200]}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise SourceError("EVDS yanıtı JSON değil.") from exc
    return parse_series(payload, codes)
