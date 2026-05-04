from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from common import FEATURES_DIR, MACRO_DIR, PRICES_DIR, REFERENCE_DIR, ROOT, SECTOR_ETFS, write_json
from schema import (
    LEGACY_TARGET_COLUMN,
    META_TARGET_COLUMN,
    RANK_RETURN_COLUMN,
    RANK_TARGET_COLUMN,
    SECTOR_HURDLE_TARGET_COLUMN,
    SECTOR_POSITIVE_TARGET_COLUMN,
    TARGET_HORIZON,
)


def unique_columns(columns: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for column in columns:
        if column not in seen:
            result.append(column)
            seen.add(column)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a labeled training dataset for the Market Pulse XGBoost model.")
    parser.add_argument("--output-name", default="training_dataset.csv.gz", help="Name of the generated feature dataset.")
    parser.add_argument("--round-trip-cost-bps", type=float, default=15.0, help="Cost deducted from forward sector-neutral labels.")
    parser.add_argument("--meta-return-hurdle", type=float, default=0.01, help="Minimum after-cost sector-neutral return for a successful setup.")
    parser.add_argument("--meta-drawdown-floor", type=float, default=-0.02, help="Minimum acceptable adjusted-close drawdown for a successful setup.")
    parser.add_argument(
        "--include-macro",
        action="store_true",
        help="Include FRED macro observation-date features. Disabled by default to avoid release-date leakage.",
    )
    return parser.parse_args()


def load_price(symbol: str) -> pd.DataFrame:
    frame = pd.read_csv(PRICES_DIR / f"{symbol}.csv")
    frame["date"] = pd.to_datetime(frame["date"])
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
    frame["symbol"] = symbol
    return frame


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window).mean()


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gains = delta.clip(lower=0.0)
    losses = -delta.clip(upper=0.0)
    avg_gain = gains.rolling(window).mean()
    avg_loss = losses.rolling(window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100 - (100 / (1 + rs))
    return result.fillna(50)


def enrich_price_features(frame: pd.DataFrame) -> pd.DataFrame:
    close = frame["close"]
    volume = frame["volume"].fillna(0)
    frame["ret_1d"] = close.pct_change(1)
    frame["ret_5d"] = close.pct_change(5)
    frame["ret_10d"] = close.pct_change(10)
    frame["ret_20d"] = close.pct_change(20)
    frame["ret_60d"] = close.pct_change(60)
    frame["ret_120d"] = close.pct_change(120)
    frame["volatility_20d"] = frame["ret_1d"].rolling(20).std()
    frame["volatility_60d"] = frame["ret_1d"].rolling(60).std()
    frame["downside_volatility_20d"] = frame["ret_1d"].clip(upper=0).rolling(20).std()
    frame["downside_volatility_60d"] = frame["ret_1d"].clip(upper=0).rolling(60).std()
    frame["max_daily_return_20d"] = frame["ret_1d"].rolling(20).max()
    frame["ret_5d_reversal"] = -frame["ret_5d"]
    frame["momentum_126d_skip_10"] = close.shift(10) / close.shift(126) - 1
    frame["momentum_252d_skip_20"] = close.shift(20) / close.shift(252) - 1
    frame["sma_20"] = sma(close, 20)
    frame["sma_50"] = sma(close, 50)
    frame["sma_100"] = sma(close, 100)
    frame["sma_200"] = sma(close, 200)
    frame["price_vs_sma_20"] = close / frame["sma_20"] - 1
    frame["price_vs_sma_50"] = close / frame["sma_50"] - 1
    frame["price_vs_sma_100"] = close / frame["sma_100"] - 1
    frame["price_vs_sma_200"] = close / frame["sma_200"] - 1
    frame["sma_20_vs_50"] = frame["sma_20"] / frame["sma_50"] - 1
    frame["sma_50_vs_200"] = frame["sma_50"] / frame["sma_200"] - 1
    frame["rsi_14"] = rsi(close, 14)
    frame["avg_volume_20"] = volume.rolling(20).mean()
    frame["avg_volume_60"] = volume.rolling(60).mean()
    frame["volume_ratio_20"] = volume / frame["avg_volume_20"].replace(0, np.nan)
    frame["volume_ratio_60"] = volume / frame["avg_volume_60"].replace(0, np.nan)
    frame["dollar_volume_20d"] = close * frame["avg_volume_20"]
    frame["log_dollar_volume_20d"] = np.log1p(frame["dollar_volume_20d"])
    frame["amihud_20d"] = (frame["ret_1d"].abs() / (close * volume).replace(0, np.nan)).rolling(20).mean()
    frame["ret_20d_vol_adj"] = frame["ret_20d"] / frame["volatility_20d"].replace(0, np.nan)
    frame["ret_60d_vol_adj"] = frame["ret_60d"] / frame["volatility_60d"].replace(0, np.nan)
    frame["high_252"] = close.rolling(252).max()
    frame["low_252"] = close.rolling(252).min()
    frame["distance_to_52w_high"] = close / frame["high_252"] - 1
    frame["distance_to_52w_low"] = close / frame["low_252"] - 1
    frame["forward_return_14d"] = close.shift(-TARGET_HORIZON) / close - 1
    entry_close = close.shift(-1)
    exit_close = close.shift(-(TARGET_HORIZON + 1))
    frame["forward_return_14d_next_close"] = exit_close / entry_close - 1

    future_path = pd.concat(
        [close.shift(-offset).rename(offset) for offset in range(1, TARGET_HORIZON + 2)],
        axis=1,
    )
    frame["max_drawdown_14d_next_close"] = future_path.div(entry_close, axis=0).sub(1).min(axis=1)
    return frame


def load_macro_features() -> pd.DataFrame:
    pieces = []
    for path in sorted(MACRO_DIR.glob("*.csv")):
        series_id = path.stem
        frame = pd.read_csv(path)
        frame.columns = [str(column).strip().lower() for column in frame.columns]
        date_column = "date" if "date" in frame.columns else frame.columns[0]
        value_column = "value" if "value" in frame.columns else frame.columns[1]
        frame = frame.rename(columns={date_column: "date", value_column: "value"})
        frame["date"] = pd.to_datetime(frame["date"])
        frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
        frame = frame.dropna(subset=["date", "value"]).sort_values("date")
        frame[f"{series_id.lower()}_level"] = frame["value"]
        frame[f"{series_id.lower()}_chg_1"] = frame["value"].diff(1)
        frame[f"{series_id.lower()}_chg_5"] = frame["value"].diff(5)
        pieces.append(frame[["date", f"{series_id.lower()}_level", f"{series_id.lower()}_chg_1", f"{series_id.lower()}_chg_5"]])

    if not pieces:
        return pd.DataFrame(columns=["date"])

    macro = pieces[0]
    for piece in pieces[1:]:
        macro = macro.merge(piece, on="date", how="outer")
    macro = macro.sort_values("date").ffill()
    return macro


def build_market_context(symbol_frames: dict[str, pd.DataFrame], constituents: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    stock_frames = []
    for symbol, frame in symbol_frames.items():
        if symbol in {"SPY", *SECTOR_ETFS}:
            continue
        stock_frames.append(
            frame[
                [
                    "date",
                    "symbol",
                    "close",
                    "ret_20d",
                    "ret_60d",
                    "ret_120d",
                    "volatility_20d",
                    "volatility_60d",
                    "price_vs_sma_50",
                    "price_vs_sma_200",
                ]
            ].copy()
        )

    combined = pd.concat(stock_frames, ignore_index=True)
    breadth = combined.assign(
        above_50=(combined["price_vs_sma_50"] > 0).astype(int),
        above_200=(combined["price_vs_sma_200"] > 0).astype(int),
    )
    breadth = (
        breadth.groupby("date")
        .agg(
            breadth_above_50=("above_50", "mean"),
            breadth_above_200=("above_200", "mean"),
            breadth_ret_20d_median=("ret_20d", "median"),
            breadth_ret_60d_median=("ret_60d", "median"),
            breadth_ret_120d_median=("ret_120d", "median"),
            breadth_volatility_20d_median=("volatility_20d", "median"),
            breadth_volatility_60d_median=("volatility_60d", "median"),
        )
        .reset_index()
    )

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
                "max_drawdown_14d_next_close",
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
                "max_drawdown_14d_next_close": "sector_max_drawdown_14d_next_close",
            }
        )
        frame["sector"] = sector_name_for_etf(etf)
        sector_frames.append(frame)

    sector_context = pd.concat(sector_frames, ignore_index=True)
    return breadth, sector_context


