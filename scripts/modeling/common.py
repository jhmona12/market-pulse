from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape
from io import StringIO
from pathlib import Path
import json
import re
import time
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "modeling"
RAW_DIR = DATA_DIR / "raw"
PRICES_DIR = RAW_DIR / "prices"
MACRO_DIR = RAW_DIR / "macro"
REFERENCE_DIR = RAW_DIR / "reference"
FEATURES_DIR = DATA_DIR / "features"
MODELS_DIR = DATA_DIR / "models"
REPORTS_DIR = DATA_DIR / "reports"

USER_AGENT = "MarketPulseModel/0.1 personal research dashboard"
TEN_YEARS_DAYS = 3653

MACRO_SERIES = (
    "DGS10",
    "DGS2",
    "FEDFUNDS",
    "UNRATE",
    "CPIAUCSL",
    "GDP",
)

SECTOR_ETFS = (
    "XLK",
    "XLC",
    "XLY",
    "XLP",
    "XLF",
    "XLV",
    "XLI",
    "XLE",
    "XLU",
    "XLB",
    "XLRE",
)


@dataclass(frozen=True)
class RunConfig:
    start_date: datetime
    end_date: datetime


def ensure_directories() -> None:
    for path in (
        DATA_DIR,
        RAW_DIR,
        PRICES_DIR,
        MACRO_DIR,
        REFERENCE_DIR,
        FEATURES_DIR,
        MODELS_DIR,
        REPORTS_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def default_run_config() -> RunConfig:
    end_date = utc_now()
    start_date = end_date - timedelta(days=TEN_YEARS_DAYS)
    return RunConfig(start_date=start_date, end_date=end_date)


def run_config_for_years(years: int) -> RunConfig:
    end_date = utc_now()
    start_date = end_date - timedelta(days=max(365, int(years * 365.25)))
    return RunConfig(start_date=start_date, end_date=end_date)


def fetch_text(url: str, timeout: int = 30) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml,text/csv,text/plain,application/json;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def yahoo_symbol(symbol: str) -> str:
    normalized = symbol.upper().strip()
    if re.match(r"^[A-Z]{1,5}\.[A-Z]$", normalized):
        return normalized.replace(".", "-")
    return normalized


def strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    text = unescape(text)
    text = re.sub(r"\[[^\]]+\]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_yahoo_history(symbol: str, start_date: datetime, end_date: datetime) -> pd.DataFrame:
    period1 = int(start_date.timestamp())
    period2 = int(end_date.timestamp())
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{quote(yahoo_symbol(symbol))}?period1={period1}&period2={period2}&interval=1d&events=history&includeAdjustedClose=true"
    )
    payload = json.loads(fetch_text(url))
    result = payload["chart"]["result"][0]
    timestamps = result.get("timestamp", [])
    quote_data = result.get("indicators", {}).get("quote", [{}])[0]
    adjusted = result.get("indicators", {}).get("adjclose", [{}])[0].get("adjclose", [])

    rows = []
    for index, stamp in enumerate(timestamps):
        raw_close_values = quote_data.get("close", [])
        raw_close = raw_close_values[index] if index < len(raw_close_values) else None
        close = adjusted[index] if index < len(adjusted) else raw_close
        if close is None:
            continue

        adjustment_factor = 1.0
        try:
            if raw_close is not None and float(raw_close) != 0:
                adjustment_factor = float(close) / float(raw_close)
        except (TypeError, ValueError):
            adjustment_factor = 1.0

        def adjusted_value(field: str) -> float | int | None:
            values = quote_data.get(field, [])
            raw_value = values[index] if index < len(values) else None
            if raw_value is None:
                return None
            try:
                return float(raw_value) * adjustment_factor
            except (TypeError, ValueError):
                return raw_value

        rows.append(
            {
                "date": datetime.fromtimestamp(stamp, tz=timezone.utc).date().isoformat(),
                "open": adjusted_value("open"),
                "high": adjusted_value("high"),
                "low": adjusted_value("low"),
                "close": close,
                "volume": quote_data.get("volume", [None])[index] if index < len(quote_data.get("volume", [])) else None,
            }
        )
    return pd.DataFrame(rows)


def fetch_fred_series(series_id: str) -> pd.DataFrame:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={quote(series_id)}"
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            csv_text = fetch_text(url, timeout=120)
            break
        except Exception as error:  # noqa: BLE001
            last_error = error
            time.sleep(1.5 * (attempt + 1))
    else:
        raise last_error if last_error is not None else RuntimeError(f"Failed to fetch FRED series {series_id}")
    frame = pd.read_csv(StringIO(csv_text))
    frame.columns = [column.strip().lower() for column in frame.columns]
    frame = frame.rename(columns={frame.columns[0]: "date", frame.columns[1]: "value"})
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame = frame.dropna(subset=["date", "value"])
    frame["series_id"] = series_id
    return frame


def fetch_sp500_constituents() -> pd.DataFrame:
    html = fetch_text("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
    table_match = re.search(r"<table[^>]+id=[\"']constituents[\"'][\s\S]*?</table>", html, flags=re.IGNORECASE)
    if table_match:
        rows = re.findall(r"<tr[\s\S]*?</tr>", table_match.group(0), flags=re.IGNORECASE)
        records = []
        for row in rows[1:]:
            cells = re.findall(r"<t[dh][^>]*>([\s\S]*?)</t[dh]>", row, flags=re.IGNORECASE)
            if len(cells) < 4:
                continue
            records.append(
                {
                    "symbol": strip_html(cells[0]),
                    "name": strip_html(cells[1]),
                    "sector": strip_html(cells[2]),
                    "sub_industry": strip_html(cells[3]),
                }
            )
        if len(records) >= 400:
            frame = pd.DataFrame(records)
        else:
            raise ValueError("Could not parse enough S&P 500 rows from Wikipedia constituents table")
    else:
        raise ValueError("No S&P 500 constituents table found on Wikipedia")

    frame["symbol"] = frame["symbol"].astype(str).str.strip()
    frame["name"] = frame["name"].astype(str).str.strip()
    frame["sector"] = frame["sector"].astype(str).str.strip()
    frame["sub_industry"] = frame["sub_industry"].astype(str).str.strip()
    return frame


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def write_json(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def safe_fetch_symbols(symbols: Iterable[str], start_date: datetime, end_date: datetime, sleep_seconds: float = 0.05) -> dict[str, str]:
    failures: dict[str, str] = {}
    for symbol in symbols:
        target = PRICES_DIR / f"{symbol}.csv"
        try:
            frame = fetch_yahoo_history(symbol, start_date, end_date)
            if frame.empty:
                raise ValueError("empty price history")
            write_csv(frame, target)
            time.sleep(sleep_seconds)
        except (HTTPError, URLError, ValueError, KeyError, IndexError, json.JSONDecodeError) as error:
            failures[symbol] = str(error)
    return failures
