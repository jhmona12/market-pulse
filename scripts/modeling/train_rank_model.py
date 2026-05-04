from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from common import FEATURES_DIR, MODELS_DIR, REPORTS_DIR, ROOT, write_json

try:
    import xgboost as xgb
except ModuleNotFoundError as error:  # pragma: no cover - handled at runtime
    raise SystemExit(
        "xgboost is not installed in the current Python environment. "
        "Run scripts/modeling/setup_training_env.sh before training."
    ) from error


TARGET_COLUMN = "relevance_grade_sector_neutral_14d"
RETURN_COLUMN = "sector_neutral_forward_return_14d_after_cost"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train an XGBoost learning-to-rank model for sector-neutral momentum selection.")
    parser.add_argument("--dataset", default="training_dataset.csv.gz", help="Dataset filename inside data/modeling/features.")
    parser.add_argument("--model-name", default="xgboost_rank_sector14", help="Base name for model outputs.")
    parser.add_argument("--test-days", type=int, default=252, help="Approximate number of trading days to reserve for test.")
    parser.add_argument("--validation-days", type=int, default=252, help="Approximate number of trading days to reserve for validation.")
    parser.add_argument("--embargo-days", type=int, default=14, help="Trading-date embargo between train/validation/test windows.")
    return parser.parse_args()


def feature_columns_for(dataset_path: str) -> list[str]:
    metadata_path = FEATURES_DIR / "training_dataset_metadata.json"
    if metadata_path.exists():
        with metadata_path.open() as handle:
            metadata = json.load(handle)
        if dataset_path == "training_dataset.csv.gz" and metadata.get("feature_columns"):
            return list(metadata["feature_columns"])
    frame = pd.read_csv(FEATURES_DIR / dataset_path, compression="gzip", nrows=0)
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
        TARGET_COLUMN,
        "candidate_momentum_setup",
        "meta_label_momentum_success",
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


def group_sizes(frame: pd.DataFrame) -> list[int]:
    return frame.groupby("date", sort=False).size().astype(int).tolist()


def make_dmatrix(frame: pd.DataFrame, feature_columns: list[str]) -> xgb.DMatrix:
    ordered = frame.sort_values(["date", "symbol"]).reset_index(drop=True)
    matrix = xgb.DMatrix(
        ordered[feature_columns].to_numpy(dtype=float),
        label=ordered[TARGET_COLUMN].to_numpy(dtype=float),
        feature_names=feature_columns,
    )
    matrix.set_group(group_sizes(ordered))
    return matrix


def evaluate_ranked(frame: pd.DataFrame, score_column: str) -> dict:
    daily_rows = []
    for _, group in frame.groupby("date", sort=True):
        ranked = group.sort_values(score_column, ascending=False)
        bucket_count = max(1, int(np.ceil(len(ranked) * 0.1)))
        top = ranked.head(bucket_count)
        bottom = ranked.tail(bucket_count)
        daily_rows.append(
            {
                "top_return": top[RETURN_COLUMN].mean(),
                "top_hit_rate": (top[RETURN_COLUMN] > 0).mean(),
                "bottom_return": bottom[RETURN_COLUMN].mean(),
                "spread": top[RETURN_COLUMN].mean() - bottom[RETURN_COLUMN].mean(),
                "avg_grade": top[TARGET_COLUMN].mean(),
            }
        )
    daily = pd.DataFrame(daily_rows)
    return {
        "top_decile_sector_neutral_return_14d": float(daily["top_return"].mean()),
        "top_decile_hit_rate": float(daily["top_hit_rate"].mean()),
        "bottom_decile_sector_neutral_return_14d": float(daily["bottom_return"].mean()),
        "top_minus_bottom_sector_neutral_return_14d": float(daily["spread"].mean()),
        "top_decile_average_relevance_grade": float(daily["avg_grade"].mean()),
    }


def main() -> None:
    args = parse_args()
    dataset_path = FEATURES_DIR / args.dataset
    dataset = pd.read_csv(dataset_path, compression="gzip")
    dataset["date"] = pd.to_datetime(dataset["date"])
    feature_columns = feature_columns_for(args.dataset)

    train_dates, validation_dates, test_dates, split_metadata = split_dates(
        dataset,
        args.validation_days,
        args.test_days,
        args.embargo_days,
    )
    train = dataset[dataset["date"].isin(train_dates)].copy()
    validation = dataset[dataset["date"].isin(validation_dates)].copy()
    test = dataset[dataset["date"].isin(test_dates)].copy()

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
    booster = xgb.train(
        params=params,
        dtrain=dtrain,
        num_boost_round=500,
        evals=[(dtrain, "train"), (dvalidation, "validation")],
        early_stopping_rounds=30,
        verbose_eval=False,
    )

    ordered_test = test.sort_values(["date", "symbol"]).reset_index(drop=True)
    dtest = xgb.DMatrix(ordered_test[feature_columns].to_numpy(dtype=float), feature_names=feature_columns)
    ordered_test["predicted_rank_score"] = booster.predict(dtest)
    ordered_test["predicted_probability"] = ordered_test["predicted_rank_score"]

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
        "predicted_rank_score",
        "predicted_probability",
    ]
    predictions = ordered_test[prediction_columns].copy()
    predictions = predictions.sort_values(["date", "predicted_rank_score"], ascending=[True, False])
    predictions.to_csv(REPORTS_DIR / f"{args.model_name}_test_predictions.csv", index=False)

    report = {
        "generated_at": pd.Timestamp.now("UTC").isoformat(),
        "dataset": str(dataset_path.relative_to(ROOT)),
        "model_path": str(model_base.with_suffix(".json").relative_to(ROOT)),
        "train_rows": int(len(train)),
        "validation_rows": int(len(validation)),
        "test_rows": int(len(test)),
        "feature_count": len(feature_columns),
        "split": split_metadata,
        "target_column": TARGET_COLUMN,
        "return_column": RETURN_COLUMN,
        "best_iteration": int(booster.best_iteration),
        "params": params,
        "test_metrics": evaluate_ranked(ordered_test, "predicted_rank_score"),
        "top_features": [
            {"feature": feature, "gain": float(gain)}
            for feature, gain in sorted(booster.get_score(importance_type="gain").items(), key=lambda item: item[1], reverse=True)[:25]
        ],
    }
    write_json(report, REPORTS_DIR / f"{args.model_name}_report.json")
    print(
        f"Saved rank model to {model_base.with_suffix('.json').relative_to(ROOT)} | "
        f"top decile sector-neutral return {report['test_metrics']['top_decile_sector_neutral_return_14d']:.4f}"
    )


if __name__ == "__main__":
    main()
