from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from common import FEATURES_DIR, REPORTS_DIR, ROOT, write_json


ORIGINAL_RETURN = "sector_neutral_forward_return_252d_after_cost"
ADJUSTED_RETURN = "drawdown_adjusted_sector_neutral_return_252d_after_cost"
MAX_DRAWDOWN = "max_drawdown_252d_next_close"
RELATIVE_DRAWDOWN = "relative_max_drawdown_252d_next_close"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare original and drawdown-adjusted long-horizon rank labels on the same walk-forward prediction rows."
    )
    parser.add_argument("--dataset", default="long_horizon_training_dataset.csv.gz")
    parser.add_argument(
        "--original-predictions",
        default="xgboost_rank_sector252_walk_forward_test_predictions.csv",
        help="Prediction CSV for the original sector-neutral label inside data/modeling/reports.",
    )
    parser.add_argument(
        "--drawdown-predictions",
        default="xgboost_rank_sector252_drawdown_adjusted_walk_forward_test_predictions.csv",
        help="Prediction CSV for the drawdown-adjusted label inside data/modeling/reports.",
    )
    parser.add_argument("--output-name", default="long_horizon_label_comparison")
    parser.add_argument(
        "--artifact-output",
        default="models/long-horizon/long_horizon_label_comparison.json",
        help="Small committed JSON artifact. Use an empty string to skip.",
    )
    return parser.parse_args()


def load_prediction_frame(path: str, dataset: pd.DataFrame) -> pd.DataFrame:
    predictions = pd.read_csv(REPORTS_DIR / path)
    predictions["date"] = pd.to_datetime(predictions["date"])
    keep = ["date", "symbol", ORIGINAL_RETURN, ADJUSTED_RETURN, MAX_DRAWDOWN, RELATIVE_DRAWDOWN]
    missing = [column for column in keep if column not in predictions.columns]
    if missing:
        predictions = predictions.drop(columns=[column for column in keep if column in predictions.columns and column not in {"date", "symbol"}])
        predictions = predictions.merge(dataset[keep], on=["date", "symbol"], how="left", validate="one_to_one")
    return predictions


def cohort_dates(frame: pd.DataFrame, period: str) -> list[pd.Timestamp]:
    if period == "daily":
        return sorted(frame["date"].drop_duplicates())
    periods = frame[["date"]].drop_duplicates().copy()
    periods["period"] = periods["date"].dt.to_period(period)
    return periods.groupby("period")["date"].min().sort_values().tolist()


def summarize_strategy(frame: pd.DataFrame, label: str, period: str, period_label: str) -> dict:
    rows = []
    for cohort_date in cohort_dates(frame, period):
        group = frame[frame["date"] == cohort_date].dropna(subset=["predicted_rank_score", ORIGINAL_RETURN])
        if group.empty:
            continue
        ranked = group.sort_values("predicted_rank_score", ascending=False)
        count = max(1, int(np.ceil(len(ranked) * 0.1)))
        top = ranked.head(count)
        bottom = ranked.tail(count)
        rows.append(
            {
                "top_original_return": top[ORIGINAL_RETURN].mean(),
                "top_original_return_median": top[ORIGINAL_RETURN].median(),
                "bottom_original_return": bottom[ORIGINAL_RETURN].mean(),
                "spread_original_return": top[ORIGINAL_RETURN].mean() - bottom[ORIGINAL_RETURN].mean(),
                "top_adjusted_return": top[ADJUSTED_RETURN].mean(),
                "top_adjusted_return_median": top[ADJUSTED_RETURN].median(),
                "top_max_drawdown": top[MAX_DRAWDOWN].mean(),
                "top_max_drawdown_median": top[MAX_DRAWDOWN].median(),
                "top_relative_drawdown": top[RELATIVE_DRAWDOWN].mean(),
                "top_relative_drawdown_median": top[RELATIVE_DRAWDOWN].median(),
            }
        )
    detail = pd.DataFrame(rows)
    if detail.empty:
        return {"label": label, "cohort": period_label, "cohort_count": 0}
    return {
        "label": label,
        "cohort": period_label,
        "cohort_count": int(len(detail)),
        "top_original_return_mean": float(detail["top_original_return"].mean()),
        "top_original_return_median": float(detail["top_original_return"].median()),
        "spread_original_return_mean": float(detail["spread_original_return"].mean()),
        "top_adjusted_return_mean": float(detail["top_adjusted_return"].mean()),
        "top_adjusted_return_median": float(detail["top_adjusted_return"].median()),
        "top_max_drawdown_mean": float(detail["top_max_drawdown"].mean()),
        "top_max_drawdown_median": float(detail["top_max_drawdown"].median()),
        "top_relative_drawdown_mean": float(detail["top_relative_drawdown"].mean()),
        "top_relative_drawdown_median": float(detail["top_relative_drawdown"].median()),
    }


def main() -> None:
    args = parse_args()
    dataset = pd.read_csv(
        FEATURES_DIR / args.dataset,
        compression="gzip",
        usecols=["date", "symbol", ORIGINAL_RETURN, ADJUSTED_RETURN, MAX_DRAWDOWN, RELATIVE_DRAWDOWN],
    )
    dataset["date"] = pd.to_datetime(dataset["date"])
    frames = [
        ("Sector-neutral label", load_prediction_frame(args.original_predictions, dataset)),
        ("Drawdown-adjusted label", load_prediction_frame(args.drawdown_predictions, dataset)),
    ]

    rows = []
    for label, frame in frames:
        for period, period_label in [
            ("daily", "Daily overlapping"),
            ("M", "Monthly first trading day"),
            ("Q", "Quarterly first trading day"),
        ]:
            rows.append(summarize_strategy(frame, label, period, period_label))

    summary = pd.DataFrame(rows)
    summary_path = REPORTS_DIR / f"{args.output_name}_summary.csv"
    summary.to_csv(summary_path, index=False)
    report = {
        "generatedAt": pd.Timestamp.now("UTC").isoformat(),
        "dataset": str((FEATURES_DIR / args.dataset).relative_to(ROOT)),
        "summaryPath": str(summary_path.relative_to(ROOT)),
        "rows": summary.to_dict(orient="records"),
        "notes": [
            "Original return columns show the investable sector-neutral 252D return after cost.",
            "Adjusted return columns include the experimental drawdown/path-quality term and should not be treated as realized portfolio return.",
            "Median top-decile returns are emphasized because one-year outcomes can be skewed by a few large winners.",
        ],
    }
    write_json(report, REPORTS_DIR / f"{args.output_name}.json")
    if args.artifact_output:
        write_json(report, ROOT / args.artifact_output)
    print(f"Wrote long-horizon label comparison to {summary_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
