"""Historical eligibility, dated classifications and sourced terminal labels.

No labels or event flags are added to predictor columns. A missing price remains
unknown unless a reviewed terminal event resolves the old security's claim.
"""
from __future__ import annotations

import re
from functools import lru_cache

import numpy as np
import pandas as pd

from security_lifecycle import TerminalEvent, resolve_terminal_outcome


def member_mask(dates, intervals):
    dates = pd.DatetimeIndex(dates)
    selected = np.zeros(len(dates), dtype=bool)
    previous_end = None
    for row in intervals.sort_values("start").itertuples():
        start, end = pd.Timestamp(row.start), pd.Timestamp(row.end)
        if pd.isna(start) or (pd.notna(end) and end <= start):
            raise ValueError("Invalid membership interval")
        if previous_end is not None and start < previous_end:
            raise ValueError("Overlapping membership intervals for one security")
        selected |= (dates >= start) & (pd.isna(end) | (dates < end))
        previous_end = end if pd.notna(end) else pd.Timestamp.max
    return selected


def symbol_key(value):
    value = str(value).strip().upper().replace("*", "").replace("/", "-")
    value = re.sub(r"\.\d+$", "", value)
    value = value.replace(".", "-").replace(" ", "")
    return {"BRKB": "BRK-B", "BFB": "BF-B"}.get(value, value)


def resolve_sector_identity(symbol, isin, active_labels, renames, isin_labels):
    """Identifiers may link aliases, but never transfer a future sector value."""
    by_symbol = set(security_candidates(symbol, active_labels, renames))
    by_isin = set(isin_labels.get(isin, ())) & set(active_labels)
    if isin in isin_labels:
        candidates = by_isin & by_symbol if by_symbol else by_isin
        return sorted(candidates) if len(candidates) == 1 else []
    return sorted(by_symbol) if len(by_symbol) == 1 else []


@lru_cache(maxsize=4096)
def _alias_keys(symbol, renames):
    graph = {}
    for old, new in renames:
        a, b = symbol_key(old), symbol_key(new)
        graph.setdefault(a, set()).add(b)
        graph.setdefault(b, set()).add(a)
    linked, pending = set(), [symbol_key(symbol)]
    while pending:
        key = pending.pop()
        if key not in linked:
            linked.add(key)
            pending.extend(graph.get(key, set()) - linked)
    return frozenset(linked)


@lru_cache(maxsize=512)
def _active_keys(labels):
    result = {}
    for label in labels:
        result.setdefault(symbol_key(re.sub(r"-\d{6}$", "", label)), []).append(label)
    return result


def security_candidates(symbol, active_labels, renames):
    """Resolve only unique names among securities eligible at the snapshot date."""
    linked = _alias_keys(symbol_key(symbol), tuple(tuple(pair) for pair in renames))
    active = _active_keys(tuple(active_labels))
    return sorted({label for key in linked for label in active.get(key, ())})


def dated_sectors(dates, observations, *, lag_sessions=1, max_age_days=62):
    """Never use a later snapshot to fill an earlier sector classification."""
    calendar = pd.DatetimeIndex(dates)
    if observations.empty:
        return pd.DataFrame({"date": calendar, "sector": None, "sector_observed_at": pd.NaT})
    obs = observations.sort_values("date").drop_duplicates("date").copy()
    positions = calendar.searchsorted(pd.DatetimeIndex(obs.date), side="right") + lag_sessions - 1
    obs = obs[positions < len(calendar)].copy()
    obs["available_at"] = calendar[positions[positions < len(calendar)]]
    obs = obs.rename(columns={"date": "sector_observed_at"})
    result = pd.merge_asof(pd.DataFrame({"date": calendar}), obs[["available_at", "sector", "sector_observed_at"]],
                           left_on="date", right_on="available_at", direction="backward")
    result.loc[(result.date - result.sector_observed_at).dt.days.gt(max_age_days), "sector"] = None
    return result.drop(columns="available_at")


def normalize_bars(raw, no_trade_dates=()):
    raw = raw.sort_values("date").copy()
    if raw.date.duplicated().any() or raw.date.isna().any():
        raise ValueError("Duplicate/missing security price dates")
    valid = np.isfinite(raw.close) & raw.close.gt(0) & np.isfinite(raw.adj_close) & raw.adj_close.gt(0)
    if not valid.all():
        raise ValueError("Nonpositive/nonfinite observed price")
    factor = raw.adj_close / raw.close
    result = raw[["date", "open", "high", "low", "volume"]].copy()
    result[["open", "high", "low"]] = result[["open", "high", "low"]].mul(factor, axis=0)
    result["close"] = raw.adj_close
    invalid_trade = raw.date.isin(pd.to_datetime(list(no_trade_dates))) | raw.source.str.contains("carried", case=False)
    result.loc[invalid_trade, ["open", "high", "low", "close", "volume"]] = np.nan
    return result


def reviewed_dividend_value(raw, record):
    date = pd.Timestamp(record["date"])
    match = raw[raw.date.eq(date)]
    if len(match) != 1 or match.iloc[0].source != "yahoo":
        raise ValueError("Reviewed dividend expects exactly one Yahoo split-adjusted bar")
    splits = raw.loc[raw.date.gt(date) & raw.stock_splits.gt(0), "stock_splits"]
    value = float(record["cash_per_then_share"]) / float(splits.prod())
    if not np.isfinite(value) or value < 0:
        raise ValueError("Invalid reviewed dividend")
    return value


