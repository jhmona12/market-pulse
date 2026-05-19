from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from build_training_dataset import (
    build_market_context,
    enrich_price_features,
    load_macro_features,
    load_price,
    sector_name_for_etf,
)
from common import FEATURES_DIR, PRICES_DIR, REFERENCE_DIR, ROOT, SECTOR_ETFS, write_json
from model_features import add_cross_sectional_model_features, model_feature_columns
from schema import (
    LONG_HORIZON_DAYS,
    LONG_LEGACY_TARGET_COLUMN,
    LONG_RANK_RETURN_COLUMN,
    LONG_RANK_TARGET_COLUMN,
    LONG_SECTOR_HURDLE_TARGET_COLUMN,
    LONG_SECTOR_POSITIVE_TARGET_COLUMN,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a clean 252-trading-day labeled dataset for the long-horizon rank model."
    )
    parser.add_argument(
        "--output-name",
        default="long_horizon_training_dataset.csv.gz",
        help="Name of the generated feature dataset inside data/modeling/features.",
    )
    parser.add_argument("--horizon-days", type=int, default=LONG_HORIZON_DAYS, help="Forward holding horizon.")
    parser.add_argument(
        "--round-trip-cost-bps",
        type=float,
        default=15.0,
        help="Cost deducted from forward sector-neutral labels.",
    )
    parser.add_argument(
        "--sector-hurdle",
        type=float,
        default=0.03,
        help="Diagnostic annual sector-neutral return hurdle for the positive-hurdle label.",
    )
    parser.add_argument(
        "--include-macro",
        action="store_true",
        help="Include FRED macro observation-date features. Disabled by default to avoid release-date leakage.",
    )
    return parser.parse_args()


def add_forward_path_labels(frame: pd.DataFrame, horizon_days: int) -> pd.DataFrame:
    frame = frame.copy()
    close = frame["close"]
    entry_close = close.shift(-1)
    exit_close = close.shift(-(horizon_days + 1))
    frame[f"forward_return_{horizon_days}d_next_close"] = exit_close / entry_close - 1
    future_path = pd.concat(
        [close.shift(-offset).rename(offset) for offset in range(1, horizon_days + 2)],
        axis=1,
    )
    frame[f"max_drawdown_{horizon_days}d_next_close"] = future_path.div(entry_close, axis=0).sub(1).min(axis=1)
    return frame


def build_sector_context_long(symbol_frames: dict[str, pd.DataFrame], horizon_days: int) -> pd.DataFrame:
    sector_frames = []
    for etf in SECTOR_ETFS:
        frame = add_forward_path_labels(symbol_frames[etf], horizon_days)
        frame = frame[
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
                f"forward_return_{horizon_days}d_next_close",
                f"max_drawdown_{horizon_days}d_next_close",
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
                f"forward_return_{horizon_days}d_next_close": f"sector_forward_return_{horizon_days}d_next_close",
                f"max_drawdown_{horizon_days}d_next_close": f"sector_max_drawdown_{horizon_days}d_next_close",
            }
        )
        frame["sector"] = sector_name_for_etf(etf)
        sector_frames.append(frame)
    return pd.concat(sector_frames, ignore_index=True)


