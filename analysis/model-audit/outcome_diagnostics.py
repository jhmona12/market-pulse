"""Pure, fixture-tested diagnostics. Missing outcomes are not zero returns."""
from __future__ import annotations

import numpy as np
import pandas as pd


def no_trade_calendar(calendar, listed_days, exit_gaps, periods):
    calendar = pd.DatetimeIndex(calendar)
    result = {}
    for row in listed_days.itertuples():
        result.setdefault(row.ticker, set()).add(pd.Timestamp(row.date))
    for row in exit_gaps.itertuples():
        start, end = pd.Timestamp(row.last_trade), pd.Timestamp(row.next_trade)
        if end <= start:
            raise ValueError("Invalid halt boundaries")
        result.setdefault(row.ticker, set()).update(calendar[(calendar > start) & (calendar < end)])
    for row in periods:
        start, end = pd.Timestamp(row["start"]), pd.Timestamp(row["end"])
        if end < start or not row["source_url"].startswith("https://"):
            raise ValueError("Invalid reviewed no-trade period")
        result.setdefault(row["security_id"], set()).update(calendar[(calendar >= start) & (calendar <= end)])
    return result


def unknown_reason(*, entry_date, exit_date, last_trade, entry_valid, exit_valid,
                   no_trade_dates=(), event=None, stock_outcome=np.nan):
    if not entry_valid:
        if entry_date in no_trade_dates:
            return "entry_during_halt"
        if entry_date > last_trade:
            if event and event.get("verified") and entry_date >= pd.Timestamp(event["effective_date"]):
                return "entry_after_verified_termination"
            return "missing_entry_before_event" if event else "entry_after_unreviewed_series_end"
        return "missing_entry_quote"
    if exit_date in no_trade_dates:
        return "exit_during_halt"
    if pd.notna(stock_outcome):
        return "missing_sector_benchmark"
    if exit_valid:
        return "unexpected_label_failure"
    if event and event.get("unresolved_rights"):
        return "unpriced_contingent_rights"
    if exit_date > last_trade:
        return "registered_event_needs_review" if event else "unreviewed_series_end"
    return "missing_exit_quote"


def disputed_price_exposure(dates, flagged_dates, calendar, horizon, lookback=252):
    """Conservative exposure, not a deletion mask based on future outcomes.

    Feature exposure uses only past dates; label exposure is tracked separately.
    A flagged daily return questions both that close and its preceding close.
    Spanning the return is conservative for adjustment errors (not just endpoints).
    """
    calendar = pd.DatetimeIndex(calendar)
    positions = calendar.get_indexer(pd.DatetimeIndex(dates))
    if (positions < 0).any():
        raise ValueError("Observation outside benchmark calendar")
    past, future = np.zeros(len(dates), bool), np.zeros(len(dates), bool)
    for date in pd.to_datetime(list(flagged_dates)):
        index = calendar.get_indexer([date])[0]
        if index < 0:
            continue
        past |= (positions >= max(0, index - 1)) & (positions - lookback <= index)
        future |= (positions + 1 <= index) & (positions + horizon + 1 >= max(0, index - 1))
    return past, future


def spinoff_return(row):
    return (float(row.parent_close) * float(row.parent_shares) + float(row.ratio) * float(row.spinco_close)
            + (0. if pd.isna(row.cash) else float(row.cash))) / float(row.parent_prev_close) - 1


def missingness_summary(rows):
    """Compare input distributions; never infer missing-at-random from a small %."""
    result = []
    for unknown, group in rows.groupby(rows.excess.isna()):
        result.append({"unknown_outcome": bool(unknown), "rows": len(group), "symbols": group.symbol.nunique(),
                       "median_prior_60d_return": group.ret_60d.median(),
                       "median_prior_60d_volatility": group.volatility_60d.median()})
    return result
