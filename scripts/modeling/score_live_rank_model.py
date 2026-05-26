from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from build_training_dataset import build_market_context, enrich_price_features, sector_name_for_etf
from common import ROOT, SECTOR_ETFS, fetch_sp500_constituents, fetch_yahoo_history, run_config_for_years, write_json
from model_features import (
    MARKET_CONTEXT_FEATURE_COLUMNS,
    RANK_BASE_COLUMNS,
    RAW_PRICE_FEATURE_COLUMNS,
    SECTOR_CONTEXT_FEATURE_COLUMNS,
    SECTOR_MEDIAN_COLUMNS,
    add_cross_sectional_model_features,
    model_feature_columns,
)

try:
    import xgboost as xgb
except ModuleNotFoundError as error:  # pragma: no cover - handled at runtime
    raise SystemExit(
        "xgboost is not installed in the current Python environment. "
        "Run scripts/modeling/setup_training_env.sh before scoring the live model."
    ) from error


MODEL_DIR = ROOT / "models" / "rank"
DEFAULT_MODEL_NAME = "xgboost_rank_sector14_tuned"
REBOUND_ACTIVATION_VOL_MULTIPLE = 0.75
REBOUND_ACTIVATION_WINDOW_DAYS = 5
STOP_ATR_DAYS = 20
STOP_CHANDELIER_DAYS = 22
STOP_SUPPORT_DAYS = 20
STOP_TREND_DAYS = 50
PACIFIC = ZoneInfo("America/Los_Angeles")
MARKET_STRIP_SYMBOLS = ("SPY", "QQQ", "IWM", "TLT", "GLD", "HYG")
MARKET_STRIP_LABELS = {
    "SPY": "S&P 500",
    "QQQ": "Nasdaq 100",
    "IWM": "Russell 2000",
    "TLT": "20Y Treasury",
    "GLD": "Gold",
    "HYG": "High Yield",
}


def observed_fixed_holiday(year: int, month: int, day: int) -> date:
    holiday = date(year, month, day)
    if holiday.weekday() == 5:
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:
        return holiday + timedelta(days=1)
    return holiday


def nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    candidate = date(year, month, 1)
    while candidate.weekday() != weekday:
        candidate += timedelta(days=1)
    return candidate + timedelta(days=7 * (nth - 1))


def last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        candidate = date(year, 12, 31)
    else:
        candidate = date(year, month + 1, 1) - timedelta(days=1)
    while candidate.weekday() != weekday:
        candidate -= timedelta(days=1)
    return candidate


def easter_sunday(year: int) -> date:
    # Meeus/Jones/Butcher Gregorian algorithm.
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def market_holidays(year: int) -> set[date]:
    holidays = {
        observed_fixed_holiday(year, 1, 1),
        nth_weekday(year, 1, 0, 3),   # Martin Luther King Jr. Day
        nth_weekday(year, 2, 0, 3),   # Washington's Birthday
        easter_sunday(year) - timedelta(days=2),
        last_weekday(year, 5, 0),     # Memorial Day
        observed_fixed_holiday(year, 6, 19),
        observed_fixed_holiday(year, 7, 4),
        nth_weekday(year, 9, 0, 1),   # Labor Day
        nth_weekday(year, 11, 3, 4),  # Thanksgiving
        observed_fixed_holiday(year, 12, 25),
    }
    if year < 2022:
        holidays.discard(observed_fixed_holiday(year, 6, 19))
    return holidays


def is_market_session(value: date) -> bool:
    return value.weekday() < 5 and value not in market_holidays(value.year)


def previous_market_session(value: date) -> date:
    candidate = value - timedelta(days=1)
    while not is_market_session(candidate):
        candidate -= timedelta(days=1)
    return candidate


def latest_expected_market_data_date(now: datetime | None = None) -> date:
    override = os.environ.get("EXPECTED_MARKET_DATA_DATE", "").strip()
    if override:
        return date.fromisoformat(override)
    now_pt = (now or datetime.now(PACIFIC)).astimezone(PACIFIC)
    today_pt = now_pt.date()
    if not is_market_session(today_pt):
        return previous_market_session(today_pt)
    return today_pt if now_pt.hour >= 14 else previous_market_session(today_pt)


def assert_fresh_as_of(as_of: object, source: str) -> None:
    if os.environ.get("ALLOW_STALE_MODEL_DATA") == "1":
        return
    if as_of is None:
        raise ValueError(f"{source} has no as-of date; refusing to use it for model scoring")
    as_of_date = pd.to_datetime(as_of).date()
    expected = latest_expected_market_data_date()
    if as_of_date < expected:
        raise ValueError(f"{source} is stale: as-of {as_of_date.isoformat()}, expected {expected.isoformat()}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score the latest S&P 500 universe with the production rank model.")
    parser.add_argument("--model-dir", default=str(MODEL_DIR.relative_to(ROOT)), help="Committed model artifact directory.")
    parser.add_argument("--model-name", default=DEFAULT_MODEL_NAME, help="Base model artifact name.")
    parser.add_argument("--output", default="data/model-rank-scores.json", help="Output JSON path.")
    parser.add_argument("--years", type=int, default=3, help="Trailing years of daily history to fetch for live features.")
    parser.add_argument("--max-workers", type=int, default=4, help="Concurrent Yahoo history fetch workers.")
    parser.add_argument("--max-symbols", type=int, default=0, help="Optional development cap for stock symbols.")
    parser.add_argument(
        "--focus-symbols",
        default="",
        help="Optional comma/space separated symbols to score against the S&P 500 reference universe.",
    )
    parser.add_argument(
        "--reference-cache",
        default="data/model-reference-cache.json",
        help="Reusable S&P 500 reference cache path for fast focus-symbol scoring.",
    )
    parser.add_argument(
        "--no-reference-cache",
        action="store_true",
        help="Ignore the reference cache and rebuild the full S&P 500 feature matrix.",
    )
    parser.add_argument(
        "--score-reference-cache",
        action="store_true",
        help=(
            "Score the cached S&P 500 reference feature rows with the selected model instead of refetching prices. "
            "Use after a full same-day scoring run has refreshed the reference cache."
        ),
    )
    return parser.parse_args()


def parse_focus_symbols(value: str) -> list[str]:
    symbols = []
    for token in value.replace(",", " ").replace(";", " ").split():
        symbol = token.strip().upper().lstrip("$")
        if not symbol:
            continue
        if not all(character.isalnum() or character in ".-^=" for character in symbol):
            continue
        symbols.append(symbol)
    return list(dict.fromkeys(symbols))[:25]


def load_fallback_constituents() -> pd.DataFrame:
    config = json.loads((ROOT / "config" / "universe.json").read_text(encoding="utf-8"))
    frame = pd.DataFrame(config["fallbackStocks"])
    frame["sub_industry"] = "Fallback"
    return frame[["symbol", "name", "sector", "sub_industry"]]


def load_constituents(max_symbols: int) -> tuple[pd.DataFrame, str]:
    try:
        constituents = fetch_sp500_constituents()
        source = "wikipedia"
    except Exception:  # noqa: BLE001 - fallback keeps the dashboard alive
        constituents = load_fallback_constituents()
        source = "fallback_universe"
    constituents["symbol"] = constituents["symbol"].astype(str)
    if max_symbols > 0:
        constituents = constituents.head(max_symbols).copy()
    return constituents, source


