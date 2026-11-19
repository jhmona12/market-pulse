"""Research-only price-quality policy, separate from verified trading halts."""
from __future__ import annotations

import numpy as np
import pandas as pd

from outcome_diagnostics import disputed_price_exposure


def unresolved_flags(ledger):
    required = {"label", "date", "status"}
    if not required.issubset(ledger.columns):
        raise ValueError("Incomplete price-review ledger")
    rows = ledger.loc[ledger.status.str.startswith("unresolved_", na=False)].copy()
    rows["date"] = pd.to_datetime(rows.date, errors="raise")
    if rows.empty or rows.duplicated(["label", "date"]).any():
        raise ValueError("Empty or duplicate quarantine flags")
    return {symbol: pd.DatetimeIndex(group.date.sort_values()) for symbol, group in rows.groupby("label")}


def quarantine_bars(bars, dates, calendar):
    """Mask questionable closes and preceding sessions, without inventing halts."""
    result = bars.copy()
    calendar = pd.DatetimeIndex(calendar)
    positions = calendar.get_indexer(pd.DatetimeIndex(dates))
    positions = positions[positions >= 0]
    affected = calendar[np.unique(np.r_[positions, np.maximum(positions - 1, 0)])]
    columns = [c for c in ("open", "high", "low", "close", "volume") if c in result]
    result.loc[result.date.isin(affected), columns] = np.nan
    return result


def quarantine_exposure(calendar, dates, horizon):
    # Outcomes are censored separately: a future bad quote must not remove a
    # stock from the investable universe at the earlier signal date.
    return disputed_price_exposure(calendar, dates, calendar, horizon)