def apply_reviewed_price_corrections(raw, records):
    """Rebase pre-event adjusted prices; never mutate frozen source bars.

    Only the reviewed ex-date return changes. Later levels and all other daily
    returns stay intact; the normalization layer adjusts OHLC consistently.
    """
    result = raw.sort_values("date").copy()
    for record in records:
        if record["action"] != "correct_dividend":
            continue
        date = pd.Timestamp(record["date"])
        if date < result.date.min() or date > result.date.max():
            continue
        if not record["source_url"].startswith("https://"):
            raise ValueError("Price correction requires primary evidence")
        value = reviewed_dividend_value(result, record)
        current = result[result.date.eq(date)].iloc[0]
        previous = result[result.date.lt(date)].tail(1)
        if previous.empty or abs(float(current.dividends) - record["expected_archive_dividend"]) > 1e-7:
            raise ValueError("Correction precondition changed; review archive or duplicate application")
        prior = previous.iloc[0]
        existing_gross = float(current.adj_close / prior.adj_close)
        corrected_gross = float((current.close + value) / prior.close)
        result.loc[result.date.lt(date), "adj_close"] *= existing_gross / corrected_gross
        result.loc[result.date.eq(date), "dividends"] = value
    return result


def forward_outcomes(raw, calendar, horizon, *, security_id, as_of, event_record=None, successor_raw=None, no_trade_dates=()):
    """Fixed next-close entry and original exit; verified liquidation then cash."""
    bars = normalize_bars(raw, no_trade_dates).set_index("date").reindex(calendar)
    entry, exit_value = bars.close.shift(-1), bars.close.shift(-(horizon + 1))
    outcome = exit_value / entry - 1
    available = pd.Series(pd.DatetimeIndex(calendar), index=calendar).shift(-(horizon + 1))
    resolved = np.zeros(len(calendar), dtype=bool)
    if event_record is None or not event_record.get("verified"):
        return outcome, available, resolved
    event_date = pd.Timestamp(event_record["effective_date"])
    known = pd.Timestamp(event_record["value_known_at"])
    if event_record.get("unresolved_rights"):
        return outcome, available, resolved
    cash = float(event_record.get("cash", 0))
    ratio = float(event_record.get("stock_ratio", 0))
    if ratio:
        if successor_raw is None:
            raise ValueError("Missing successor event valuation")
        successor = successor_raw[successor_raw.date.ge(event_date)].sort_values("date").head(1)
        if len(successor) != 1 or successor.iloc[0].date - event_date > pd.Timedelta(days=4):
            raise ValueError("No realizable successor price near event date")
        valuation_date = successor.iloc[0].date
        event_date = max(event_date, valuation_date)
        known = max(known, valuation_date)
        # Yahoo closes are already split-adjusted; undo later splits to get
        # contractual event-day share units. Other sources store raw closes.
        value = float(successor.iloc[0].close)
        if successor.iloc[0].source == "yahoo":
            splits = successor_raw.loc[successor_raw.date.gt(event_date) & successor_raw.stock_splits.gt(0), "stock_splits"]
            value *= float(splits.prod())
        cash += ratio * value
    observed = raw[raw.date.le(event_date) & ~raw.date.isin(pd.to_datetime(list(no_trade_dates))) & ~raw.source.str.contains("carried", case=False)].sort_values("date")
    if observed.empty:
        raise ValueError("No actual trade before terminal event")
    last = observed.iloc[-1]
    if event_date - last.date > pd.Timedelta(days=7):
        raise ValueError("Terminal event cannot bridge an unreviewed long missing history")
    event = TerminalEvent(security_id=security_id, kind=event_record["kind"], effective_date=event_date.date(),
        value_known_at=known.date(), cash_equivalent_per_old_share=cash, source_url=event_record["source_url"], verified=True)
    # Do not acquire a synthetic post-termination holding. The final observed
    # trade can still be an entry when the transaction closed after that close.
    for i in np.flatnonzero(outcome.isna() & entry.notna()):
        end_index = i + horizon + 1
        if end_index >= len(calendar) or calendar[i + 1] > last.date or calendar[end_index] < event_date:
            continue
        result = resolve_terminal_outcome(security_id=security_id, total_return_to_last=float(last.adj_close / entry.iloc[i] - 1),
            last_raw_close=float(last.close), last_trade_date=last.date.date(), exit_date=calendar[end_index].date(),
            as_of=pd.Timestamp(as_of).date(), event=event)
        if result["status"] == "resolved":
            outcome.iloc[i] = result["return"]
            available.iloc[i] = pd.Timestamp(result["available_at"])
            resolved[i] = True
    return outcome, available, resolved


def outcome_bounds(raw, calendar, horizon, *, outcome, event_record=None, **kwargs):
    """Unpriced rights produce sensitivity bounds, never training labels."""
    low, high = outcome.copy(), outcome.copy()
    if event_record and event_record.get("verified") and event_record.get("unresolved_rights") and event_record.get("rights_max") is not None:
        maximum = float(event_record["rights_max"])
        if not np.isfinite(maximum) or maximum < 0:
            raise ValueError("Invalid contingent-rights upper bound")
        for extra, target in ((0, low), (maximum, high)):
            bounded_event = {**event_record, "unresolved_rights": False, "cash": float(event_record["cash"]) + extra}
            estimate, _, _ = forward_outcomes(raw, calendar, horizon, event_record=bounded_event, **kwargs)
            target.loc[outcome.isna()] = estimate.loc[outcome.isna()]
    return low, high
