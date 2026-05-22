from __future__ import annotations

import argparse

import pandas as pd

from common import FEATURES_DIR, REPORTS_DIR, ROOT, write_json
from train_rank_model import (
    DEFAULT_EARLY_STOPPING_ROUNDS,
    DEFAULT_NUM_BOOST_ROUND,
    DEFAULT_RANK_PARAMS,
    RETURN_COLUMN,
    TARGET_COLUMN,
    evaluate_ranked,
    feature_columns_for,
    filter_by_symbol_history,
)
from walk_forward_rank_model import fold_slices, train_fold
from train_rank_model import sample_dates

try:
    import xgboost as xgb
except ModuleNotFoundError as error:  # pragma: no cover - handled at runtime
    raise SystemExit(
        "xgboost is not installed in the current Python environment. "
        "Run scripts/modeling/setup_training_env.sh before tuning."
    ) from error


TUNING_PRESETS = {
    "shallow_strong_regularized": {
        "params": {
            **DEFAULT_RANK_PARAMS,
            "eta": 0.04,
            "max_depth": 2,
            "min_child_weight": 120,
            "subsample": 0.85,
            "colsample_bytree": 0.8,
            "lambda": 6.0,
            "alpha": 0.5,
        },
        "num_boost_round": 500,
        "early_stopping_rounds": 35,
    },
    "depth2_more_rounds": {
        "params": {
            **DEFAULT_RANK_PARAMS,
            "eta": 0.025,
            "max_depth": 2,
            "min_child_weight": 80,
            "subsample": 0.9,
            "colsample_bytree": 0.85,
            "lambda": 4.0,
            "alpha": 0.0,
        },
        "num_boost_round": 900,
        "early_stopping_rounds": 50,
    },
    "depth3_less_child": {
        "params": {
            **DEFAULT_RANK_PARAMS,
            "eta": 0.04,
            "max_depth": 3,
            "min_child_weight": 40,
            "subsample": 0.85,
            "colsample_bytree": 0.8,
            "lambda": 4.0,
            "alpha": 0.0,
        },
        "num_boost_round": 700,
        "early_stopping_rounds": 40,
    },
    "legacy_baseline": {
        "params": {
            **DEFAULT_RANK_PARAMS,
            "eta": 0.05,
            "max_depth": 4,
            "min_child_weight": 50,
            "subsample": 0.8,
            "lambda": 2.0,
        },
        "num_boost_round": 500,
        "early_stopping_rounds": 30,
    },
    "current_default": {
        "params": DEFAULT_RANK_PARAMS,
        "num_boost_round": DEFAULT_NUM_BOOST_ROUND,
        "early_stopping_rounds": DEFAULT_EARLY_STOPPING_ROUNDS,
    },
    "lower_eta": {
        "params": {
            **DEFAULT_RANK_PARAMS,
            "eta": 0.025,
            "max_depth": 4,
            "min_child_weight": 50,
            "lambda": 2.0,
        },
        "num_boost_round": 900,
        "early_stopping_rounds": 50,
    },
    "deeper_regularized": {
        "params": {
            **DEFAULT_RANK_PARAMS,
            "eta": 0.035,
            "max_depth": 5,
            "min_child_weight": 80,
            "subsample": 0.75,
            "colsample_bytree": 0.75,
            "lambda": 5.0,
        },
        "num_boost_round": 700,
        "early_stopping_rounds": 40,
    },
    "less_regularized": {
        "params": {
            **DEFAULT_RANK_PARAMS,
            "eta": 0.05,
            "max_depth": 4,
            "min_child_weight": 20,
            "lambda": 1.0,
        },
        "num_boost_round": 500,
        "early_stopping_rounds": 30,
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tune XGBoost rank-model hyperparameters with walk-forward evaluation.")
    parser.add_argument("--dataset", default="training_dataset.csv.gz", help="Dataset filename inside data/modeling/features.")
    parser.add_argument("--output-name", default="xgboost_rank_sector14_tuning", help="Base name for tuning reports.")
    parser.add_argument("--target-column", default=TARGET_COLUMN, help="Relevance-grade target column.")
    parser.add_argument("--return-column", default=RETURN_COLUMN, help="Forward return column used for economic evaluation.")
    parser.add_argument(
        "--preset",
        action="append",
        choices=sorted(TUNING_PRESETS),
        help="Preset to evaluate. Repeat for multiple presets. Defaults to all presets.",
    )
    parser.add_argument("--folds", type=int, default=4, help="Number of recent non-overlapping test folds.")
    parser.add_argument("--test-days", type=int, default=63, help="Trading days per test fold.")
    parser.add_argument("--validation-days", type=int, default=126, help="Trading days per validation fold.")
    parser.add_argument("--embargo-days", type=int, default=14, help="Trading-date embargo around validation/test windows.")
    parser.add_argument("--min-train-days", type=int, default=756, help="Minimum expanding-window train size.")
    parser.add_argument(
        "--min-symbol-rows",
        type=int,
        default=0,
        help="Drop symbols with fewer than this many feature-complete rows before tuning.",
    )
    parser.add_argument(
        "--train-sample-frequency",
        choices=["daily", "weekly", "monthly"],
        default="daily",
        help="Sample train and validation dates before fitting. Test dates are never sampled.",
    )
    return parser.parse_args()


def cohort_dates(frame: pd.DataFrame, period: str) -> list[pd.Timestamp]:
    if period == "daily":
        return sorted(frame["date"].drop_duplicates())
    periods = frame[["date"]].drop_duplicates().copy()
    periods["period"] = periods["date"].dt.to_period(period)
    return periods.groupby("period")["date"].min().sort_values().tolist()


def summarize_decile_cohorts(
    frame: pd.DataFrame,
    score_column: str,
    return_column: str,
    target_column: str,
    period: str,
) -> dict:
    rows = []
    for date in cohort_dates(frame, period):
        group = frame[frame["date"] == date].dropna(subset=[score_column, return_column, target_column])
        if group.empty:
            continue
        ranked = group.sort_values(score_column, ascending=False)
        bucket_count = max(1, int(len(ranked) * 0.1 + 0.999999))
        top = ranked.head(bucket_count)
        bottom = ranked.tail(bucket_count)
        rows.append(
            {
                "top_return_mean": top[return_column].mean(),
                "top_return_median": top[return_column].median(),
                "spread_mean": top[return_column].mean() - bottom[return_column].mean(),
                "spread_median": top[return_column].median() - bottom[return_column].median(),
                "avg_grade": top[target_column].mean(),
            }
        )
    if not rows:
        return {
            "cohort_count": 0,
            "top_return_mean": None,
            "top_return_median": None,
            "top_member_return_median": None,
            "spread_mean": None,
            "spread_median": None,
            "member_spread_median": None,
            "avg_grade_mean": None,
        }
    summary = pd.DataFrame(rows)
    return {
        "cohort_count": int(len(summary)),
        "top_return_mean": float(summary["top_return_mean"].mean()),
        "top_return_median": float(summary["top_return_mean"].median()),
        "top_member_return_median": float(summary["top_return_median"].median()),
        "spread_mean": float(summary["spread_mean"].mean()),
        "spread_median": float(summary["spread_mean"].median()),
        "member_spread_median": float(summary["spread_median"].median()),
        "avg_grade_mean": float(summary["avg_grade"].mean()),
    }


def evaluate_preset(
    dataset: pd.DataFrame,
    folds: list[dict],
    feature_columns: list[str],
    preset: dict,
    target_column: str,
    return_column: str,
    train_sample_frequency: str,
) -> dict:
    predictions = []
    fold_reports = []
    for fold in folds:
        train_dates = sample_dates(fold["train_dates"], train_sample_frequency)
        validation_dates = sample_dates(fold["validation_dates"], train_sample_frequency)
        train = dataset[dataset["date"].isin(train_dates)].copy()
        validation = dataset[dataset["date"].isin(validation_dates)].copy()
        test = dataset[dataset["date"].isin(fold["test_dates"])].copy()
        booster = train_fold(
            train,
            validation,
            feature_columns,
            target_column=target_column,
            params=preset["params"],
            num_boost_round=preset["num_boost_round"],
            early_stopping_rounds=preset["early_stopping_rounds"],
        )
        ordered_test = test.sort_values(["date", "symbol"]).reset_index(drop=True)
        dtest = xgb.DMatrix(ordered_test[feature_columns].to_numpy(dtype=float), feature_names=feature_columns)
        ordered_test["predicted_rank_score"] = booster.predict(dtest)
        ordered_test["fold"] = fold["fold"]
        predictions.append(ordered_test[["fold", "date", "symbol", return_column, target_column, "predicted_rank_score"]])
        fold_reports.append(
            {
                "fold": fold["fold"],
                "train_rows": int(len(train)),
                "validation_rows": int(len(validation)),
                "test_rows": int(len(test)),
                "train_date_count": int(len(train_dates)),
                "validation_date_count": int(len(validation_dates)),
                "best_iteration": int(booster.best_iteration),
                "split": {key: value for key, value in fold.items() if not key.endswith("_dates")},
                "test_metrics": evaluate_ranked(ordered_test, "predicted_rank_score", return_column, target_column),
            }
        )
    combined = pd.concat(predictions, ignore_index=True)
    return {
        "combined_test_metrics": evaluate_ranked(combined, "predicted_rank_score", return_column, target_column),
        "monthly_cohort_metrics": summarize_decile_cohorts(
            combined,
            "predicted_rank_score",
            return_column,
            target_column,
            "M",
        ),
        "quarterly_cohort_metrics": summarize_decile_cohorts(
            combined,
            "predicted_rank_score",
            return_column,
            target_column,
            "Q",
        ),
        "folds": fold_reports,
    }


def main() -> None:
    args = parse_args()
    dataset_path = FEATURES_DIR / args.dataset
    dataset = pd.read_csv(dataset_path, compression="gzip")
    dataset["date"] = pd.to_datetime(dataset["date"])
    dataset, removed_symbols = filter_by_symbol_history(dataset, args.min_symbol_rows)
    feature_columns = feature_columns_for(args.dataset)
    unique_dates = pd.DatetimeIndex(sorted(dataset["date"].drop_duplicates()))
    folds = fold_slices(unique_dates, args)
    if not folds:
        raise ValueError("No valid walk-forward folds could be built with the requested settings.")

    preset_names = args.preset or list(TUNING_PRESETS)
    reports = []
    summary_rows = []
    for name in preset_names:
        preset = TUNING_PRESETS[name]
        report = evaluate_preset(
            dataset,
            folds,
            feature_columns,
            preset,
            args.target_column,
            args.return_column,
            args.train_sample_frequency,
        )
        metrics = report["combined_test_metrics"]
        monthly = report["monthly_cohort_metrics"]
        quarterly = report["quarterly_cohort_metrics"]
        row = {
            "preset": name,
            "feature_count": len(feature_columns),
            "top_decile_return": metrics["top_decile_return"],
            "top_decile_return_median": metrics["top_decile_return_median"],
            "top_decile_hit_rate": metrics["top_decile_hit_rate"],
            "top_minus_bottom_return": metrics["top_minus_bottom_return"],
            "monthly_top_decile_return_mean": monthly["top_return_mean"],
            "monthly_top_decile_return_median": monthly["top_return_median"],
            "monthly_top_decile_member_return_median": monthly["top_member_return_median"],
            "monthly_top_minus_bottom_return_median": monthly["spread_median"],
            "quarterly_top_decile_return_mean": quarterly["top_return_mean"],
            "quarterly_top_decile_return_median": quarterly["top_return_median"],
            "quarterly_top_decile_member_return_median": quarterly["top_member_return_median"],
            "quarterly_top_minus_bottom_return_median": quarterly["spread_median"],
            "median_best_iteration": float(pd.Series([fold["best_iteration"] for fold in report["folds"]]).median()),
        }
        summary_rows.append(row)
        reports.append(
            {
                **row,
                "params": preset["params"],
                "num_boost_round": preset["num_boost_round"],
                "early_stopping_rounds": preset["early_stopping_rounds"],
                "monthly_cohort_metrics": monthly,
                "quarterly_cohort_metrics": quarterly,
                "folds": report["folds"],
            }
        )
        print(
            f"{name}: daily {metrics['top_decile_return']:.4f} | "
            f"monthly median {monthly['top_return_median']:.4f} | "
            f"quarterly median {quarterly['top_return_median']:.4f} | "
            f"q spread median {quarterly['spread_median']:.4f}"
        )

    summary = pd.DataFrame(summary_rows).sort_values(
        ["quarterly_top_decile_return_median", "monthly_top_decile_return_median", "quarterly_top_minus_bottom_return_median"],
        ascending=[False, False, False],
    )
    summary_path = REPORTS_DIR / f"{args.output_name}_summary.csv"
    summary.to_csv(summary_path, index=False)
    payload = {
        "generated_at": pd.Timestamp.now("UTC").isoformat(),
        "dataset": str(dataset_path.relative_to(ROOT)),
        "target_column": args.target_column,
        "return_column": args.return_column,
        "feature_count": len(feature_columns),
        "fold_count": len(folds),
        "test_days": args.test_days,
        "validation_days": args.validation_days,
        "embargo_days": args.embargo_days,
        "train_sample_frequency": args.train_sample_frequency,
        "min_symbol_rows": args.min_symbol_rows,
        "removed_symbol_count": len(removed_symbols),
        "removed_symbols": removed_symbols,
        "summary_path": str(summary_path.relative_to(ROOT)),
        "presets": reports,
    }
    write_json(payload, REPORTS_DIR / f"{args.output_name}_report.json")
    print(f"Wrote tuning summary to {summary_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
