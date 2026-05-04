from __future__ import annotations

import argparse

import pandas as pd

from common import FEATURES_DIR, MODELS_DIR, REPORTS_DIR, ROOT, write_json
from train_rank_model import RETURN_COLUMN, TARGET_COLUMN, evaluate_ranked, feature_columns_for, make_dmatrix

try:
    import xgboost as xgb
except ModuleNotFoundError as error:  # pragma: no cover - handled at runtime
    raise SystemExit(
        "xgboost is not installed in the current Python environment. "
        "Run scripts/modeling/setup_training_env.sh before training."
    ) from error


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run embargoed walk-forward XGBoost rank-model evaluation.")
    parser.add_argument("--dataset", default="training_dataset.csv.gz", help="Dataset filename inside data/modeling/features.")
    parser.add_argument("--model-name", default="xgboost_rank_sector14_walk_forward", help="Base name for report outputs.")
    parser.add_argument("--folds", type=int, default=4, help="Number of recent non-overlapping test folds.")
    parser.add_argument("--test-days", type=int, default=63, help="Trading days per test fold.")
    parser.add_argument("--validation-days", type=int, default=126, help="Trading days per validation fold.")
    parser.add_argument("--embargo-days", type=int, default=14, help="Trading-date embargo around validation/test windows.")
    parser.add_argument("--min-train-days", type=int, default=756, help="Minimum expanding-window train size.")
    return parser.parse_args()


def fold_slices(unique_dates: pd.DatetimeIndex, args: argparse.Namespace) -> list[dict]:
    folds = []
    for fold_offset in reversed(range(args.folds)):
        test_end = len(unique_dates) - fold_offset * args.test_days
        test_start = test_end - args.test_days
        validation_end = test_start - args.embargo_days
        validation_start = validation_end - args.validation_days
        train_end = validation_start - args.embargo_days
        if test_start < 0 or validation_start < 0 or train_end < args.min_train_days:
            continue
        folds.append(
            {
                "fold": len(folds) + 1,
                "train_dates": unique_dates[:train_end],
                "validation_dates": unique_dates[validation_start:validation_end],
                "test_dates": unique_dates[test_start:test_end],
                "train_end": unique_dates[train_end - 1].date().isoformat(),
                "validation_start": unique_dates[validation_start].date().isoformat(),
                "validation_end": unique_dates[validation_end - 1].date().isoformat(),
                "test_start": unique_dates[test_start].date().isoformat(),
                "test_end": unique_dates[test_end - 1].date().isoformat(),
            }
        )
    return folds


def train_fold(train: pd.DataFrame, validation: pd.DataFrame, feature_columns: list[str]) -> xgb.Booster:
    dtrain = make_dmatrix(train, feature_columns)
    dvalidation = make_dmatrix(validation, feature_columns)
    params = {
        "objective": "rank:ndcg",
        "eval_metric": ["ndcg@25", "ndcg@50"],
        "eta": 0.05,
        "max_depth": 4,
        "min_child_weight": 50,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "lambda": 2.0,
        "alpha": 0.0,
        "seed": 42,
    }
    return xgb.train(
        params=params,
        dtrain=dtrain,
        num_boost_round=500,
        evals=[(dtrain, "train"), (dvalidation, "validation")],
        early_stopping_rounds=30,
        verbose_eval=False,
    )


def main() -> None:
    args = parse_args()
    dataset_path = FEATURES_DIR / args.dataset
    dataset = pd.read_csv(dataset_path, compression="gzip")
    dataset["date"] = pd.to_datetime(dataset["date"])
    feature_columns = feature_columns_for(args.dataset)
    unique_dates = pd.DatetimeIndex(sorted(dataset["date"].drop_duplicates()))
    folds = fold_slices(unique_dates, args)
    if not folds:
        raise ValueError("No valid walk-forward folds could be built with the requested settings.")

    predictions = []
    fold_reports = []
    for fold in folds:
        train = dataset[dataset["date"].isin(fold["train_dates"])].copy()
        validation = dataset[dataset["date"].isin(fold["validation_dates"])].copy()
        test = dataset[dataset["date"].isin(fold["test_dates"])].copy()
        booster = train_fold(train, validation, feature_columns)

        ordered_test = test.sort_values(["date", "symbol"]).reset_index(drop=True)
        dtest = xgb.DMatrix(ordered_test[feature_columns].to_numpy(dtype=float), feature_names=feature_columns)
        ordered_test["predicted_rank_score"] = booster.predict(dtest)
        ordered_test["predicted_probability"] = ordered_test["predicted_rank_score"]
        ordered_test["fold"] = fold["fold"]
        predictions.append(
            ordered_test[
                [
                    "fold",
                    "date",
                    "symbol",
                    "sector",
                    "close",
                    RETURN_COLUMN,
                    TARGET_COLUMN,
                    "candidate_momentum_setup",
                    "predicted_rank_score",
                    "predicted_probability",
                ]
            ].copy()
        )
        fold_reports.append(
            {
                "fold": fold["fold"],
                "train_rows": int(len(train)),
                "validation_rows": int(len(validation)),
                "test_rows": int(len(test)),
                "best_iteration": int(booster.best_iteration),
                "split": {key: value for key, value in fold.items() if not key.endswith("_dates")},
                "test_metrics": evaluate_ranked(ordered_test, "predicted_rank_score"),
            }
        )

        fold_model = MODELS_DIR / f"{args.model_name}_fold{fold['fold']}.json"
        booster.save_model(str(fold_model))

    combined = pd.concat(predictions, ignore_index=True)
    combined = combined.sort_values(["date", "predicted_rank_score"], ascending=[True, False])
    combined.to_csv(REPORTS_DIR / f"{args.model_name}_test_predictions.csv", index=False)

    report = {
        "generated_at": pd.Timestamp.now("UTC").isoformat(),
        "dataset": str(dataset_path.relative_to(ROOT)),
        "prediction_path": str((REPORTS_DIR / f"{args.model_name}_test_predictions.csv").relative_to(ROOT)),
        "feature_count": len(feature_columns),
        "fold_count": len(folds),
        "test_days": args.test_days,
        "validation_days": args.validation_days,
        "embargo_days": args.embargo_days,
        "target_column": TARGET_COLUMN,
        "return_column": RETURN_COLUMN,
        "combined_test_metrics": evaluate_ranked(combined, "predicted_rank_score"),
        "folds": fold_reports,
    }
    write_json(report, REPORTS_DIR / f"{args.model_name}_report.json")
    print(
        f"Wrote walk-forward rank predictions to {(REPORTS_DIR / f'{args.model_name}_test_predictions.csv').relative_to(ROOT)} | "
        f"combined top decile return {report['combined_test_metrics']['top_decile_sector_neutral_return_14d']:.4f}"
    )


if __name__ == "__main__":
    main()
