from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from common import FEATURES_DIR, PRICES_DIR, REFERENCE_DIR, REPORTS_DIR, ROOT, write_json
from schema import NON_FEATURE_COLUMNS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit cached modeling data for label, null, outlier, and alignment issues.")
    parser.add_argument("--dataset", default="training_dataset.csv.gz", help="Dataset filename inside data/modeling/features.")
    parser.add_argument("--output-name", default="data_quality_report", help="Base report filename inside data/modeling/reports.")
    return parser.parse_args()


def raw_price_report() -> dict:
    constituents = pd.read_csv(REFERENCE_DIR / "sp500_constituents.csv")
    symbols = constituents["symbol"].dropna().astype(str).tolist()
    spy = pd.read_csv(PRICES_DIR / "SPY.csv", usecols=["date"])
    spy["date"] = pd.to_datetime(spy["date"])
    spy_dates = pd.Index(spy["date"])

    missing_files = []
    quality_issues = []
    row_counts = []
    for symbol in symbols:
        path = PRICES_DIR / f"{symbol}.csv"
        if not path.exists():
            missing_files.append(symbol)
            continue
        frame = pd.read_csv(path)
        row_counts.append(len(frame))
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        close = pd.to_numeric(frame.get("close"), errors="coerce")
        volume = pd.to_numeric(frame.get("volume"), errors="coerce")
        returns = close.pct_change()
        in_window = spy_dates[(spy_dates >= frame["date"].min()) & (spy_dates <= frame["date"].max())]
        missing_spy_dates = len(in_window.difference(pd.Index(frame["date"])))
        issue = {
            "symbol": symbol,
            "rows": int(len(frame)),
            "start": str(frame["date"].min().date()),
            "end": str(frame["date"].max().date()),
            "duplicate_dates": int(frame["date"].duplicated().sum()),
            "null_close": int(close.isna().sum()),
            "nonpositive_close": int((close <= 0).sum()),
            "null_volume": int(volume.isna().sum()),
            "extreme_abs_1d_return_gt_50pct": int((returns.abs() > 0.5).sum()),
            "spy_dates_missing_in_symbol_window": int(missing_spy_dates),
        }
        if any(
            issue[key]
            for key in [
                "duplicate_dates",
                "null_close",
                "nonpositive_close",
                "null_volume",
                "extreme_abs_1d_return_gt_50pct",
                "spy_dates_missing_in_symbol_window",
            ]
        ):
            quality_issues.append(issue)

    row_counts_series = pd.Series(row_counts, dtype=float)
    return {
        "constituent_count": int(len(symbols)),
        "missing_price_files": missing_files,
        "price_file_quality_issues": quality_issues,
        "row_count_quantiles": {str(key): float(value) for key, value in row_counts_series.quantile([0, 0.01, 0.05, 0.5, 0.95, 1]).items()},
    }