def sector_name_for_etf(symbol: str) -> str:
    mapping = {
        "XLK": "Information Technology",
        "XLC": "Communication Services",
        "XLY": "Consumer Discretionary",
        "XLP": "Consumer Staples",
        "XLF": "Financials",
        "XLV": "Health Care",
        "XLI": "Industrials",
        "XLE": "Energy",
        "XLU": "Utilities",
        "XLB": "Materials",
        "XLRE": "Real Estate",
    }
    return mapping[symbol]


def main() -> None:
    args = parse_args()
    round_trip_cost = args.round_trip_cost_bps / 10000
    constituents = pd.read_csv(REFERENCE_DIR / "sp500_constituents.csv")
    constituents["symbol"] = constituents["symbol"].astype(str)

    symbol_frames: dict[str, pd.DataFrame] = {}
    for symbol in [*constituents["symbol"].tolist(), "SPY", *SECTOR_ETFS]:
        path = PRICES_DIR / f"{symbol}.csv"
        if not path.exists():
            continue
        symbol_frames[symbol] = enrich_price_features(load_price(symbol))

    spy = symbol_frames["SPY"][
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
    spy = spy.rename(
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

    macro = load_macro_features() if args.include_macro else pd.DataFrame(columns=["date"])
    breadth, sector_context = build_market_context(symbol_frames, constituents)
    dataset_frames = []
    sector_lookup = constituents.set_index("symbol")["sector"].to_dict()

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
        if not macro.empty:
            frame = frame.merge(macro, on="date", how="left")
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

    dataset = pd.concat(dataset_frames, ignore_index=True)
    dataset = dataset.sort_values(["date", "symbol"]).reset_index(drop=True)

    rank_base_columns = [
        "ret_5d_reversal",
        "ret_20d",
        "ret_60d",
        "ret_120d",
        "momentum_126d_skip_10",
        "momentum_252d_skip_20",
        "ret_20d_vol_adj",
        "ret_60d_vol_adj",
        "rsi_14",
        "volatility_20d",
        "volatility_60d",
        "downside_volatility_20d",
        "downside_volatility_60d",
        "max_daily_return_20d",
        "price_vs_sma_50",
        "price_vs_sma_200",
        "distance_to_52w_high",
        "distance_to_52w_low",
        "volume_ratio_20",
        "volume_ratio_60",
        "log_dollar_volume_20d",
        "amihud_20d",
        "beta_60d",
        "rel_ret_20d_vs_spy",
        "rel_ret_60d_vs_spy",
        "rel_ret_120d_vs_spy",
        "rel_rsi_vs_spy",
        "rel_volatility_20d_vs_spy",
        "rel_volatility_60d_vs_spy",
        "idiosyncratic_ret_20d",
        "idiosyncratic_ret_60d",
    ]
    rank_features = []
    for column in rank_base_columns:
        rank_features.append(dataset.groupby("date")[column].rank(pct=True).rename(f"{column}_pct_rank"))
        rank_features.append(
            dataset.groupby(["date", "sector"])[column].rank(pct=True).rename(f"{column}_sector_pct_rank")
        )
    dataset = pd.concat([dataset, *rank_features], axis=1).copy()

    sector_median_columns = [
        "ret_20d",
        "ret_60d",
        "ret_120d",
        "momentum_126d_skip_10",
        "momentum_252d_skip_20",
        "ret_20d_vol_adj",
        "ret_60d_vol_adj",
        "rsi_14",
        "volatility_20d",
        "volatility_60d",
        "downside_volatility_20d",
        "downside_volatility_60d",
        "max_daily_return_20d",
        "volume_ratio_20",
        "volume_ratio_60",
        "distance_to_52w_high",
        "distance_to_52w_low",
        "beta_60d",
        "idiosyncratic_ret_20d",
        "idiosyncratic_ret_60d",
    ]
    sector_median_features = []
    for column in sector_median_columns:
        sector_median = dataset.groupby(["date", "sector"])[column].transform("median")
        sector_median_features.append((dataset[column] - sector_median).rename(f"{column}_minus_sector_median"))
    dataset = pd.concat([dataset, *sector_median_features], axis=1).copy()

    derived_features = pd.DataFrame(
        {
            "rel_ret_20d_vs_sector_etf": dataset["ret_20d"] - dataset["sector_ret_20d"],
            "rel_ret_60d_vs_sector_etf": dataset["ret_60d"] - dataset["sector_ret_60d"],
            "rel_ret_120d_vs_sector_etf": dataset["ret_120d"] - dataset["sector_ret_120d"],
            "rel_momentum_126d_vs_sector_etf": dataset["momentum_126d_skip_10"]
            - dataset["sector_momentum_126d_skip_10"],
            "rel_momentum_252d_vs_sector_etf": dataset["momentum_252d_skip_20"]
            - dataset["sector_momentum_252d_skip_20"],
            "volatility_20d_vs_sector_etf": dataset["volatility_20d"] - dataset["sector_volatility_20d"],
            "volatility_60d_vs_sector_etf": dataset["volatility_60d"] - dataset["sector_volatility_60d"],
            "price_vs_sector_sma_50": dataset["price_vs_sma_50"] - dataset["sector_price_vs_sma_50"],
            "price_vs_sector_sma_200": dataset["price_vs_sma_200"] - dataset["sector_price_vs_sma_200"],
            "rsi_vs_sector_etf": dataset["rsi_14"] - dataset["sector_rsi_14"],
            "sector_neutral_forward_return_14d": dataset["forward_return_14d_next_close"]
            - dataset["sector_forward_return_14d_next_close"],
        },
        index=dataset.index,
    )
    derived_features[RANK_RETURN_COLUMN] = derived_features["sector_neutral_forward_return_14d"] - round_trip_cost
    dataset = pd.concat([dataset, derived_features], axis=1).copy()
    technical_composite_score = dataset[
        [
            "ret_60d_sector_pct_rank",
            "ret_120d_sector_pct_rank",
            "momentum_126d_skip_10_sector_pct_rank",
            "ret_60d_vol_adj_sector_pct_rank",
            "price_vs_sma_200_sector_pct_rank",
            "distance_to_52w_high_sector_pct_rank",
            "rel_ret_60d_vs_spy_pct_rank",
            "idiosyncratic_ret_60d_sector_pct_rank",
        ]
    ].mean(axis=1).rename("technical_composite_score")
    dataset = pd.concat([dataset, technical_composite_score], axis=1).copy()

    raw_price_feature_columns = [
        "ret_1d",
        "ret_5d",
        "ret_10d",
        "ret_20d",
        "ret_60d",
        "ret_120d",
        "ret_5d_reversal",
        "momentum_126d_skip_10",
        "momentum_252d_skip_20",
        "volatility_20d",
        "volatility_60d",
        "downside_volatility_20d",
        "downside_volatility_60d",
        "max_daily_return_20d",
        "ret_20d_vol_adj",
        "ret_60d_vol_adj",
        "price_vs_sma_20",
        "price_vs_sma_50",
        "price_vs_sma_100",
        "price_vs_sma_200",
        "sma_20_vs_50",
        "sma_50_vs_200",
        "rsi_14",
        "volume_ratio_20",
        "volume_ratio_60",
        "distance_to_52w_high",
        "distance_to_52w_low",
        "log_dollar_volume_20d",
        "amihud_20d",
        "beta_60d",
        "idiosyncratic_ret_20d",
        "idiosyncratic_ret_60d",
    ]
    market_context_feature_columns = [
        "spy_ret_20d",
        "spy_ret_60d",
        "spy_ret_120d",
        "spy_volatility_20d",
        "spy_volatility_60d",
        "spy_price_vs_sma_50",
        "spy_price_vs_sma_200",
        "spy_rsi_14",
        "rel_ret_20d_vs_spy",
        "rel_ret_60d_vs_spy",
        "rel_ret_120d_vs_spy",
        "rel_rsi_vs_spy",
        "rel_volatility_20d_vs_spy",
        "rel_volatility_60d_vs_spy",
        "breadth_above_50",
        "breadth_above_200",
        "breadth_ret_20d_median",
        "breadth_ret_60d_median",
        "breadth_ret_120d_median",
        "breadth_volatility_20d_median",
        "breadth_volatility_60d_median",
    ]
    sector_context_feature_columns = [
        "sector_ret_20d",
        "sector_ret_60d",
        "sector_ret_120d",
        "sector_momentum_126d_skip_10",
        "sector_momentum_252d_skip_20",
        "sector_volatility_20d",
        "sector_volatility_60d",
        "sector_price_vs_sma_50",
        "sector_price_vs_sma_200",
        "sector_rsi_14",
    ]
    relative_sector_feature_columns = [
        "rel_ret_20d_vs_sector_etf",
        "rel_ret_60d_vs_sector_etf",
        "rel_ret_120d_vs_sector_etf",
        "rel_momentum_126d_vs_sector_etf",
        "rel_momentum_252d_vs_sector_etf",
        "volatility_20d_vs_sector_etf",
        "volatility_60d_vs_sector_etf",
        "price_vs_sector_sma_50",
        "price_vs_sector_sma_200",
        "rsi_vs_sector_etf",
    ]
    feature_columns = unique_columns(
        [
            *raw_price_feature_columns,
            *market_context_feature_columns,
            *sector_context_feature_columns,
            *[f"{column}_pct_rank" for column in rank_base_columns],
            *[f"{column}_sector_pct_rank" for column in rank_base_columns],
            *[f"{column}_minus_sector_median" for column in sector_median_columns],
            *relative_sector_feature_columns,
            "technical_composite_score",
        ]
    )
    macro_feature_columns = [
        column
        for column in dataset.columns
        if column.endswith("_level") or column.endswith("_chg_1") or column.endswith("_chg_5")
    ]
    feature_columns.extend(column for column in macro_feature_columns if column not in feature_columns)
    missing_feature_columns = [column for column in feature_columns if column not in dataset.columns]
    if missing_feature_columns:
        raise ValueError(f"Feature columns missing from dataset: {missing_feature_columns}")

    target_columns = [
        "forward_return_14d",
        "spy_forward_return_14d",
        "excess_return_14d",
        "forward_return_14d_next_close",
        "sector_forward_return_14d_next_close",
        "sector_neutral_forward_return_14d",
        RANK_RETURN_COLUMN,
        "max_drawdown_14d_next_close",
    ]
    dataset = dataset.replace([np.inf, -np.inf], np.nan)
    dataset = dataset.dropna(subset=[*target_columns, *feature_columns])
    dataset[LEGACY_TARGET_COLUMN] = (dataset["excess_return_14d"] > 0).astype(int)
    dataset[SECTOR_POSITIVE_TARGET_COLUMN] = (dataset[RANK_RETURN_COLUMN] > 0).astype(int)
    dataset[SECTOR_HURDLE_TARGET_COLUMN] = (
        dataset[RANK_RETURN_COLUMN] > args.meta_return_hurdle
    ).astype(int)
    dataset["sector_neutral_forward_return_pct_rank"] = dataset.groupby("date")[
        RANK_RETURN_COLUMN
    ].rank(pct=True)
    dataset[RANK_TARGET_COLUMN] = np.select(
        [
            dataset["sector_neutral_forward_return_pct_rank"] >= 0.90,
            dataset["sector_neutral_forward_return_pct_rank"] >= 0.80,
            dataset["sector_neutral_forward_return_pct_rank"] <= 0.10,
            dataset["sector_neutral_forward_return_pct_rank"] <= 0.20,
        ],
        [4, 3, 0, 1],
        default=2,
    ).astype(int)
    dataset["candidate_momentum_setup"] = (
        (dataset["technical_composite_score"] >= 0.75)
        & (dataset["ret_60d_minus_sector_median"] > 0)
        & (dataset["ret_60d_sector_pct_rank"] >= 0.70)
        & (dataset["momentum_126d_skip_10_sector_pct_rank"] >= 0.65)
        & (dataset["price_vs_sma_50"] > 0)
        & (dataset["price_vs_sma_200"] > 0)
        & (dataset["rsi_14"] <= 78)
        & (dataset["volume_ratio_20"] >= 0.70)
    ).astype(int)
    dataset[META_TARGET_COLUMN] = np.where(
        dataset["candidate_momentum_setup"].eq(1),
        (
            (dataset[RANK_RETURN_COLUMN] > args.meta_return_hurdle)
            & (dataset["max_drawdown_14d_next_close"] > args.meta_drawdown_floor)
        ).astype(int),
        np.nan,
    )
    output_columns = [
        "date",
        "symbol",
        "sector",
        "close",
        "forward_return_14d",
        "spy_forward_return_14d",
        "excess_return_14d",
        "forward_return_14d_next_close",
        "sector_forward_return_14d_next_close",
        "sector_neutral_forward_return_14d",
        RANK_RETURN_COLUMN,
        "max_drawdown_14d_next_close",
        "sector_max_drawdown_14d_next_close",
        LEGACY_TARGET_COLUMN,
        SECTOR_POSITIVE_TARGET_COLUMN,
        SECTOR_HURDLE_TARGET_COLUMN,
        "sector_neutral_forward_return_pct_rank",
        RANK_TARGET_COLUMN,
        "candidate_momentum_setup",
        META_TARGET_COLUMN,
        *feature_columns,
    ]
    dataset = dataset[output_columns]

    output_path = FEATURES_DIR / args.output_name
    output_path.parent.mkdir(parents=True, exist_ok=True)
    dataset.to_csv(output_path, index=False, compression="gzip")

    metadata = {
        "generated_at": pd.Timestamp.now("UTC").isoformat(),
        "row_count": int(len(dataset)),
        "symbol_count": int(dataset["symbol"].nunique()),
        "start_date": str(dataset["date"].min().date()),
        "end_date": str(dataset["date"].max().date()),
        "label_positive_rate": float(dataset[LEGACY_TARGET_COLUMN].mean()),
        "sector_neutral_positive_rate": float(dataset[SECTOR_POSITIVE_TARGET_COLUMN].mean()),
        "sector_neutral_hurdle_rate": float(dataset[SECTOR_HURDLE_TARGET_COLUMN].mean()),
        "candidate_rows": int(dataset["candidate_momentum_setup"].sum()),
        "candidate_success_rate": float(dataset.loc[dataset["candidate_momentum_setup"].eq(1), META_TARGET_COLUMN].mean()),
        "feature_columns": feature_columns,
        "include_macro": bool(args.include_macro),
        "target_horizon_days": TARGET_HORIZON,
        "round_trip_cost_bps": args.round_trip_cost_bps,
        "meta_return_hurdle": args.meta_return_hurdle,
        "meta_drawdown_floor": args.meta_drawdown_floor,
    }
    write_json(metadata, FEATURES_DIR / "training_dataset_metadata.json")
    print(f"Wrote {len(dataset)} rows across {dataset['symbol'].nunique()} symbols to {output_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