def load_price_frame(symbol: str, years: int) -> tuple[str, pd.DataFrame | None, str | None]:
    config = run_config_for_years(years)
    expected_as_of = latest_expected_market_data_date()
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            frame = fetch_yahoo_history(symbol, config.start_date, config.end_date)
            if frame.empty:
                raise ValueError("empty price history")
            frame["date"] = pd.to_datetime(frame["date"])
            frame = frame[frame["date"].dt.date <= expected_as_of].copy()
            if frame.empty:
                raise ValueError(f"empty price history through expected EOD date {expected_as_of.isoformat()}")
            for column in ("open", "high", "low", "close", "volume"):
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
            frame = frame.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
            frame["symbol"] = symbol
            return symbol, enrich_price_features(frame), None
        except Exception as error:  # noqa: BLE001
            last_error = error
            if attempt < 3:
                time.sleep(1.5 * (attempt + 1))
    return symbol, None, str(last_error or "unknown error")


def fetch_symbol_frames(symbols: list[str], years: int, max_workers: int) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    frames: dict[str, pd.DataFrame] = {}
    failures: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(load_price_frame, symbol, years) for symbol in symbols]
        for future in as_completed(futures):
            symbol, frame, error = future.result()
            if frame is None:
                failures[symbol] = error or "unknown error"
            else:
                frames[symbol] = frame
    return frames, failures


def build_spy_context(spy_frame: pd.DataFrame) -> pd.DataFrame:
    spy = spy_frame[
        [
            "date",
            "forward_return_14d",
            "ret_1d",
            "ret_20d",
            "ret_60d",
            "ret_120d",
            "volatility_20d",
            "volatility_60d",
            "price_vs_sma_50",
            "price_vs_sma_200",
            "rsi_14",
        ]
    ].copy()
    return spy.rename(
        columns={
            "forward_return_14d": "spy_forward_return_14d",
            "ret_1d": "spy_ret_1d",
            "ret_20d": "spy_ret_20d",
            "ret_60d": "spy_ret_60d",
            "ret_120d": "spy_ret_120d",
            "volatility_20d": "spy_volatility_20d",
            "volatility_60d": "spy_volatility_60d",
            "price_vs_sma_50": "spy_price_vs_sma_50",
            "price_vs_sma_200": "spy_price_vs_sma_200",
            "rsi_14": "spy_rsi_14",
        }
    )


