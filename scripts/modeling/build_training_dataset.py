from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from common import FEATURES_DIR, MACRO_DIR, PRICES_DIR, REFERENCE_DIR, ROOT, SECTOR_ETFS, write_csv, write_json


TARGET_HORIZON = 14


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a labeled training dataset for the Market Pulse XGBoost model.")
    parser.add_argument("--output-name", default="training_dataset.csv.gz", help="Name of the generated feature dataset.")
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
    frame["volatility_20d"] = frame["ret_1d"].rolling(20).std()
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
    frame["volume_ratio_20"] = volume / frame["avg_volume_20"].replace(0, np.nan)
    frame["high_252"] = close.rolling(252).max()
    frame["distance_to_52w_high"] = close / frame["high_252"] - 1
    frame["forward_return_14d"] = close.shift(-TARGET_HORIZON) / close - 1
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
        stock_frames.append(frame[["date", "symbol", "close", "ret_20d", "ret_60d", "price_vs_sma_50", "price_vs_sma_200"]].copy())

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
        )
        .reset_index()
    )

    sector_frames = []
    for etf in SECTOR_ETFS:
        frame = symbol_frames[etf][["date", "ret_20d", "ret_60d", "price_vs_sma_50", "price_vs_sma_200", "rsi_14"]].copy()
        frame = frame.rename(
            columns={
                "ret_20d": "sector_ret_20d",
                "ret_60d": "sector_ret_60d",
                "price_vs_sma_50": "sector_price_vs_sma_50",
                "price_vs_sma_200": "sector_price_vs_sma_200",
                "rsi_14": "sector_rsi_14",
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
    constituents = pd.read_csv(REFERENCE_DIR / "sp500_constituents.csv")
    constituents["symbol"] = constituents["symbol"].astype(str)

    symbol_frames: dict[str, pd.DataFrame] = {}
    for symbol in [*constituents["symbol"].tolist(), "SPY", *SECTOR_ETFS]:
        path = PRICES_DIR / f"{symbol}.csv"
        if not path.exists():
            continue
        symbol_frames[symbol] = enrich_price_features(load_price(symbol))

    spy = symbol_frames["SPY"][["date", "forward_return_14d", "ret_20d", "ret_60d", "price_vs_sma_50", "price_vs_sma_200", "rsi_14"]].copy()
    spy = spy.rename(
        columns={
            "forward_return_14d": "spy_forward_return_14d",
            "ret_20d": "spy_ret_20d",
            "ret_60d": "spy_ret_60d",
            "price_vs_sma_50": "spy_price_vs_sma_50",
            "price_vs_sma_200": "spy_price_vs_sma_200",
            "rsi_14": "spy_rsi_14",
        }
    )

    macro = load_macro_features()
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
        frame["excess_return_14d"] = frame["forward_return_14d"] - frame["spy_forward_return_14d"]
        frame["label_outperform_spy_14d"] = (frame["excess_return_14d"] > 0).astype(int)
        frame["rel_ret_20d_vs_spy"] = frame["ret_20d"] - frame["spy_ret_20d"]
        frame["rel_ret_60d_vs_spy"] = frame["ret_60d"] - frame["spy_ret_60d"]
        frame["rel_rsi_vs_spy"] = frame["rsi_14"] - frame["spy_rsi_14"]
        dataset_frames.append(frame)

    dataset = pd.concat(dataset_frames, ignore_index=True)
    dataset = dataset.sort_values(["date", "symbol"]).reset_index(drop=True)

    rank_base_columns = [
        "ret_20d",
        "ret_60d",
        "price_vs_sma_50",
        "price_vs_sma_200",
        "distance_to_52w_high",
        "volume_ratio_20",
        "rel_ret_20d_vs_spy",
        "rel_ret_60d_vs_spy",
        "rel_rsi_vs_spy",
    ]
    for column in rank_base_columns:
        dataset[f"{column}_pct_rank"] = dataset.groupby("date")[column].rank(pct=True)

    feature_columns = [
        "ret_1d",
        "ret_5d",
        "ret_10d",
        "ret_20d",
        "ret_60d",
        "volatility_20d",
        "price_vs_sma_20",
        "price_vs_sma_50",
        "price_vs_sma_100",
        "price_vs_sma_200",
        "sma_20_vs_50",
        "sma_50_vs_200",
        "rsi_14",
        "volume_ratio_20",
        "distance_to_52w_high",
        "spy_ret_20d",
        "spy_ret_60d",
        "spy_price_vs_sma_50",
        "spy_price_vs_sma_200",
        "spy_rsi_14",
        "rel_ret_20d_vs_spy",
        "rel_ret_60d_vs_spy",
        "rel_rsi_vs_spy",
        "breadth_above_50",
        "breadth_above_200",
        "breadth_ret_20d_median",
        "breadth_ret_60d_median",
        "sector_ret_20d",
        "sector_ret_60d",
        "sector_price_vs_sma_50",
        "sector_price_vs_sma_200",
        "sector_rsi_14",
        "ret_20d_pct_rank",
        "ret_60d_pct_rank",
        "price_vs_sma_50_pct_rank",
        "price_vs_sma_200_pct_rank",
        "distance_to_52w_high_pct_rank",
        "volume_ratio_20_pct_rank",
        "rel_ret_20d_vs_spy_pct_rank",
        "rel_ret_60d_vs_spy_pct_rank",
        "rel_rsi_vs_spy_pct_rank",
    ]
    macro_feature_columns = [
        column
        for column in dataset.columns
        if column.endswith("_level") or column.endswith("_chg_1") or column.endswith("_chg_5")
    ]
    feature_columns.extend(column for column in macro_feature_columns if column not in feature_columns)

    dataset = dataset.dropna(subset=["label_outperform_spy_14d", *feature_columns])
    output_columns = [
        "date",
        "symbol",
        "sector",
        "close",
        "forward_return_14d",
        "spy_forward_return_14d",
        "excess_return_14d",
        "label_outperform_spy_14d",
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
        "label_positive_rate": float(dataset["label_outperform_spy_14d"].mean()),
        "feature_columns": feature_columns,
        "target_horizon_days": TARGET_HORIZON,
    }
    write_json(metadata, FEATURES_DIR / "training_dataset_metadata.json")
    print(f"Wrote {len(dataset)} rows across {dataset['symbol'].nunique()} symbols to {output_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
