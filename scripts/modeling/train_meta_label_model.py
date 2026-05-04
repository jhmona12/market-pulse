from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from common import FEATURES_DIR, MODELS_DIR, REPORTS_DIR, ROOT, write_json
from train_xgboost_model import auc_score, log_loss

try:
    import xgboost as xgb
except ModuleNotFoundError as error:  # pragma: no cover - handled at runtime
    raise SystemExit(
        "xgboost is not installed in the current Python environment. "
        "Run scripts/modeling/setup_training_env.sh before training."
    ) from error


TARGET_COLUMN = "meta_label_momentum_success"
RETURN_COLUMN = "sector_neutral_forward_return_14d_after_cost"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train a candidate-only meta-label model for screened momentum setups.")
    parser.add_argument("--dataset", default="training_dataset.csv.gz", help="Dataset filename inside data/modeling/features.")
    parser.add_argument("--model-name", default="xgboost_meta_momentum14", help="Base name for model outputs.")
    parser.add_argument("--test-days", type=int, default=252, help="Approximate number of trading days to reserve for test.")
    parser.add_argument("--validation-days", type=int, default=252, help="Approximate number of trading days to reserve for validation.")
    parser.add_argument("--embargo-days", type=int, default=14, help="Trading-date embargo between train/validation/test windows.")
    return parser.parse_args()


def feature_columns_for(dataset_name: str) -> list[str]:
    metadata_path = FEATURES_DIR / "training_dataset_metadata.json"
    if metadata_path.exists() and dataset_name == "training_dataset.csv.gz":
        with metadata_path.open() as handle:
            metadata = json.load(handle)
        if metadata.get("feature_columns"):
            return list(metadata["feature_columns"])
    frame = pd.read_csv(FEATURES_DIR / dataset_name, compression="gzip", nrows=0)
    excluded = {
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
        RETURN_COLUMN,
        "max_drawdown_14d_next_close",
        "sector_max_drawdown_14d_next_close",
        "label_outperform_spy_14d",
        "label_sector_neutral_positive_14d",
        "label_sector_neutral_hurdle_14d",
        "sector_neutral_forward_return_pct_rank",
        "relevance_grade_sector_neutral_14d",
        "candidate_momentum_setup",
        TARGET_COLUMN,
    }
    return [column for column in frame.columns if column not in excluded]


def split_dates(dataset: pd.DataFrame, validation_days: int, test_days: int, embargo_days: int) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex, pd.DatetimeIndex, dict]:
    unique_dates = pd.DatetimeIndex(sorted(dataset["date"].drop_duplicates()))
    if len(unique_dates) <= validation_days + test_days + embargo_days * 2 + 10:
        raise ValueError("Not enough history to create embargoed train/validation/test splits.")
    test_start_index = len(unique_dates) - test_days
    validation_start_index = test_start_index - validation_days
    train_end_index = max(0, validation_start_index - embargo_days)
    validation_end_index = max(validation_start_index, test_start_index - embargo_days)
    metadata = {
        "train_end": unique_dates[train_end_index - 1].date().isoformat() if train_end_index else None,
        "validation_start": unique_dates[validation_start_index].date().isoformat(),
        "validation_end": unique_dates[validation_end_index - 1].date().isoformat() if validation_end_index > validation_start_index else None,
        "test_start": unique_dates[test_start_index].date().isoformat(),
        "embargo_days": embargo_days,
    }
    return (
        unique_dates[:train_end_index],
        unique_dates[validation_start_index:validation_end_index],
        unique_dates[test_start_index:],
        metadata,
    )


def evaluate(frame: pd.DataFrame, probability_column: str) -> dict:
    daily_rows = []
    for _, group in frame.groupby("date", sort=True):
        ranked = group.sort_values(probability_column, ascending=False)
        bucket_count = max(1, int(np.ceil(len(ranked) * 0.25)))
        top = ranked.head(bucket_count)
        bottom = ranked.tail(bucket_count)
        daily_rows.append(
            {
                "top_return": top[RETURN_COLUMN].mean(),
                "top_success_rate": top[TARGET_COLUMN].mean(),
                "bottom_return": bottom[RETURN_COLUMN].mean(),
                "spread": top[RETURN_COLUMN].mean() - bottom[RETURN_COLUMN].mean(),
            }
        )
    daily = pd.DataFrame(daily_rows)
    return {
        "top_quartile_sector_neutral_return_14d": float(daily["top_return"].mean()),
        "top_quartile_success_rate": float(daily["top_success_rate"].mean()),
        "bottom_quartile_sector_neutral_return_14d": float(daily["bottom_return"].mean()),
        "top_minus_bottom_sector_neutral_return_14d": float(daily["spread"].mean()),
    }