def build_sector_context(symbol_frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    sector_frames = []
    for etf in SECTOR_ETFS:
        frame = symbol_frames[etf][
            [
                "date",
                "ret_20d",
                "ret_60d",
                "ret_120d",
                "momentum_126d_skip_10",
                "momentum_252d_skip_20",
                "volatility_20d",
                "volatility_60d",
                "price_vs_sma_50",
                "price_vs_sma_200",
                "rsi_14",
                "forward_return_14d_next_close",
            ]
        ].copy()
        frame = frame.rename(
            columns={
                "ret_20d": "sector_ret_20d",
                "ret_60d": "sector_ret_60d",
                "ret_120d": "sector_ret_120d",
                "momentum_126d_skip_10": "sector_momentum_126d_skip_10",
                "momentum_252d_skip_20": "sector_momentum_252d_skip_20",
                "volatility_20d": "sector_volatility_20d",
                "volatility_60d": "sector_volatility_60d",
                "price_vs_sma_50": "sector_price_vs_sma_50",
                "price_vs_sma_200": "sector_price_vs_sma_200",
                "rsi_14": "sector_rsi_14",
                "forward_return_14d_next_close": "sector_forward_return_14d_next_close",
            }
        )
        frame["sector"] = sector_name_for_etf(etf)
        sector_frames.append(frame)
    return pd.concat(sector_frames, ignore_index=True)


def infer_sector_for_symbol(symbol: str, frame: pd.DataFrame, symbol_frames: dict[str, pd.DataFrame]) -> dict:
    stock_returns = frame[["date", "ret_1d"]].dropna().tail(252).rename(columns={"ret_1d": "stock_ret"})
    correlations = []
    for etf in SECTOR_ETFS:
        sector_frame = symbol_frames.get(etf)
        if sector_frame is None:
            continue
        merged = stock_returns.merge(
            sector_frame[["date", "ret_1d"]].dropna().tail(252).rename(columns={"ret_1d": "sector_ret"}),
            on="date",
            how="inner",
        )
        correlation = merged["stock_ret"].corr(merged["sector_ret"]) if len(merged) >= 60 else np.nan
        correlations.append(
            {
                "sector": sector_name_for_etf(etf),
                "sectorEtf": etf,
                "correlation": correlation,
                "observations": len(merged),
            }
        )

    clean = [item for item in correlations if np.isfinite(item["correlation"])]
    if not clean:
        return {
            "symbol": symbol,
            "sector": "Information Technology",
            "sectorEtf": "XLK",
            "sectorSource": "fallback",
            "sectorCorrelation": None,
            "sectorCorrelationObservations": 0,
        }

    best = sorted(clean, key=lambda item: item["correlation"], reverse=True)[0]
    return {
        "symbol": symbol,
        "sector": best["sector"],
        "sectorEtf": best["sectorEtf"],
        "sectorSource": "return_correlation",
        "sectorCorrelation": round(float(best["correlation"]), 4),
        "sectorCorrelationObservations": int(best["observations"]),
    }


def append_focus_constituents(
    constituents: pd.DataFrame,
    focus_symbols: list[str],
    symbol_frames: dict[str, pd.DataFrame],
) -> tuple[pd.DataFrame, dict[str, dict]]:
    existing = set(constituents["symbol"].astype(str))
    additions = []
    sector_diagnostics: dict[str, dict] = {}
    for symbol in focus_symbols:
        if symbol in existing:
            sector_diagnostics[symbol] = {
                "symbol": symbol,
                "sector": constituents.loc[constituents["symbol"].eq(symbol), "sector"].iloc[0],
                "sectorSource": "sp500_constituent",
                "sectorCorrelation": None,
                "sectorCorrelationObservations": None,
            }
            continue
        frame = symbol_frames.get(symbol)
        if frame is None:
            continue
        diagnostic = infer_sector_for_symbol(symbol, frame, symbol_frames)
        sector_diagnostics[symbol] = diagnostic
        additions.append(
            {
                "symbol": symbol,
                "name": symbol,
                "sector": diagnostic["sector"],
                "sub_industry": "Focus ticker",
            }
        )
    if additions:
        constituents = pd.concat([constituents, pd.DataFrame(additions)], ignore_index=True)
    return constituents, sector_diagnostics


def pct_change(current: object, base: object) -> float | None:
    current_number = float(current) if current is not None else np.nan
    base_number = float(base) if base is not None else np.nan
    if not np.isfinite(current_number) or not np.isfinite(base_number) or base_number == 0:
        return None
    return (current_number / base_number - 1) * 100


def trailing_calendar_return(frame: pd.DataFrame, days: int) -> float | None:
    clean = frame.dropna(subset=["date", "close"]).sort_values("date")
    if clean.empty:
        return None
    latest = clean.iloc[-1]
    target_date = latest["date"] - pd.Timedelta(days=days)
    base = clean[clean["date"].le(target_date)]
    if base.empty:
        return None
    return pct_change(latest["close"], base.iloc[-1]["close"])


def beta_to_benchmark(frame: pd.DataFrame, benchmark: pd.DataFrame, period: int = 60) -> float | None:
    if frame.empty or benchmark.empty:
        return None
    asset = frame[["date", "close"]].dropna().sort_values("date").copy()
    bench = benchmark[["date", "close"]].dropna().sort_values("date").copy()
    asset["asset_return"] = asset["close"].pct_change()
    bench["benchmark_return"] = bench["close"].pct_change()
    pairs = asset[["date", "asset_return"]].merge(bench[["date", "benchmark_return"]], on="date", how="inner").dropna()
    window = pairs.tail(period)
    if len(window) < min(40, period):
        return None
    variance = window["benchmark_return"].var()
    if not np.isfinite(variance) or variance == 0:
        return None
    return window["asset_return"].cov(window["benchmark_return"]) / variance


def average_true_range(clean: pd.DataFrame, period: int) -> float | None:
    if len(clean) < period + 1:
        return None
    previous_close = clean["close"].shift(1)
    ranges = pd.concat(
        [
            clean["high"] - clean["low"],
            (clean["high"] - previous_close).abs(),
            (clean["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    window = pd.to_numeric(ranges, errors="coerce").dropna().tail(period)
    if len(window) != period:
        return None
    value = float(window.mean())
    return value if np.isfinite(value) and value > 0 else None


def stop_sell_signal_for_frame(frame: pd.DataFrame) -> dict:
    clean = frame.dropna(subset=["date", "high", "low", "close"]).sort_values("date").copy()
    if len(clean) < STOP_TREND_DAYS:
        return {}
    for column in ("high", "low", "close"):
        clean[column] = pd.to_numeric(clean[column], errors="coerce")
    clean = clean.dropna(subset=["high", "low", "close"])
    if len(clean) < STOP_TREND_DAYS:
        return {}

    close = float(clean.iloc[-1]["close"])
    atr20 = average_true_range(clean, STOP_ATR_DAYS)
    atr22 = average_true_range(clean, STOP_CHANDELIER_DAYS)
    high22 = float(clean["high"].tail(STOP_CHANDELIER_DAYS).max())
    low20 = float(clean["low"].tail(STOP_SUPPORT_DAYS).min())
    ma50 = float(clean["close"].tail(STOP_TREND_DAYS).mean())
    if atr20 is None or atr22 is None:
        return {}
    if not all(np.isfinite(value) for value in (close, atr20, atr22, high22, low20, ma50)) or close <= 0:
        return {}

    chandelier_stop = high22 - 3.0 * atr22
    trend_stop = ma50 - 0.5 * atr20
    support_stop = low20 - 0.25 * atr20
    lower_bound = close - 4.0 * atr20
    upper_bound = close - 1.5 * atr20
    raw_stop = max(chandelier_stop, trend_stop, support_stop)
    stop_sell_price = min(max(raw_stop, lower_bound), upper_bound)
    stop_sell_distance_pct = (stop_sell_price / close - 1.0) * 100

    return {
        "stopSellPrice": finite_or_none(stop_sell_price, 2),
        "stopSellDistancePct": finite_or_none(stop_sell_distance_pct, 2),
        "stopSellAtr20": finite_or_none(atr20, 2),
        "stopSellRule": "Exit on close below stop",
        "stopSellBasis": "balanced_atr_chandelier",
        "stopSellComponents": {
            "chandelierStop": finite_or_none(chandelier_stop, 2),
            "trendStop": finite_or_none(trend_stop, 2),
            "supportStop": finite_or_none(support_stop, 2),
            "lowerBound": finite_or_none(lower_bound, 2),
            "upperBound": finite_or_none(upper_bound, 2),
        },
    }


def dashboard_metrics_for_frame(symbol: str, frame: pd.DataFrame, benchmark: pd.DataFrame | None = None) -> dict:
    clean = frame.dropna(subset=["date", "close"]).sort_values("date")
    if clean.empty:
        return {}
    latest = clean.iloc[-1]
    latest_date = latest["date"]
    latest_close = latest["close"]
    first_ytd = clean[clean["date"].ge(pd.Timestamp(year=latest_date.year, month=1, day=1))]
    ytd_base = first_ytd.iloc[0]["close"] if not first_ytd.empty else clean.iloc[0]["close"]
    volume = pd.to_numeric(clean.get("volume", pd.Series(dtype=float)), errors="coerce")
    avg_volume_20 = volume.tail(20).mean() if len(volume) else np.nan
    beta_60d = 1.0 if symbol == "SPY" else beta_to_benchmark(clean, benchmark) if benchmark is not None else None
    return {
        "priceDataDate": latest_date.date().isoformat(),
        "close": finite_or_none(latest_close, 2),
        "changePct": finite_or_none(pct_change(latest_close, clean.iloc[-2]["close"] if len(clean) > 1 else None), 2),
        "return7": finite_or_none(trailing_calendar_return(clean, 7), 2),
        "return14": finite_or_none(trailing_calendar_return(clean, 14), 2),
        "return30": finite_or_none(trailing_calendar_return(clean, 30), 2),
        "return60": finite_or_none(trailing_calendar_return(clean, 60), 2),
        "return90": finite_or_none(trailing_calendar_return(clean, 90), 2),
        "ytdReturn": finite_or_none(pct_change(latest_close, ytd_base), 2),
        "beta60d": finite_or_none(beta_60d, 2),
        "rsi14": finite_or_none(latest.get("rsi_14"), 1),
        "volumeRatio": finite_or_none(float(latest.get("volume", np.nan)) / avg_volume_20 if avg_volume_20 else None, 2),
        "above50": bool(latest.get("price_vs_sma_50", np.nan) > 0),
        "above100": bool(latest.get("price_vs_sma_100", np.nan) > 0),
        "above200": bool(latest.get("price_vs_sma_200", np.nan) > 0),
        "history": [finite_or_none(value, 2) for value in clean["close"].tail(36).tolist()],
        "stopSellSignal": stop_sell_signal_for_frame(frame),
        "technicalSource": "model_scorer_yahoo_history",
    }


def build_dashboard_metrics(symbol_frames: dict[str, pd.DataFrame]) -> dict[str, dict]:
    benchmark = symbol_frames.get("SPY")
    return {
        symbol: dashboard_metrics_for_frame(symbol, frame, benchmark)
        for symbol, frame in symbol_frames.items()
        if frame is not None and not frame.empty
    }


def build_market_rows(symbol_frames: dict[str, pd.DataFrame], dashboard_metrics: dict[str, dict]) -> list[dict]:
    rows = []
    for symbol in MARKET_STRIP_SYMBOLS:
        metrics = dashboard_metrics.get(symbol)
        if not metrics:
            continue
        rows.append(
            {
                "symbol": symbol,
                "label": MARKET_STRIP_LABELS.get(symbol, symbol),
                "name": MARKET_STRIP_LABELS.get(symbol, symbol),
                "price": metrics.get("close"),
                "close": metrics.get("close"),
                "changePct": metrics.get("changePct"),
                "return7": metrics.get("return7"),
                "return14": metrics.get("return14"),
                "return30": metrics.get("return30"),
                "return60": metrics.get("return60"),
                "return90": metrics.get("return90"),
                "ytdReturn": metrics.get("ytdReturn"),
                "beta60d": metrics.get("beta60d"),
                "above50": metrics.get("above50"),
                "above100": metrics.get("above100"),
                "above200": metrics.get("above200"),
                "rsi14": metrics.get("rsi14"),
                "history": metrics.get("history", []),
                "date": metrics.get("priceDataDate"),
                "source": metrics.get("technicalSource"),
            }
        )
    return rows


def build_sector_rows(dashboard_metrics: dict[str, dict]) -> list[dict]:
    spy_return_30 = dashboard_metrics.get("SPY", {}).get("return30")
    rows = []
    for etf in SECTOR_ETFS:
        metrics = dashboard_metrics.get(etf)
        if not metrics:
            continue
        return_30 = metrics.get("return30")
        rows.append(
            {
                "sector": sector_name_for_etf(etf),
                "symbol": etf,
                "name": f"{sector_name_for_etf(etf)} Select Sector SPDR Fund",
                "date": metrics.get("priceDataDate"),
                "close": metrics.get("close"),
                "change1d": metrics.get("changePct"),
                "change5d": metrics.get("return7"),
                "change30d": return_30,
                "change60d": metrics.get("return60"),
                "change90d": metrics.get("return90"),
                "ytd": metrics.get("ytdReturn"),
                "relative30d": finite_or_none(return_30 - spy_return_30, 2) if return_30 is not None and spy_return_30 is not None else None,
                "above50": metrics.get("above50"),
                "above200": metrics.get("above200"),
                "rsi14": metrics.get("rsi14"),
                "history": metrics.get("history", []),
                "source": metrics.get("technicalSource"),
            }
        )
    return sorted(rows, key=lambda item: item.get("change30d") if item.get("change30d") is not None else -999, reverse=True)


def build_base_feature_dataset(
    symbol_frames: dict[str, pd.DataFrame],
    constituents: pd.DataFrame,
    breadth: pd.DataFrame,
    sector_context: pd.DataFrame,
    spy: pd.DataFrame,
) -> pd.DataFrame:
    sector_lookup = constituents.set_index("symbol")["sector"].to_dict()
    dataset_frames = []
    for symbol in constituents["symbol"].tolist():
        frame = symbol_frames.get(symbol)
        if frame is None:
            continue
        frame = frame.copy()
        frame["sector"] = sector_lookup[symbol]
        frame["symbol"] = symbol
        frame = frame.merge(spy, on="date", how="left")
        frame = frame.merge(breadth, on="date", how="left")
        frame = frame.merge(sector_context, on=["date", "sector"], how="left")
        spy_variance_60d = frame["spy_ret_1d"].rolling(60).var()
        frame["beta_60d"] = frame["ret_1d"].rolling(60).cov(frame["spy_ret_1d"]) / spy_variance_60d.replace(0, np.nan)
        frame["excess_return_14d"] = frame["forward_return_14d"] - frame["spy_forward_return_14d"]
        frame["rel_ret_20d_vs_spy"] = frame["ret_20d"] - frame["spy_ret_20d"]
        frame["rel_ret_60d_vs_spy"] = frame["ret_60d"] - frame["spy_ret_60d"]
        frame["rel_ret_120d_vs_spy"] = frame["ret_120d"] - frame["spy_ret_120d"]
        frame["rel_rsi_vs_spy"] = frame["rsi_14"] - frame["spy_rsi_14"]
        frame["rel_volatility_20d_vs_spy"] = frame["volatility_20d"] - frame["spy_volatility_20d"]
        frame["rel_volatility_60d_vs_spy"] = frame["volatility_60d"] - frame["spy_volatility_60d"]
        frame["idiosyncratic_ret_20d"] = frame["ret_20d"] - frame["beta_60d"] * frame["spy_ret_20d"]
        frame["idiosyncratic_ret_60d"] = frame["ret_60d"] - frame["beta_60d"] * frame["spy_ret_60d"]
        dataset_frames.append(frame)

    return pd.concat(dataset_frames, ignore_index=True).sort_values(["date", "symbol"]).reset_index(drop=True)


def assemble_live_features(
    symbol_frames: dict[str, pd.DataFrame],
    constituents: pd.DataFrame,
    reference_symbols: set[str] | None = None,
) -> pd.DataFrame:
    spy = build_spy_context(symbol_frames["SPY"])
    reference_symbols = reference_symbols or set(constituents["symbol"].astype(str))
    reference_frames = {
        symbol: frame
        for symbol, frame in symbol_frames.items()
        if symbol in reference_symbols or symbol in {"SPY", *SECTOR_ETFS}
    }
    reference_constituents = constituents[constituents["symbol"].isin(reference_symbols)].copy()
    breadth, sector_context = build_market_context(reference_frames, reference_constituents)
    dataset = build_base_feature_dataset(symbol_frames, constituents, breadth, sector_context, spy)
    dataset = add_cross_sectional_model_features(dataset, round_trip_cost=None)
    return dataset


def reason_tags(row: pd.Series) -> list[str]:
    reasons = []
    if row.get("volatility_60d_minus_sector_median", 0) < 0:
        reasons.append("Lower 60d volatility than sector median")
    if row.get("momentum_252d_skip_20_minus_sector_median", 0) > 0:
        reasons.append("Long-horizon momentum ahead of sector median")
    if row.get("ret_120d_sector_pct_rank", 0) >= 0.7:
        reasons.append("Top-sector 120d return rank")
    if row.get("price_vs_sma_200", 0) > 0:
        reasons.append("Above 200d trend")
    if row.get("distance_to_52w_high", -1) > -0.08:
        reasons.append("Within 8% of 52w high")
    if row.get("log_dollar_volume_20d_pct_rank", 0) >= 0.7:
        reasons.append("Liquid versus universe")
    return reasons[:4]


def risk_flags(row: pd.Series) -> list[str]:
    flags = []
    if row.get("rsi_14", 0) > 76:
        flags.append("RSI extended")
    if row.get("volatility_60d_minus_sector_median", 0) > 0:
        flags.append("Above-sector volatility")
    if row.get("price_vs_sma_50", 0) < 0:
        flags.append("Below 50d trend")
    if row.get("price_vs_sma_200", 0) < 0:
        flags.append("Below 200d trend")
    if row.get("volume_ratio_20", 1) < 0.7:
        flags.append("Weak volume confirmation")
    return flags[:4]


def is_top_decile(row: pd.Series) -> bool:
    rank = int(row.get("model_rank", 999999))
    universe_count = int(row.get("model_universe_count", 0))
    return universe_count > 0 and rank <= max(1, int(np.ceil(universe_count * 0.1)))


def is_momentum_confirmed(row: pd.Series) -> bool:
    return (
        is_top_decile(row)
        and row.get("price_vs_sma_50", 0) > 0
        and row.get("price_vs_sma_200", 0) > 0
        and row.get("ret_20d", 0) > 0
        and row.get("ret_60d", 0) > 0
    )


def is_rebound_watch(row: pd.Series) -> bool:
    return (
        is_top_decile(row)
        and row.get("price_vs_sma_50", 0) < 0
        and row.get("price_vs_sma_200", 0) < 0
        and row.get("ret_20d", 0) < 0
        and row.get("ret_60d", 0) < 0
    )


def setup_type(row: pd.Series) -> str:
    if is_momentum_confirmed(row):
        return "momentum_confirmed"
    if is_rebound_watch(row):
        return "model_rebound_watch"
    if is_top_decile(row) and (row.get("price_vs_sma_50", 0) < 0 or row.get("price_vs_sma_200", 0) < 0):
        return "model_ranked_not_momentum_confirmed"
    return "model_ranked"


def setup_tags(row: pd.Series) -> list[str]:
    kind = setup_type(row)
    if kind == "momentum_confirmed":
        return ["Momentum Confirmed"]
    if kind == "model_rebound_watch":
        return ["Model Rebound Watch", "Not Momentum Confirmed", "Activation Pending"]
    if kind == "model_ranked_not_momentum_confirmed":
        return ["Not Momentum Confirmed"]
    return []


def should_surface_stop_sell(row: pd.Series, setup: str) -> bool:
    return setup in {"momentum_confirmed", "model_rebound_watch", "model_ranked_not_momentum_confirmed"} or is_top_decile(row)


def rebound_activation(row: pd.Series) -> dict:
    if not is_rebound_watch(row):
        return {}
    close = row.get("close")
    volatility_20d = row.get("volatility_20d")
    if close is None or volatility_20d is None:
        return {}
    close = float(close)
    volatility_20d = float(volatility_20d)
    if not np.isfinite(close) or not np.isfinite(volatility_20d) or close <= 0 or volatility_20d <= 0:
        return {}
    activation_pct = REBOUND_ACTIVATION_VOL_MULTIPLE * volatility_20d
    activation_price = close * (1 + activation_pct)
    return {
        "reboundActivationPrice": finite_or_none(activation_price, 2),
        "reboundActivationPct": finite_or_none(activation_pct * 100, 2),
        "reboundActivationVolMultiple": REBOUND_ACTIVATION_VOL_MULTIPLE,
        "reboundActivationWindowDays": REBOUND_ACTIVATION_WINDOW_DAYS,
        "reboundActivationRule": "Close above current close plus 0.75x 20-day realized daily volatility within 5 trading days.",
        "volatility20d": finite_or_none(volatility_20d, 4),
    }


def finite_or_none(value: object, digits: int | None = None) -> float | int | str | None:
    if value is None:
        return None
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        if not np.isfinite(value):
            return None
        return round(float(value), digits) if digits is not None else float(value)
    return value


def frame_records(frame: pd.DataFrame, columns: list[str] | None = None) -> list[dict]:
    if columns is not None:
        columns = [column for column in columns if column in frame.columns]
        frame = frame[columns].copy()
    else:
        frame = frame.copy()
    frame = frame.replace([np.inf, -np.inf], np.nan)
    for column in frame.columns:
        if pd.api.types.is_datetime64_any_dtype(frame[column]):
            frame[column] = frame[column].dt.date.astype(str)
    return json.loads(frame.to_json(orient="records"))


def records_frame(records: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(records)
    if "date" in frame.columns:
        frame["date"] = pd.to_datetime(frame["date"])
    numeric_exclusions = {"symbol", "name", "sector", "sub_industry", "sectorEtf", "sectorSource"}
    for column in frame.columns:
        if column not in numeric_exclusions and column != "date":
            converted = pd.to_numeric(frame[column], errors="coerce")
            if converted.notna().sum() == frame[column].notna().sum():
                frame[column] = converted
    return frame


def unique_existing_columns(frame: pd.DataFrame, columns: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for column in columns:
        if column in frame.columns and column not in seen:
            result.append(column)
            seen.add(column)
    return result


def score_rows(dataset: pd.DataFrame, model_path: Path, feature_columns: list[str]) -> pd.DataFrame:
    latest_date = dataset["date"].max()
    latest = dataset[dataset["date"].eq(latest_date)].copy()
    latest = latest.dropna(subset=feature_columns)
    booster = xgb.Booster()
    booster.load_model(str(model_path))
    dmatrix = xgb.DMatrix(latest[feature_columns].to_numpy(dtype=float), feature_names=feature_columns)
    latest["model_score"] = booster.predict(dmatrix)
    latest = latest.sort_values("model_score", ascending=False).reset_index(drop=True)
    latest["model_rank"] = np.arange(1, len(latest) + 1)
    latest["model_percentile"] = 100 * (1 - (latest["model_rank"] - 1) / max(1, len(latest) - 1))
    return latest


def reference_cache_columns(base_dataset: pd.DataFrame) -> list[str]:
    return unique_existing_columns(
        base_dataset,
        [
            "date",
            "symbol",
            "name",
            "sector",
            "sub_industry",
            "close",
            "forward_return_14d",
            "spy_forward_return_14d",
            "excess_return_14d",
            "forward_return_14d_next_close",
            "sector_forward_return_14d_next_close",
            *RAW_PRICE_FEATURE_COLUMNS,
            *MARKET_CONTEXT_FEATURE_COLUMNS,
            *SECTOR_CONTEXT_FEATURE_COLUMNS,
            *RANK_BASE_COLUMNS,
            *SECTOR_MEDIAN_COLUMNS,
        ],
    )


def write_reference_cache(
    cache_path: Path,
    base_dataset: pd.DataFrame,
    breadth: pd.DataFrame,
    scored: pd.DataFrame,
    constituents: pd.DataFrame,
    reference_symbols: set[str],
    metadata: dict,
    model_name: str,
    model_path: Path,
    constituent_source: str,
    name_lookup: dict[str, str],
    dashboard_metrics: dict[str, dict],
    market_rows: list[dict],
    sector_rows: list[dict],
) -> None:
    reference_scored = scored[scored["symbol"].isin(reference_symbols)].copy()
    if reference_scored.empty:
        return

    latest_date = reference_scored["date"].max()
    reference_base = base_dataset[
        base_dataset["date"].eq(latest_date) & base_dataset["symbol"].isin(reference_symbols)
    ].copy()
    reference_base = reference_base.merge(
        constituents[["symbol", "name", "sub_industry"]],
        on="symbol",
        how="left",
    )
    reference_scores = reference_scored[["symbol", "model_score", "model_rank", "model_percentile", "sector"]].copy()
    reference_scores = reference_scores.rename(
        columns={
            "model_score": "modelScore",
            "model_rank": "modelRank",
            "model_percentile": "modelPercentile",
        }
    )
    reference_scores["name"] = reference_scores["symbol"].map(name_lookup).fillna(reference_scores["symbol"])
    reference_score_series = reference_scored["model_score"]
    reference_rankings = [
        row_payload(row, name_lookup, reference_scores=reference_score_series, dashboard_metrics=dashboard_metrics)
        for _, row in reference_scored.iterrows()
    ]
    payload = {
        "status": "ready",
        "generatedAt": pd.Timestamp.now("UTC").isoformat(),
        "asOfDate": latest_date.date().isoformat(),
        "referenceUniverse": "Current S&P 500",
        "referenceUniverseCount": int(len(reference_score_series)),
        "model": {
            "name": model_name,
            "path": str(model_path.relative_to(ROOT)),
            "target": metadata.get("target_column"),
            "returnColumn": metadata.get("return_column"),
            "featureCount": len(metadata.get("feature_columns", [])),
            "numBoostRound": metadata.get("num_boost_round"),
            "trainingEndDate": metadata.get("training_end_date"),
            "constituentSource": constituent_source,
        },
        "referenceRows": frame_records(reference_base, reference_cache_columns(reference_base)),
        "breadthRows": frame_records(breadth),
        "referenceScores": frame_records(reference_scores),
        "referenceRankings": reference_rankings,
        "marketRows": market_rows,
        "sectorRows": sector_rows,
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")


def sp500_comparison(row: pd.Series, reference_scores: pd.Series) -> dict:
    if reference_scores.empty:
        return {"sp500Rank": None, "sp500UniverseCount": 0, "sp500Percentile": None}
    rank = int((reference_scores > row["model_score"]).sum() + 1)
    universe_count = int(len(reference_scores))
    percentile = 100 * (1 - (rank - 1) / max(1, universe_count - 1))
    return {
        "sp500Rank": rank,
        "sp500UniverseCount": universe_count,
        "sp500Percentile": finite_or_none(percentile, 1),
    }


def row_payload(
    row: pd.Series,
    name_lookup: dict[str, str],
    sector_diagnostics: dict[str, dict] | None = None,
    reference_scores: pd.Series | None = None,
    dashboard_metrics: dict[str, dict] | None = None,
) -> dict:
    rank = int(row["model_rank"])
    universe_count = int(row["model_universe_count"])
    bucket = "Top Decile" if rank <= max(1, int(np.ceil(universe_count * 0.1))) else "Top Quintile" if rank <= max(1, int(np.ceil(universe_count * 0.2))) else "Ranked"
    diagnostic = (sector_diagnostics or {}).get(str(row["symbol"]), {})
    symbol = str(row["symbol"])
    metrics = (dashboard_metrics or {}).get(symbol, {})
    setup = setup_type(row)
    payload = {
        "symbol": symbol,
        "name": name_lookup.get(symbol, symbol),
        "sector": row["sector"],
        "modelRank": rank,
        "modelUniverseCount": universe_count,
        "modelScore": finite_or_none(row["model_score"], 6),
        "modelPercentile": finite_or_none(row["model_percentile"], 1),
        "modelBucket": bucket,
        "modelReasons": reason_tags(row),
        "riskFlags": risk_flags(row),
        "setupType": setup,
        "setupTags": setup_tags(row),
        "asOfDate": row["date"].date().isoformat(),
        "close": finite_or_none(row.get("close"), 2),
        "rsi14": finite_or_none(row.get("rsi_14"), 1),
        "beta60d": finite_or_none(row.get("beta_60d"), 2),
        "return20": finite_or_none(row.get("ret_20d", 0) * 100, 2),
        "return60": finite_or_none(row.get("ret_60d", 0) * 100, 2),
        "return120": finite_or_none(row.get("ret_120d", 0) * 100, 2),
        "relativeReturn60VsSpy": finite_or_none(row.get("rel_ret_60d_vs_spy", 0) * 100, 2),
        "relativeReturn20VsSpy": finite_or_none(row.get("rel_ret_20d_vs_spy", 0) * 100, 2),
        "sectorReturn60": finite_or_none(row.get("sector_ret_60d", 0) * 100, 2),
        "volatility60d": finite_or_none(row.get("volatility_60d"), 4),
        "volatility60dVsSector": finite_or_none(row.get("volatility_60d_minus_sector_median"), 4),
        "distanceTo52wHigh": finite_or_none(row.get("distance_to_52w_high", 0) * 100, 2),
        "above50": bool(row.get("price_vs_sma_50", 0) > 0),
        "above200": bool(row.get("price_vs_sma_200", 0) > 0),
        "sectorSource": diagnostic.get("sectorSource", "sp500_constituent"),
        "sectorEtf": diagnostic.get("sectorEtf"),
        "sectorCorrelation": diagnostic.get("sectorCorrelation"),
        "sectorCorrelationObservations": diagnostic.get("sectorCorrelationObservations"),
    }
    payload.update({key: value for key, value in metrics.items() if value is not None and key != "stopSellSignal"})
    payload.update(rebound_activation(row))
    if should_surface_stop_sell(row, setup):
        stop_sell_signal = metrics.get("stopSellSignal") or {}
        payload.update({key: value for key, value in stop_sell_signal.items() if value is not None})
    if reference_scores is not None:
        payload.update(sp500_comparison(row, reference_scores))
    if "price_data_date" in row and pd.notna(row.get("price_data_date")):
        price_data_date = row.get("price_data_date")
        payload["priceDataDate"] = (
            price_data_date.date().isoformat() if hasattr(price_data_date, "date") else str(price_data_date)
        )
    return payload


def load_reference_cache(path: Path) -> dict | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("status") != "ready" or not payload.get("referenceRows") or not payload.get("referenceScores"):
        return None
    try:
        assert_fresh_as_of(payload.get("asOfDate"), "S&P 500 reference cache")
    except ValueError as error:
        print(f"Reference cache unavailable; {error}")
        return None
    return payload


def score_focus_with_reference_cache(
    args: argparse.Namespace,
    cache: dict,
    model_path: Path,
    metadata: dict,
    feature_columns: list[str],
    focus_symbols: list[str],
) -> dict:
    reference_rows = records_frame(cache.get("referenceRows", []))
    breadth = records_frame(cache.get("breadthRows", []))
    reference_scores_frame = records_frame(cache.get("referenceScores", []))
    if reference_rows.empty or breadth.empty or reference_scores_frame.empty:
        raise ValueError("reference cache is missing required rows")

    reference_date = pd.to_datetime(cache.get("asOfDate") or reference_rows["date"].max())
    reference_rows["date"] = reference_date
    cached_rankings = {str(item["symbol"]): item for item in cache.get("referenceRankings", []) if item.get("symbol")}
    reference_symbols = set(reference_rows["symbol"].astype(str))
    cached_reference_scores = pd.to_numeric(reference_scores_frame["modelScore"], errors="coerce").dropna()

    results = []
    focus_unscored_symbols = []
    external_focus = []
    for symbol in focus_symbols:
        if symbol in cached_rankings:
            results.append(cached_rankings[symbol])
        elif symbol in reference_symbols:
            focus_unscored_symbols.append(symbol)
        else:
            external_focus.append(symbol)

    failures: dict[str, str] = {}
    sector_diagnostics: dict[str, dict] = {}
    dashboard_metrics: dict[str, dict] = {}
    if external_focus:
        required_symbols = list(dict.fromkeys([*external_focus, "SPY", *SECTOR_ETFS]))
        symbol_frames, failures = fetch_symbol_frames(required_symbols, args.years, args.max_workers)
        missing_context = [symbol for symbol in ["SPY", *SECTOR_ETFS] if symbol not in symbol_frames]
        if missing_context:
            raise ValueError(f"Missing required model context histories: {missing_context}")
        dashboard_metrics = build_dashboard_metrics(symbol_frames)

        additions = []
        for symbol in external_focus:
            frame = symbol_frames.get(symbol)
            if frame is None:
                continue
            latest_price_date = frame["date"].max().date()
            if latest_price_date < reference_date.date():
                failures[symbol] = (
                    f"stale price history: latest {latest_price_date.isoformat()}, "
                    f"expected {reference_date.date().isoformat()}"
                )
                continue
            diagnostic = infer_sector_for_symbol(symbol, frame, symbol_frames)
            sector_diagnostics[symbol] = diagnostic
            additions.append(
                {
                    "symbol": symbol,
                    "name": symbol,
                    "sector": diagnostic["sector"],
                    "sub_industry": "Focus ticker",
                }
            )

        if additions:
            external_constituents = pd.DataFrame(additions)
            spy = build_spy_context(symbol_frames["SPY"])
            sector_context = build_sector_context(symbol_frames)
            focus_history = build_base_feature_dataset(
                symbol_frames,
                external_constituents,
                breadth,
                sector_context,
                spy,
            )
            focus_history = focus_history[focus_history["date"].le(reference_date)].copy()
            context_columns = [
                column
                for column in ("spy_ret_20d", "breadth_above_50", "sector_ret_20d")
                if column in focus_history.columns
            ]
            focus_history = focus_history.dropna(subset=context_columns)
            focus_latest = focus_history.sort_values(["symbol", "date"]).groupby("symbol", as_index=False).tail(1)
            focus_latest["price_data_date"] = focus_latest["date"]
            focus_latest["date"] = reference_date
            focus_latest = focus_latest.merge(
                external_constituents[["symbol", "name", "sub_industry"]],
                on="symbol",
                how="left",
            )

            combined = pd.concat([reference_rows, focus_latest], ignore_index=True, sort=False)
            dataset = add_cross_sectional_model_features(combined, round_trip_cost=None)
            missing_features = [column for column in feature_columns if column not in dataset.columns]
            if missing_features:
                raise ValueError(f"Cached feature matrix is missing model features: {missing_features}")
            scored = score_rows(dataset, model_path, feature_columns)
            scored["model_universe_count"] = len(scored)
            scored_focus = scored[scored["symbol"].isin(external_focus)].copy()
            reference_name_lookup = (
                reference_rows.set_index("symbol")["name"].dropna().to_dict()
                if "name" in reference_rows.columns
                else {}
            )
            name_lookup = {**reference_name_lookup, **external_constituents.set_index("symbol")["name"].to_dict()}
            results.extend(
                row_payload(
                    row,
                    name_lookup,
                    sector_diagnostics,
                    reference_scores=cached_reference_scores,
                    dashboard_metrics=dashboard_metrics,
                )
                for _, row in scored_focus.iterrows()
            )
            focus_unscored_symbols.extend(
                sorted(set(external_focus) - set(scored_focus["symbol"].astype(str)) - set(failures))
            )

    results = sorted(results, key=lambda item: item.get("sp500Rank") or item.get("modelRank") or 999999)
    focus_failures = {symbol: failures[symbol] for symbol in focus_symbols if symbol in failures}
    notes = [
        "Focus tickers are scored with the same production XGBoost rank model as the S&P 500 dashboard.",
        f"S&P 500 reference scores come from the daily cache generated {cache.get('generatedAt', 'during the last full refresh')}.",
        "The daily refresh rebuilds the full S&P 500 reference universe; on-demand scoring only fetches pasted non-reference tickers plus SPY and sector ETF context.",
        "S&P 500 rank compares each focus ticker's model score against the cached current S&P 500 reference scores.",
        "Non-S&P tickers are assigned a sector proxy using trailing daily-return correlation to SPDR sector ETFs.",
        "International or exchange-suffix tickers may be less comparable because price currency, trading calendar, and volume conventions can differ from U.S. common stocks.",
    ]
    return {
        "status": "ready",
        "generatedAt": pd.Timestamp.now("UTC").isoformat(),
        "asOfDate": cache.get("asOfDate"),
        "model": cache.get(
            "model",
            {
                "name": args.model_name,
                "path": str(model_path.relative_to(ROOT)),
                "target": metadata.get("target_column"),
                "returnColumn": metadata.get("return_column"),
                "featureCount": len(feature_columns),
            },
        ),
        "scoredCount": len(results),
        "requestedSymbolCount": len(focus_symbols),
        "focusSymbols": focus_symbols,
        "focusRankings": results,
        "focusFailureCount": len(focus_failures),
        "focusFailures": focus_failures,
        "focusUnscoredSymbols": sorted(set(focus_unscored_symbols)),
        "referenceUniverse": cache.get("referenceUniverse", "Current S&P 500"),
        "referenceUniverseCount": int(cache.get("referenceUniverseCount") or len(cached_reference_scores)),
        "technicalTape": {
            "status": "ready",
            "source": "model_reference_cache",
            "asOfDate": cache.get("asOfDate"),
            "expectedAsOfDate": latest_expected_market_data_date().isoformat(),
        },
        "marketRows": cache.get("marketRows", []),
        "sectorRows": cache.get("sectorRows", []),
        "referenceCache": {
            "path": args.reference_cache,
            "generatedAt": cache.get("generatedAt"),
            "asOfDate": cache.get("asOfDate"),
        },
        "sectorDiagnostics": sector_diagnostics,
        "methodologyNotes": notes,
        "failedSymbolCount": len(failures),
        "unscoredSymbolCount": len(focus_unscored_symbols),
        "unscoredSymbols": sorted(set(focus_unscored_symbols)),
        "failures": failures,
        "rankings": results,
    }


def cached_dashboard_metrics(cache: dict) -> dict[str, dict]:
    keys = [
        "priceDataDate",
        "changePct",
        "return7",
        "return14",
        "return30",
        "return90",
        "ytdReturn",
        "volumeRatio",
        "above100",
        "history",
        "technicalSource",
        "stopSellPrice",
        "stopSellDistancePct",
        "stopSellAtr20",
        "stopSellRule",
        "stopSellBasis",
        "stopSellComponents",
    ]
    metrics: dict[str, dict] = {}
    for item in cache.get("referenceRankings", []):
        symbol = str(item.get("symbol") or "")
        if not symbol:
            continue
        metrics[symbol] = {key: item.get(key) for key in keys if key in item}
    return metrics


def score_reference_cache(
    args: argparse.Namespace,
    cache: dict,
    model_path: Path,
    metadata: dict,
    feature_columns: list[str],
) -> dict:
    reference_rows = records_frame(cache.get("referenceRows", []))
    if reference_rows.empty:
        raise ValueError("reference cache is missing referenceRows")

    reference_date = pd.to_datetime(cache.get("asOfDate") or reference_rows["date"].max())
    reference_rows["date"] = reference_date
    dataset = add_cross_sectional_model_features(reference_rows, round_trip_cost=None)
    missing_features = [column for column in feature_columns if column not in dataset.columns]
    if missing_features:
        raise ValueError(f"Cached feature matrix is missing model features: {missing_features}")

    scored = score_rows(dataset, model_path, feature_columns)
    scored["model_universe_count"] = len(scored)
    latest_date = scored["date"].max().date().isoformat() if not scored.empty else None
    assert_fresh_as_of(latest_date, "cached reference model rankings")

    name_lookup = {}
    if "name" in reference_rows.columns:
        name_lookup = reference_rows.set_index("symbol")["name"].dropna().astype(str).to_dict()
    dashboard_metrics = cached_dashboard_metrics(cache)
    rankings = [
        row_payload(row, name_lookup, dashboard_metrics=dashboard_metrics)
        for _, row in scored.iterrows()
    ]
    unscored_symbols = sorted(set(reference_rows["symbol"].astype(str)) - {item["symbol"] for item in rankings})
    return {
        "status": "ready",
        "generatedAt": pd.Timestamp.now("UTC").isoformat(),
        "asOfDate": latest_date,
        "model": {
            "name": args.model_name,
            "path": str(model_path.relative_to(ROOT)),
            "target": metadata.get("target_column"),
            "returnColumn": metadata.get("return_column"),
            "featureCount": len(feature_columns),
            "numBoostRound": metadata.get("num_boost_round"),
            "trainingEndDate": metadata.get("training_end_date"),
            "constituentSource": cache.get("model", {}).get("constituentSource"),
        },
        "scoredCount": len(rankings),
        "requestedSymbolCount": int(len(reference_rows)),
        "focusSymbols": [],
        "focusRankings": [],
        "focusFailureCount": 0,
        "focusFailures": {},
        "focusUnscoredSymbols": [],
        "referenceUniverse": cache.get("referenceUniverse") or "Current S&P 500",
        "referenceUniverseCount": int(len(rankings)),
        "technicalTape": {
            "status": "ready",
            "source": "model_reference_cache",
            "asOfDate": latest_date,
            "expectedAsOfDate": latest_expected_market_data_date().isoformat(),
        },
        "marketRows": cache.get("marketRows", []),
        "sectorRows": cache.get("sectorRows", []),
        "sectorDiagnostics": {},
        "methodologyNotes": [
            "This run scored the cached current S&P 500 feature matrix with the selected model to avoid duplicate price-history fetches.",
            "The cache is accepted only when its as-of date satisfies the same freshness guardrail as a full model scoring pass.",
        ],
        "failedSymbolCount": 0,
        "unscoredSymbolCount": len(unscored_symbols),
        "unscoredSymbols": unscored_symbols,
        "failures": {},
        "rankings": rankings,
    }


def main() -> None:
    args = parse_args()
    model_dir = ROOT / args.model_dir
    model_path = model_dir / f"{args.model_name}.json"
    metadata_path = model_dir / f"{args.model_name}_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    feature_columns = list(metadata["feature_columns"])

    focus_symbols = parse_focus_symbols(args.focus_symbols)
    cache_path = ROOT / args.reference_cache
    if args.score_reference_cache:
        cache = load_reference_cache(cache_path)
        if cache is None:
            raise SystemExit(f"Reference cache unavailable: {cache_path.relative_to(ROOT)}")
        try:
            payload = score_reference_cache(args, cache, model_path, metadata, feature_columns)
        except Exception as error:  # noqa: BLE001
            raise SystemExit(f"Unable to score reference cache: {error}") from error
        output_path = ROOT / args.output
        write_json(payload, output_path)
        print(f"Wrote {len(payload['rankings'])} cached live model rankings to {output_path.relative_to(ROOT)}")
        return

    if focus_symbols and not args.no_reference_cache:
        cache = load_reference_cache(cache_path)
        if cache is not None:
            try:
                payload = score_focus_with_reference_cache(args, cache, model_path, metadata, feature_columns, focus_symbols)
                output_path = ROOT / args.output
                write_json(payload, output_path)
                print(f"Wrote {len(payload['focusRankings'])} focus rankings using {cache_path.relative_to(ROOT)}")
                return
            except Exception as error:  # noqa: BLE001 - cache fallback should not block scoring
                print(f"Reference cache unavailable; rebuilding full universe: {error}")

    constituents, constituent_source = load_constituents(args.max_symbols)
    reference_symbols = set(constituents["symbol"].astype(str))
    required_symbols = list(dict.fromkeys([*constituents["symbol"].tolist(), *focus_symbols, "SPY", *SECTOR_ETFS, *MARKET_STRIP_SYMBOLS]))
    symbol_frames, failures = fetch_symbol_frames(required_symbols, args.years, args.max_workers)
    missing_context = [symbol for symbol in ["SPY", *SECTOR_ETFS] if symbol not in symbol_frames]
    if missing_context:
        raise SystemExit(f"Missing required model context histories: {missing_context}")

    dashboard_metrics = build_dashboard_metrics(symbol_frames)
    market_rows = build_market_rows(symbol_frames, dashboard_metrics)
    sector_rows = build_sector_rows(dashboard_metrics)
    constituents, sector_diagnostics = append_focus_constituents(constituents, focus_symbols, symbol_frames)
    spy = build_spy_context(symbol_frames["SPY"])
    reference_frames = {
        symbol: frame
        for symbol, frame in symbol_frames.items()
        if symbol in reference_symbols or symbol in {"SPY", *SECTOR_ETFS}
    }
    reference_constituents = constituents[constituents["symbol"].isin(reference_symbols)].copy()
    breadth, sector_context = build_market_context(reference_frames, reference_constituents)
    base_dataset = build_base_feature_dataset(symbol_frames, constituents, breadth, sector_context, spy)
    dataset = add_cross_sectional_model_features(base_dataset, round_trip_cost=None)
    missing_features = [column for column in feature_columns if column not in dataset.columns]
    if missing_features:
        raise SystemExit(f"Live feature matrix is missing model features: {missing_features}")

    scored = score_rows(dataset, model_path, feature_columns)
    scored["model_universe_count"] = len(scored)
    latest_date = scored["date"].max().date().isoformat() if not scored.empty else None
    try:
        assert_fresh_as_of(latest_date, "live model rankings")
    except ValueError as error:
        raise SystemExit(str(error)) from error
    name_lookup = constituents.set_index("symbol")["name"].to_dict()
    reference_scores = scored[scored["symbol"].isin(reference_symbols)]["model_score"]
    rankings = [row_payload(row, name_lookup, sector_diagnostics, dashboard_metrics=dashboard_metrics) for _, row in scored.iterrows()]
    focus_scored = scored[scored["symbol"].isin(focus_symbols)].copy()
    focus_rankings = [
        row_payload(row, name_lookup, sector_diagnostics, reference_scores=reference_scores, dashboard_metrics=dashboard_metrics)
        for _, row in focus_scored.iterrows()
    ]
    scored_symbols = set(scored["symbol"].astype(str))
    unscored_symbols = sorted(set(constituents["symbol"].astype(str)) - scored_symbols - set(failures))
    focus_failures = {symbol: failures[symbol] for symbol in focus_symbols if symbol in failures}
    focus_unscored_symbols = sorted(set(focus_symbols) - set(focus_scored["symbol"].astype(str)) - set(focus_failures))

    payload = {
        "status": "ready",
        "generatedAt": pd.Timestamp.now("UTC").isoformat(),
        "asOfDate": latest_date,
        "model": {
            "name": args.model_name,
            "path": str(model_path.relative_to(ROOT)),
            "target": metadata.get("target_column"),
            "returnColumn": metadata.get("return_column"),
            "featureCount": len(feature_columns),
            "numBoostRound": metadata.get("num_boost_round"),
            "trainingEndDate": metadata.get("training_end_date"),
            "constituentSource": constituent_source,
        },
        "scoredCount": len(rankings),
        "requestedSymbolCount": len(constituents),
        "focusSymbols": focus_symbols,
        "focusRankings": focus_rankings,
        "focusFailureCount": len(focus_failures),
        "focusFailures": focus_failures,
        "focusUnscoredSymbols": focus_unscored_symbols,
        "referenceUniverse": "Current S&P 500",
        "referenceUniverseCount": int(len(reference_scores)),
        "technicalTape": {
            "status": "ready",
            "source": "model_scorer_yahoo_history",
            "asOfDate": latest_date,
            "expectedAsOfDate": latest_expected_market_data_date().isoformat(),
        },
        "marketRows": market_rows,
        "sectorRows": sector_rows,
        "sectorDiagnostics": {symbol: sector_diagnostics.get(symbol) for symbol in focus_symbols if symbol in sector_diagnostics},
        "methodologyNotes": [
            "Focus tickers are scored with the same production XGBoost rank model as the S&P 500 dashboard.",
            "S&P 500 rank compares each focus ticker's model score against the current S&P 500 reference scores.",
            "Non-S&P tickers are assigned a sector proxy using trailing daily-return correlation to SPDR sector ETFs.",
            "International or exchange-suffix tickers may be less comparable because price currency, trading calendar, and volume conventions can differ from U.S. common stocks.",
        ],
        "failedSymbolCount": len(failures),
        "unscoredSymbolCount": len(unscored_symbols),
        "unscoredSymbols": unscored_symbols,
        "failures": failures,
        "rankings": rankings,
    }
    output_path = ROOT / args.output
    write_json(payload, output_path)
    should_write_cache = args.max_symbols == 0 or args.reference_cache != "data/model-reference-cache.json"
    if should_write_cache:
        write_reference_cache(
            cache_path,
            base_dataset,
            breadth,
            scored,
            constituents,
            reference_symbols,
            metadata,
            args.model_name,
            model_path,
            constituent_source,
            name_lookup,
            dashboard_metrics,
            market_rows,
            sector_rows,
        )
        print(f"Wrote S&P 500 reference cache to {cache_path.relative_to(ROOT)}")
    print(f"Wrote {len(rankings)} live model rankings to {output_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
