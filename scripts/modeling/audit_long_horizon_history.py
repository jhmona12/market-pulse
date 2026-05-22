from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pandas as pd

from common import FEATURES_DIR, PRICES_DIR, REFERENCE_DIR, REPORTS_DIR, ROOT, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit how far back the long-horizon model can train without leaning on sparse short-history rows."
    )
    parser.add_argument(
        "--dataset",
        default="long_horizon_training_dataset.csv.gz",
        help="Long-horizon dataset filename inside data/modeling/features.",
    )
    parser.add_argument(
        "--coverage-levels",
        default="0.90,0.95,0.98,1.00",
        help="Comma-separated universe coverage thresholds to test.",
    )
    parser.add_argument(
        "--output-name",
        default="long_horizon_history_audit",
        help="Base name for report outputs inside data/modeling/reports.",
    )
    return parser.parse_args()


def safe_date(value) -> str | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date().isoformat()


def price_coverage() -> pd.DataFrame:
    rows = []
    for path in sorted(PRICES_DIR.glob("*.csv")):
        try:
            frame = pd.read_csv(path, usecols=["date", "close"])
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
            rows.append(
                {
                    "symbol": path.stem,
                    "price_rows": int(len(frame)),
                    "price_start": frame["date"].min(),
                    "price_end": frame["date"].max(),
                    "null_close_rows": int(frame["close"].isna().sum()),
                }
            )
        except Exception as error:  # noqa: BLE001
            rows.append(
                {
                    "symbol": path.stem,
                    "price_rows": 0,
                    "price_start": pd.NaT,
                    "price_end": pd.NaT,
                    "null_close_rows": None,
                    "price_error": str(error),
                }
            )
    return pd.DataFrame(rows)


def feature_coverage(dataset_path: Path) -> pd.DataFrame:
    dataset = pd.read_csv(dataset_path, compression="gzip", usecols=["date", "symbol"])
    dataset["date"] = pd.to_datetime(dataset["date"])
    return (
        dataset.groupby("symbol")
        .agg(feature_rows=("date", "size"), feature_start=("date", "min"), feature_end=("date", "max"))
        .reset_index()
    )


def recommended_start_dates(feature: pd.DataFrame, levels: list[float]) -> list[dict]:
    starts = feature["feature_start"].dropna().sort_values().reset_index(drop=True)
    total = int(len(starts))
    rows = []
    for level in levels:
        if total == 0:
            rows.append({"coverage_level": level, "symbol_count": 0, "recommended_start_date": None})
            continue
        minimum_symbols = math.ceil(total * level)
        index = min(max(total - minimum_symbols, 0), total - 1)
        rows.append(
            {
                "coverage_level": float(level),
                "symbol_count": minimum_symbols,
                "recommended_start_date": safe_date(starts.iloc[index]),
            }
        )
    return rows


def main() -> None:
    args = parse_args()
    dataset_path = FEATURES_DIR / args.dataset
    levels = [float(value.strip()) for value in args.coverage_levels.split(",") if value.strip()]

    constituents = pd.read_csv(REFERENCE_DIR / "sp500_constituents.csv")
    constituents["symbol"] = constituents["symbol"].astype(str)
    coverage = constituents[["symbol", "name", "sector", "sub_industry"]].merge(price_coverage(), on="symbol", how="left")
    coverage = coverage.merge(feature_coverage(dataset_path), on="symbol", how="left")
    coverage["feature_coverage_share"] = coverage["feature_rows"] / coverage["feature_rows"].max()
    coverage = coverage.sort_values(["feature_start", "symbol"], ascending=[False, True])

    audit_rows = recommended_start_dates(coverage.dropna(subset=["feature_start"]), levels)
    short_history = coverage.sort_values(["feature_rows", "symbol"]).head(30).copy()
    for column in ["price_start", "price_end", "feature_start", "feature_end"]:
        coverage[column] = coverage[column].map(safe_date)
        short_history[column] = short_history[column].map(safe_date)

    output_csv = REPORTS_DIR / f"{args.output_name}_symbol_coverage.csv"
    coverage.to_csv(output_csv, index=False)
    output_short_csv = REPORTS_DIR / f"{args.output_name}_short_history.csv"
    short_history.to_csv(output_short_csv, index=False)

    manifest_path = ROOT / "data" / "modeling" / "raw_manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    feature = coverage.dropna(subset=["feature_rows"])
    report = {
        "generated_at": pd.Timestamp.now("UTC").isoformat(),
        "dataset": str(dataset_path.relative_to(ROOT)),
        "raw_manifest_years_requested": manifest.get("years_requested"),
        "raw_manifest_start_date": manifest.get("start_date"),
        "raw_manifest_end_date": manifest.get("end_date"),
        "symbol_count": int(len(coverage)),
        "feature_complete_symbol_count": int(feature["symbol"].nunique()),
        "feature_start_min": safe_date(pd.to_datetime(feature["feature_start"]).min()) if not feature.empty else None,
        "feature_start_max": safe_date(pd.to_datetime(feature["feature_start"]).max()) if not feature.empty else None,
        "feature_end_min": safe_date(pd.to_datetime(feature["feature_end"]).min()) if not feature.empty else None,
        "feature_end_max": safe_date(pd.to_datetime(feature["feature_end"]).max()) if not feature.empty else None,
        "recommended_start_dates": audit_rows,
        "short_history_symbols": short_history[
            ["symbol", "name", "sector", "feature_rows", "feature_start", "feature_end", "price_start", "price_end"]
        ].to_dict(orient="records"),
        "outputs": {
            "symbol_coverage": str(output_csv.relative_to(ROOT)),
            "short_history": str(output_short_csv.relative_to(ROOT)),
        },
        "notes": [
            "The recommendation is based on feature-complete rows, not merely raw price availability.",
            "A longer raw price cache can move the earliest clean date earlier, but only after every feature and 252D label can be computed.",
            "Short-history current constituents are visible here so they can be excluded in sensitivity tests without silently disappearing.",
        ],
    }
    write_json(report, REPORTS_DIR / f"{args.output_name}.json")
    print(
        f"Wrote history audit for {report['feature_complete_symbol_count']}/{report['symbol_count']} feature-complete symbols. "
        f"Earliest feature-complete date: {report['feature_start_min']}."
    )


if __name__ == "__main__":
    main()