def main() -> None:
    args = parse_args()
    horizon = args.horizon_days
    if horizon != LONG_HORIZON_DAYS:
        raise ValueError("This script is intentionally reserved for the 252-day long-horizon model.")

    round_trip_cost = args.round_trip_cost_bps / 10000
    constituents = pd.read_csv(REFERENCE_DIR / "sp500_constituents.csv")
    constituents["symbol"] = constituents["symbol"].astype(str)

    symbol_frames: dict[str, pd.DataFrame] = {}
    for symbol in [*constituents["symbol"].tolist(), "SPY", *SECTOR_ETFS]:
        path = PRICES_DIR / f"{symbol}.csv"
        if not path.exists():
            continue
        symbol_frames[symbol] = add_forward_path_labels(enrich_price_features(load_price(symbol)), horizon)

    missing_context = [symbol for symbol in ["SPY", *SECTOR_ETFS] if symbol not in symbol_frames]
    if missing_context:
        raise ValueError(f"Missing required market/sector context price histories: {missing_context}")

    spy = symbol_frames["SPY"][
        [
            "date",
            f"forward_return_{horizon}d_next_close",
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
            f"forward_return_{horizon}d_next_close": f"spy_forward_return_{horizon}d_next_close",
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
    breadth, _ = build_market_context(symbol_frames, constituents)
    sector_context = build_sector_context_long(symbol_frames, horizon)
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
        frame[f"excess_return_{horizon}d"] = (
            frame[f"forward_return_{horizon}d_next_close"] - frame[f"spy_forward_return_{horizon}d_next_close"]
        )
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
    dataset = add_cross_sectional_model_features(dataset, round_trip_cost=None)
    dataset[f"sector_neutral_forward_return_{horizon}d"] = (
        dataset[f"forward_return_{horizon}d_next_close"] - dataset[f"sector_forward_return_{horizon}d_next_close"]
    )
    dataset[LONG_RANK_RETURN_COLUMN] = dataset[f"sector_neutral_forward_return_{horizon}d"] - round_trip_cost
    feature_columns = model_feature_columns(dataset)
    missing_feature_columns = [column for column in feature_columns if column not in dataset.columns]
    if missing_feature_columns:
        raise ValueError(f"Feature columns missing from dataset: {missing_feature_columns}")

    target_columns = [
        f"forward_return_{horizon}d_next_close",
        f"spy_forward_return_{horizon}d_next_close",
        f"excess_return_{horizon}d",
        f"sector_forward_return_{horizon}d_next_close",
        f"sector_neutral_forward_return_{horizon}d",
        LONG_RANK_RETURN_COLUMN,
        f"max_drawdown_{horizon}d_next_close",
        f"sector_max_drawdown_{horizon}d_next_close",
    ]
    dataset = dataset.replace([np.inf, -np.inf], np.nan)
    dataset = dataset.dropna(subset=[*target_columns, *feature_columns])
    dataset[LONG_LEGACY_TARGET_COLUMN] = (dataset[f"excess_return_{horizon}d"] > 0).astype(int)
    dataset[LONG_SECTOR_POSITIVE_TARGET_COLUMN] = (dataset[LONG_RANK_RETURN_COLUMN] > 0).astype(int)
    dataset[LONG_SECTOR_HURDLE_TARGET_COLUMN] = (dataset[LONG_RANK_RETURN_COLUMN] > args.sector_hurdle).astype(int)
    dataset[f"sector_neutral_forward_return_{horizon}d_pct_rank"] = dataset.groupby("date")[
        LONG_RANK_RETURN_COLUMN
    ].rank(pct=True)
    dataset[LONG_RANK_TARGET_COLUMN] = np.select(
        [
            dataset[f"sector_neutral_forward_return_{horizon}d_pct_rank"] >= 0.90,
            dataset[f"sector_neutral_forward_return_{horizon}d_pct_rank"] >= 0.80,
            dataset[f"sector_neutral_forward_return_{horizon}d_pct_rank"] <= 0.10,
            dataset[f"sector_neutral_forward_return_{horizon}d_pct_rank"] <= 0.20,
        ],
        [4, 3, 0, 1],
        default=2,
    ).astype(int)

    output_columns = [
        "date",
        "symbol",
        "sector",
        "close",
        *target_columns,
        LONG_LEGACY_TARGET_COLUMN,
        LONG_SECTOR_POSITIVE_TARGET_COLUMN,
        LONG_SECTOR_HURDLE_TARGET_COLUMN,
        f"sector_neutral_forward_return_{horizon}d_pct_rank",
        LONG_RANK_TARGET_COLUMN,
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
        "target_horizon_days": horizon,
        "entry_timing": "next trading day's adjusted close",
        "exit_timing": f"{horizon} trading days after entry",
        "target_column": LONG_RANK_TARGET_COLUMN,
        "return_column": LONG_RANK_RETURN_COLUMN,
        "label_outperform_spy_rate": float(dataset[LONG_LEGACY_TARGET_COLUMN].mean()),
        "sector_neutral_positive_rate": float(dataset[LONG_SECTOR_POSITIVE_TARGET_COLUMN].mean()),
        "sector_neutral_hurdle": args.sector_hurdle,
        "sector_neutral_hurdle_rate": float(dataset[LONG_SECTOR_HURDLE_TARGET_COLUMN].mean()),
        "feature_columns": feature_columns,
        "include_macro": bool(args.include_macro),
        "round_trip_cost_bps": args.round_trip_cost_bps,
        "notes": [
            "This dataset is intentionally separate from the 14-day tactical model dataset.",
            "It reuses the shared price/technical feature pipeline but writes only 252-day target columns.",
            "Macro observation-date features remain disabled by default to avoid release-date leakage.",
        ],
    }
    write_json(metadata, FEATURES_DIR / "long_horizon_training_dataset_metadata.json")
    print(f"Wrote {len(dataset)} rows across {dataset['symbol'].nunique()} symbols to {output_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
