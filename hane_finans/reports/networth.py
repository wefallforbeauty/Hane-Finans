"""Net worth over time in the four units."""

from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy.orm import Session

from hane_finans.core.dates import sample_dates
from hane_finans.core.valuation import (
    DEFAULT_MAX_AGE_DAYS,
    liquidity,
    net_worth,
    value_positions,
)
from hane_finans.ledger.queries import positions_over_time, transaction_date_range
from hane_finans.reports.context import MarketData, load_market_data

COLUMNS = ["tarih", "tl", "reel_tl", "usd", "altin_g", "varlik", "borc", "likit_net", "eksik_fiyat", "bayat_fiyat"]


def networth_series(
    session: Session,
    start: date | None = None,
    end: date | None = None,
    frequency: str = "ay",
    owner: str | None = None,
    market: MarketData | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> pd.DataFrame:
    """One row per reporting date: net worth in TL, real TL, USD and grams of gold,
    assets, debts and liquid net assets (all TRY except the two units)."""
    first, last = transaction_date_range(session)
    if first is None:
        return pd.DataFrame(columns=COLUMNS)
    start = start or first
    end = end or max(last, date.today())
    market = market or load_market_data(session)
    dates = sample_dates(start, end, frequency)
    rows = []
    for d, positions in positions_over_time(session, dates, owner=owner).items():
        valued = value_positions(positions, market.book, d, max_age_days)
        nw = net_worth(valued, market.book, d, market.deflator, market.base_month)
        u = nw.net
        rows.append(
            {
                "tarih": d,
                "tl": float(u.try_),
                "reel_tl": float(u.real_try) if u.real_try is not None else None,
                "usd": float(u.usd) if u.usd is not None else None,
                "altin_g": float(u.gold_g) if u.gold_g is not None else None,
                "varlik": float(nw.assets),
                "borc": float(nw.liabilities),
                "likit_net": float(liquidity(valued).liquid_net),
                "eksik_fiyat": len(nw.missing),
                "bayat_fiyat": len(nw.stale),
            }
        )
    return pd.DataFrame(rows, columns=COLUMNS)