def main() -> None:
    args = parse_args()
    dataset_path = FEATURES_DIR / args.dataset
    dataset = pd.read_csv(dataset_path, compression="gzip")
    dataset["date"] = pd.to_datetime(dataset["date"])
    candidates = dataset[dataset["candidate_momentum_setup"].eq(1) & dataset[TARGET_COLUMN].notna()].copy()
    feature_columns = feature_columns_for(args.dataset)

    train_dates, validation_dates, test_dates, split_metadata = split_dates(
        dataset,
        args.validation_days,
        args.test_days,
        args.embargo_days,
    )
    train = candidates[candidates["date"].isin(train_dates)].copy()
    validation = candidates[candidates["date"].isin(validation_dates)].copy()
    test = candidates[candidates["date"].isin(test_dates)].copy()

    if train[TARGET_COLUMN].nunique() < 2 or validation[TARGET_COLUMN].nunique() < 2:
        raise ValueError("Meta-label train/validation split needs both success and failure classes.")

    dtrain = xgb.DMatrix(train[feature_columns].to_numpy(dtype=float), label=train[TARGET_COLUMN].to_numpy(dtype=int), feature_names=feature_columns)
    dvalidation = xgb.DMatrix(
        validation[feature_columns].to_numpy(dtype=float),
        label=validation[TARGET_COLUMN].to_numpy(dtype=int),
        feature_names=feature_columns,
    )
    positive_rate = train[TARGET_COLUMN].mean()
    scale_pos_weight = float((1 - positive_rate) / positive_rate) if positive_rate else 1.0
    params = {
        "objective": "binary:logistic",
        "eval_metric": ["logloss", "auc"],
        "eta": 0.05,
        "max_depth": 3,
        "min_child_weight": 20,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "lambda": 2.0,
        "alpha": 0.0,
        "scale_pos_weight": scale_pos_weight,
        "seed": 42,
    }
    booster = xgb.train(
        params=params,
        dtrain=dtrain,
        num_boost_round=400,
        evals=[(dtrain, "train"), (dvalidation, "validation")],
        early_stopping_rounds=30,
        verbose_eval=False,
    )

    test = test.sort_values(["date", "symbol"]).reset_index(drop=True)
    dtest = xgb.DMatrix(test[feature_columns].to_numpy(dtype=float), label=test[TARGET_COLUMN].to_numpy(dtype=int), feature_names=feature_columns)
    test_prob = booster.predict(dtest)
    test["predicted_probability"] = test_prob

    model_base = MODELS_DIR / args.model_name
    model_base.parent.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(model_base.with_suffix(".json")))

    prediction_columns = [
        "date",
        "symbol",
        "sector",
        "close",
        RETURN_COLUMN,
        TARGET_COLUMN,
        "candidate_momentum_setup",
        "predicted_probability",
    ]
    predictions = test[prediction_columns].sort_values(["date", "predicted_probability"], ascending=[True, False])
    predictions.to_csv(REPORTS_DIR / f"{args.model_name}_test_predictions.csv", index=False)

    y_test = test[TARGET_COLUMN].to_numpy(dtype=int)
    report = {
        "generated_at": pd.Timestamp.now("UTC").isoformat(),
        "dataset": str(dataset_path.relative_to(ROOT)),
        "model_path": str(model_base.with_suffix(".json").relative_to(ROOT)),
        "candidate_rows": int(len(candidates)),
        "train_rows": int(len(train)),
        "validation_rows": int(len(validation)),
        "test_rows": int(len(test)),
        "train_success_rate": float(train[TARGET_COLUMN].mean()),
        "validation_success_rate": float(validation[TARGET_COLUMN].mean()),
        "test_success_rate": float(test[TARGET_COLUMN].mean()),
        "feature_count": len(feature_columns),
        "split": split_metadata,
        "target_column": TARGET_COLUMN,
        "return_column": RETURN_COLUMN,
        "best_iteration": int(booster.best_iteration),
        "params": params,
        "test_metrics": {
            "auc": auc_score(y_test, test_prob),
            "log_loss": log_loss(y_test, test_prob),
            **evaluate(test, "predicted_probability"),
        },
        "top_features": [
            {"feature": feature, "gain": float(gain)}
            for feature, gain in sorted(booster.get_score(importance_type="gain").items(), key=lambda item: item[1], reverse=True)[:25]
        ],
    }
    write_json(report, REPORTS_DIR / f"{args.model_name}_report.json")
    print(
        f"Saved meta-label model to {model_base.with_suffix('.json').relative_to(ROOT)} | "
        f"top quartile return {report['test_metrics']['top_quartile_sector_neutral_return_14d']:.4f}"
    )


if __name__ == "__main__":
    main()
