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

try:
    import xgboost as xgb
except ModuleNotFoundError as error:  # pragma: no cover - handled at runtime
    raise SystemExit(
        "xgboost is not installed in the current Python environment. "
        "Run scripts/modeling/setup_training_env.sh before tuning."
    ) from error


TUNING_PRESETS = {
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
    return parser.parse_args()


def evaluate_preset(
    dataset: pd.DataFrame,
    folds: list[dict],
    feature_columns: list[str],
    preset: dict,
    target_column: str,
    return_column: str,
) -> dict:
    predictions = []
    fold_reports = []
    for fold in folds:
        train = dataset[dataset["date"].isin(fold["train_dates"])].copy()
        validation = dataset[dataset["date"].isin(fold["validation_dates"])].copy()
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
                "best_iteration": int(booster.best_iteration),
                "split": {key: value for key, value in fold.items() if not key.endswith("_dates")},
                "test_metrics": evaluate_ranked(ordered_test, "predicted_rank_score", return_column, target_column),
            }
        )
    combined = pd.concat(predictions, ignore_index=True)
    return {
        "combined_test_metrics": evaluate_ranked(combined, "predicted_rank_score", return_column, target_column),
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
        report = evaluate_preset(dataset, folds, feature_columns, preset, args.target_column, args.return_column)
        metrics = report["combined_test_metrics"]
        row = {
            "preset": name,
            "feature_count": len(feature_columns),
            "top_decile_return": metrics["top_decile_return"],
            "top_decile_hit_rate": metrics["top_decile_hit_rate"],
            "top_minus_bottom_return": metrics["top_minus_bottom_return"],
            "median_best_iteration": float(pd.Series([fold["best_iteration"] for fold in report["folds"]]).median()),
        }
        summary_rows.append(row)
        reports.append(
            {
                **row,
                "params": preset["params"],
                "num_boost_round": preset["num_boost_round"],
                "early_stopping_rounds": preset["early_stopping_rounds"],
                "folds": report["folds"],
            }
        )
        print(
            f"{name}: top return {metrics['top_decile_return']:.4f} | "
            f"hit {metrics['top_decile_hit_rate']:.3f} | "
            f"spread {metrics['top_minus_bottom_return']:.4f}"
        )

    summary = pd.DataFrame(summary_rows).sort_values("top_decile_return", ascending=False)
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
