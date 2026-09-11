"""Parse adjusted daily bars and retain enough evidence to explain rejected rows."""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from zoneinfo import ZoneInfo

import pandas as pd


def number(value):
    try:
        result = float(value)
        return result if isfinite(result) else None
    except (TypeError, ValueError):
        return None


def parse_yahoo_history(payload: dict) -> pd.DataFrame:
    chart = payload.get("chart") or {}
    if chart.get("error") or not chart.get("result"):
        raise ValueError(f"Yahoo chart response: {chart.get('error') or 'missing result'}")
    result = chart["result"][0]
    meta = result.get("meta") or {}
    # Daily dates belong to the exchange, not the machine running the job.
    exchange_name = meta.get("exchangeTimezoneName")
    if not exchange_name:
        raise ValueError("Yahoo chart response is missing exchangeTimezoneName")
    exchange = ZoneInfo(exchange_name)
    stamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    quote = (indicators.get("quote") or [{}])[0]
    adjusted = (indicators.get("adjclose") or [{}])[0].get("adjclose") or []

    def value(values, index):
        return number(values[index]) if index < len(values) else None

    rows, tail = [], []
    rejected = 0
    for index, stamp in enumerate(stamps):
        instant = datetime.fromtimestamp(stamp, tz=timezone.utc)
        session = instant.astimezone(exchange).date().isoformat()
        raw_close = value(quote.get("close") or [], index)
        close = value(adjusted, index)
        reason = None
        if close is None or close <= 0:
            reason = "missing_or_invalid_adjusted_close"
        elif raw_close is None or raw_close <= 0:
            reason = "missing_or_invalid_raw_close"
        if index >= len(stamps) - 3:
            tail.append({"timestampUtc": instant.isoformat(), "sessionDate": session,
                         "close": raw_close, "adjustedClose": close, "rejectedReason": reason})
        if reason:
            rejected += 1
            continue
        factor = close / raw_close

        def adjusted_value(field):
            raw = value(quote.get(field) or [], index)
            return raw * factor if raw is not None else None

        rows.append({"date": session, "open": adjusted_value("open"),
                     "high": adjusted_value("high"), "low": adjusted_value("low"),
                     "close": close, "volume": value(quote.get("volume") or [], index)})

    frame = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    consolidated = 0
    # Yahoo can append a revised daily bar for the same final exchange date.
    # Prefer that complete replacement, never add its volume to the earlier bar.
    if len(frame) >= 2 and frame.iloc[-1]["date"] == frame.iloc[-2]["date"]:
        frame = frame.drop(frame.index[-2]).reset_index(drop=True)
        consolidated = 1
    if frame["date"].duplicated().any():
        raise ValueError("Yahoo history contains duplicate sessions beyond the final daily update")
    frame.attrs["priceHistory"] = {
        "exchangeTimezone": exchange_name, "rawRows": len(stamps),
        "acceptedRows": len(frame), "rejectedRows": rejected,
        "consolidatedDailyRows": consolidated, "tail": tail,
    }
    return frame