def dataset_report(dataset_path: Path) -> dict:
    dataset = pd.read_csv(dataset_path, compression="gzip")
    constituents = pd.read_csv(REFERENCE_DIR / "sp500_constituents.csv")
    constituent_symbols = set(constituents["symbol"].dropna().astype(str))
    dataset_symbols = set(dataset["symbol"].dropna().astype(str))
    feature_columns = [column for column in dataset.columns if column not in NON_FEATURE_COLUMNS]
    numeric_features = dataset[feature_columns].to_numpy(dtype=float)
    non_null_excess = dataset["excess_return_14d"].notna()
    label_mismatches = int(
        (
            (dataset.loc[non_null_excess, "excess_return_14d"] > 0).astype(int).to_numpy()
            != dataset.loc[non_null_excess, "label_outperform_spy_14d"].to_numpy()
        ).sum()
    )

    date_counts = dataset.groupby("date")["symbol"].nunique()
    split_rows = []
    dates = pd.to_datetime(dataset["date"])
    for name, start, end in [
        ("train_reference", None, "2024-04-22"),
        ("validation_reference", "2024-04-22", "2025-04-28"),
        ("test_reference", "2025-04-28", None),
    ]:
        mask = pd.Series(True, index=dataset.index)
        if start:
            mask &= dates >= pd.Timestamp(start)
        if end:
            mask &= dates < pd.Timestamp(end)
        part = dataset[mask]
        if part.empty:
            continue
        split_rows.append(
            {
                "name": name,
                "rows": int(len(part)),
                "start": str(part["date"].min()),
                "end": str(part["date"].max()),
                "label_positive_rate": float(part["label_outperform_spy_14d"].mean()),
                "null_excess_return_rows": int(part["excess_return_14d"].isna().sum()),
            }
        )

    return {
        "rows": int(len(dataset)),
        "symbols": int(dataset["symbol"].nunique()),
        "constituent_symbols": int(len(constituent_symbols)),
        "constituents_missing_from_dataset": sorted(constituent_symbols - dataset_symbols),
        "dates": int(dataset["date"].nunique()),
        "start_date": str(dataset["date"].min()),
        "end_date": str(dataset["date"].max()),
        "feature_count": int(len(feature_columns)),
        "duplicate_date_symbol_rows": int(dataset.duplicated(["date", "symbol"]).sum()),
        "null_forward_return_rows": int(dataset["forward_return_14d"].isna().sum()),
        "null_spy_forward_return_rows": int(dataset["spy_forward_return_14d"].isna().sum()),
        "null_excess_return_rows": int(dataset["excess_return_14d"].isna().sum()),
        "null_next_close_forward_return_rows": int(dataset["forward_return_14d_next_close"].isna().sum()),
        "null_sector_forward_return_rows": int(dataset["sector_forward_return_14d_next_close"].isna().sum()),
        "null_sector_neutral_return_rows": int(dataset["sector_neutral_forward_return_14d_after_cost"].isna().sum()),
        "label_mismatches_on_non_null_excess": label_mismatches,
        "feature_null_cells": int(dataset[feature_columns].isna().sum().sum()),
        "feature_infinite_cells": int(np.isinf(numeric_features).sum()),
        "min_symbols_per_date": int(date_counts.min()),
        "median_symbols_per_date": float(date_counts.median()),
        "max_symbols_per_date": int(date_counts.max()),
        "label_positive_rate": float(dataset["label_outperform_spy_14d"].mean()),
        "sector_neutral_positive_rate": float(dataset["label_sector_neutral_positive_14d"].mean()),
        "sector_neutral_hurdle_rate": float(dataset["label_sector_neutral_hurdle_14d"].mean()),
        "candidate_rows": int(dataset["candidate_momentum_setup"].sum()),
        "candidate_success_rate": float(dataset.loc[dataset["candidate_momentum_setup"].eq(1), "meta_label_momentum_success"].mean()),
        "abs_excess_return_gt_50pct_rows": int((dataset["excess_return_14d"].abs() > 0.5).sum()),
        "abs_sector_neutral_return_gt_50pct_rows": int((dataset["sector_neutral_forward_return_14d_after_cost"].abs() > 0.5).sum()),
        "excess_return_quantiles": {
            str(key): float(value)
            for key, value in dataset["excess_return_14d"].quantile([0, 0.001, 0.01, 0.5, 0.99, 0.999, 1]).items()
        },
        "split_reference": split_rows,
    }


def main() -> None:
    args = parse_args()
    dataset_path = FEATURES_DIR / args.dataset
    report = {
        "generated_at": pd.Timestamp.now("UTC").isoformat(),
        "dataset_path": str(dataset_path.relative_to(ROOT)),
        "raw_prices": raw_price_report(),
        "dataset": dataset_report(dataset_path),
    }
    output_path = REPORTS_DIR / f"{args.output_name}.json"
    write_json(report, output_path)
    print(f"Wrote data quality report to {output_path.relative_to(ROOT)}")
    print(
        "Dataset rows: "
        f"{report['dataset']['rows']} | null excess rows: {report['dataset']['null_excess_return_rows']} | "
        f"label mismatches: {report['dataset']['label_mismatches_on_non_null_excess']} | "
        f"feature null cells: {report['dataset']['feature_null_cells']}"
    )


if __name__ == "__main__":
    main()
