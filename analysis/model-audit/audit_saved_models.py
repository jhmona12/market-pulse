"""Read-only audit of cached datasets and historical early-stopping predictions.

No network calls, retraining, model replacement, or dashboard publication.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import xgboost as xgb

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/modeling"))
from common import PRICES_DIR, write_json  # noqa: E402
from schema import NON_FEATURE_COLUMNS  # noqa: E402

SPECS = [
    ("tactical", "models/rank/xgboost_rank_sector14_tuned_metadata.json", "xgboost_rank_sector14_feature_v2_tuned", 14),
    ("strategic", "models/long-horizon/xgboost_rank_sector252_15y_monthly_tuned_research_metadata.json", "xgboost_rank_sector252_15y_monthly_tuned_walk_forward", 252),
]


def audit_raw_cache() -> dict:
    calendar = pd.DatetimeIndex(pd.read_csv(PRICES_DIR / "SPY.csv", usecols=["date"])["date"])
    issues, endings = [], {}
    rows = all_gains = 0
    for path in sorted(PRICES_DIR.glob("*.csv")):
        frame = pd.read_csv(path, usecols=["date", "close"], parse_dates=["date"]).sort_values("date")
        dates = pd.DatetimeIndex(frame["date"])
        expected = calendar[(calendar >= dates.min()) & (calendar <= dates.max())]
        missing = expected.difference(dates)
        duplicates = int(dates.duplicated().sum())
        invalid = int((~np.isfinite(frame["close"]) | frame["close"].le(0)).sum())
        end = str(dates.max().date())
        endings[end] = endings.get(end, 0) + 1
        delta = frame["close"].diff()
        all_gains += int((delta.clip(upper=0).rolling(14).sum().eq(0) & delta.clip(lower=0).rolling(14).sum().gt(0)).sum())
        rows += len(frame)
        if len(missing) or duplicates or invalid:
            issues.append({"symbol": path.stem, "missing_internal_spy_sessions": len(missing), "duplicate_dates": duplicates,
                "invalid_closes": invalid, "missing_date_sample": list(missing.strftime("%Y-%m-%d")[:5])})
    return {"rows": rows, "cache_end_date_counts": endings, "all_gains_rsi_edge_case_rows": all_gains, "issues": issues,
        "scope": "Current local research raw cache, not live scoring cache or immutable source data from the original model builds. Calendar checks use SPY, not an independent vendor."}


def audit(spec: tuple) -> dict:
    name, metadata_path, research_name, horizon = spec
    metadata = json.loads((ROOT / metadata_path).read_text())
    report = json.loads((ROOT / f"data/modeling/reports/{research_name}_report.json").read_text())
    feature_columns = metadata["feature_columns"]
    prediction_path = ROOT / f"data/modeling/reports/{research_name}_test_predictions.csv"
    dates = pd.read_csv(prediction_path, usecols=["date", "fold"]).drop_duplicates()
    dates["date"] = pd.to_datetime(dates["date"])
    dates["month"] = dates["date"].dt.to_period("M")
    dates = dates.sort_values("date").groupby(["fold", "month"]).first().reset_index()
    sampled = set(dates["date"].dt.strftime("%Y-%m-%d"))
    return_column, target_column = metadata["return_column"], metadata["target_column"]
    forward = f"forward_return_{horizon}d_next_close"
    sector = f"sector_forward_return_{horizon}d_next_close"
    needed = ["date", "symbol", *feature_columns, return_column, target_column, forward, sector]
    selected = []
    row_count = bad_features = bad_labels = duplicate_rows = label_math_errors = 0
    all_dates, symbols = set(), set()
    last_date, last_symbols = None, set()
    for chunk in pd.read_csv(ROOT / metadata["dataset"], usecols=needed, chunksize=20000):
        if not chunk["date"].is_monotonic_increasing or (last_date and chunk["date"].iloc[0] < last_date):
            raise ValueError("Expected date-sorted training cache; cannot perform boundary duplicate audit.")
        duplicate_rows += int(chunk.duplicated(["date", "symbol"]).sum())
        if last_date:
            duplicate_rows += int((chunk["date"].eq(last_date) & chunk["symbol"].isin(last_symbols)).sum())
        end_date = chunk["date"].iloc[-1]
        end_symbols = set(chunk.loc[chunk["date"].eq(end_date), "symbol"])
        last_symbols = last_symbols | end_symbols if last_date == end_date else end_symbols
        last_date = end_date
        bad_features += int((~np.isfinite(chunk[feature_columns].to_numpy(dtype=float))).sum())
        bad_labels += int((~np.isfinite(chunk[[return_column, target_column]].to_numpy(dtype=float))).sum())
        # Both exported datasets declare a 15 bp round-trip cost.
        expected = chunk[forward] - chunk[sector] - 0.0015
        label_math_errors += int((~np.isclose(expected, chunk[return_column], atol=1e-10)).sum())
        selected.append(chunk[chunk["date"].isin(sampled)].copy())
        row_count += len(chunk)
        all_dates.update(chunk["date"].unique())
        symbols.update(chunk["symbol"].unique())
    frame = pd.concat(selected, ignore_index=True)
    cohorts, fold_details = [], []
    for fold in report["folds"]:
        fold_id = fold["fold"]
        booster = xgb.Booster()
        booster.load_model(ROOT / f"data/modeling/models/{research_name}_fold{fold_id}.json")
        fold_details.append({"fold": fold_id, "saved_rounds": booster.num_boosted_rounds(), "best_rounds": booster.best_iteration + 1})
        fold_dates = set(dates.loc[dates["fold"].eq(fold_id), "date"].dt.strftime("%Y-%m-%d"))
        sample = frame[frame["date"].isin(fold_dates)].copy()
        matrix = xgb.DMatrix(sample[feature_columns].to_numpy(dtype=float), feature_names=feature_columns)
        sample["old"] = booster.predict(matrix)
        sample["best"] = booster.predict(matrix, iteration_range=(0, booster.best_iteration + 1))
        for date, group in sample.groupby("date"):
            size = int(np.ceil(len(group) / 10))
            old_top = group.nlargest(size, "old")
            best_top = group.nlargest(size, "best")
            cohorts.append({"date": date, "fold": fold_id, "names": len(group),
                "old_top_mean": float(old_top[return_column].mean()), "best_top_mean": float(best_top[return_column].mean()),
                "old_top_member_median": float(old_top[return_column].median()), "best_top_member_median": float(best_top[return_column].median()),
                "top_decile_overlap": len(set(old_top["symbol"]) & set(best_top["symbol"])) / size,
                "rank_correlation": float(group["old"].rank().corr(group["best"].rank()))})
    summary = pd.DataFrame(cohorts)
    model_path = ROOT / metadata["model_path"]
    live = xgb.Booster()
    live.load_model(model_path)
    result = {"model": name, "metadata": metadata_path, "artifact_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
        "deployed_rounds": live.num_boosted_rounds(), "deployed_has_best_iteration": live.attr("best_iteration") is not None,
        "dataset_rows_scanned": row_count, "dataset_symbols": len(symbols), "dataset_date_count": len(all_dates),
        "dataset_start": min(all_dates), "dataset_end": max(all_dates), "nonfinite_feature_cells": bad_features,
        "nonfinite_target_cells": bad_labels, "duplicate_symbol_dates": duplicate_rows, "sector_return_arithmetic_errors": label_math_errors,
        "forbidden_feature_columns": sorted(set(feature_columns) & NON_FEATURE_COLUMNS),
        "saved_fold_rounds": fold_details, "monthly_probe_count": len(cohorts),
        "mean_top_decile_overlap": float(summary["top_decile_overlap"].mean()),
        "mean_rank_correlation": float(summary["rank_correlation"].mean()),
        "old_mean_cohort_return": float(summary["old_top_mean"].mean()),
        "best_mean_cohort_return": float(summary["best_top_mean"].mean()),
        "old_median_cohort_mean_return": float(summary["old_top_mean"].median()),
        "best_median_cohort_mean_return": float(summary["best_top_mean"].median()),
        "cohorts": cohorts,
        "scope": "Replay of saved research folds on their first available monthly test dates. Isolates early-stopping tree selection; does not repair historical splits, survivorship, source prices, or tuning selection bias."}
    print(json.dumps({k: v for k, v in result.items() if k != "cohorts"}, indent=2), flush=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=f"data/diagnostics/model-audit-{pd.Timestamp.now('UTC').date()}.json")
    args = parser.parse_args()
    payload = {"generated_at": pd.Timestamp.now("UTC").isoformat(), "raw_cache": audit_raw_cache(), "models": []}
    for spec in SPECS:
        print(f"Scanning {spec[0]} cached dataset...", flush=True)
        payload["models"].append(audit(spec))
        write_json(payload, ROOT / args.output)


if __name__ == "__main__":
    main()
