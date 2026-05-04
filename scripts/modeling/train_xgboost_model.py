from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from common import FEATURES_DIR, MODELS_DIR, REPORTS_DIR, ROOT, write_json

try:
    import xgboost as xgb
except ModuleNotFoundError as error:  # pragma: no cover - handled at runtime
    raise SystemExit(
        "xgboost is not installed in the current Python environment. "
        "Install it before running the training step."
    ) from error


TARGET_COLUMN = "label_outperform_spy_14d"
IDENTITY_COLUMNS = {
    "date",
    "symbol",
    "sector",
    "close",
    "forward_return_14d",
    "spy_forward_return_14d",
    "excess_return_14d",
    TARGET_COLUMN,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train an XGBoost classifier for 14-day SPY-relative outperformance.")
    parser.add_argument("--dataset", default="training_dataset.csv.gz", help="Dataset filename inside data/modeling/features.")
    parser.add_argument("--model-name", default="xgboost_spy14", help="Base name for model outputs.")
    parser.add_argument("--test-days", type=int, default=252, help="Approximate number of trading days to reserve for test.")
    parser.add_argument("--validation-days", type=int, default=252, help="Approximate number of trading days to reserve for validation.")
    parser.add_argument("--exclude-macro", action="store_true", help="Drop raw macro feature columns from the training matrix.")
    parser.add_argument(
        "--train-excess-return-cap",
        type=float,
        default=None,
        help="Drop train/validation rows where abs(excess_return_14d) is above this threshold. Test rows are left untouched.",
    )
    return parser.parse_args()


def auc_score(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    order = np.argsort(y_prob)
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(1, len(y_prob) + 1)
    positives = y_true == 1
    negatives = y_true == 0
    pos_count = positives.sum()
    neg_count = negatives.sum()
    if pos_count == 0 or neg_count == 0:
        return float("nan")
    rank_sum = ranks[positives].sum()
    return float((rank_sum - pos_count * (pos_count + 1) / 2) / (pos_count * neg_count))


def log_loss(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    clipped = np.clip(y_prob, 1e-6, 1 - 1e-6)
    return float(-(y_true * np.log(clipped) + (1 - y_true) * np.log(1 - clipped)).mean())


def top_bucket_excess_return(frame: pd.DataFrame, probability_column: str, fraction: float = 0.1) -> float:
    threshold_count = max(1, int(len(frame) * fraction))
    ranked = frame.sort_values(probability_column, ascending=False).head(threshold_count)
    return float(ranked["excess_return_14d"].mean())


def date_balanced_bucket_metrics(frame: pd.DataFrame, probability_column: str, fraction: float = 0.1) -> dict[str, float]:
    daily_rows = []
    for _, group in frame.groupby("date", sort=True):
        ranked = group.sort_values(probability_column, ascending=False)
        threshold_count = max(1, int(np.ceil(len(ranked) * fraction)))
        top = ranked.head(threshold_count)
        bottom = ranked.tail(threshold_count)
        daily_rows.append(
            {
                "top_excess": top["excess_return_14d"].mean(),
                "top_hit_rate": top[TARGET_COLUMN].mean(),
                "bottom_excess": bottom["excess_return_14d"].mean(),
                "spread": top["excess_return_14d"].mean() - bottom["excess_return_14d"].mean(),
            }
        )
    daily = pd.DataFrame(daily_rows)
    return {
        "top_decile_average_excess_return_14d": float(daily["top_excess"].mean()),
        "top_decile_hit_rate": float(daily["top_hit_rate"].mean()),
        "bottom_decile_average_excess_return_14d": float(daily["bottom_excess"].mean()),
        "top_minus_bottom_decile_excess_return_14d": float(daily["spread"].mean()),
    }


def split_dates(dataset: pd.DataFrame, validation_days: int, test_days: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    unique_dates = sorted(dataset["date"].drop_duplicates())
    if len(unique_dates) <= validation_days + test_days + 10:
        raise ValueError("Not enough history to create train/validation/test splits.")
    validation_start = unique_dates[-(validation_days + test_days)]
    test_start = unique_dates[-test_days]
    return pd.Timestamp(validation_start), pd.Timestamp(test_start)


def main() -> None:
    args = parse_args()
    dataset_path = FEATURES_DIR / args.dataset
    dataset = pd.read_csv(dataset_path, compression="gzip")
    dataset["date"] = pd.to_datetime(dataset["date"])

    validation_start, test_start = split_dates(dataset, args.validation_days, args.test_days)
    train = dataset[dataset["date"] < validation_start].copy()
    validation = dataset[(dataset["date"] >= validation_start) & (dataset["date"] < test_start)].copy()
    test = dataset[dataset["date"] >= test_start].copy()
    dropped_train_outliers = 0
    dropped_validation_outliers = 0
    if args.train_excess_return_cap is not None:
        train_keep = train["excess_return_14d"].abs() <= args.train_excess_return_cap
        validation_keep = validation["excess_return_14d"].abs() <= args.train_excess_return_cap
        dropped_train_outliers = int((~train_keep).sum())
        dropped_validation_outliers = int((~validation_keep).sum())
        train = train[train_keep].copy()
        validation = validation[validation_keep].copy()

    feature_columns = [column for column in dataset.columns if column not in IDENTITY_COLUMNS]
    if args.exclude_macro:
        feature_columns = [
            column
            for column in feature_columns
            if not (column.endswith("_level") or column.endswith("_chg_1") or column.endswith("_chg_5"))
        ]
    x_train = train[feature_columns].to_numpy(dtype=float)
    x_validation = validation[feature_columns].to_numpy(dtype=float)
    x_test = test[feature_columns].to_numpy(dtype=float)
    y_train = train[TARGET_COLUMN].to_numpy(dtype=int)
    y_validation = validation[TARGET_COLUMN].to_numpy(dtype=int)
    y_test = test[TARGET_COLUMN].to_numpy(dtype=int)

    dtrain = xgb.DMatrix(x_train, label=y_train, feature_names=feature_columns)
    dvalidation = xgb.DMatrix(x_validation, label=y_validation, feature_names=feature_columns)
    dtest = xgb.DMatrix(x_test, label=y_test, feature_names=feature_columns)

    params = {
        "objective": "binary:logistic",
        "eval_metric": ["logloss", "auc"],
        "eta": 0.05,
        "max_depth": 5,
        "min_child_weight": 20,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "lambda": 1.0,
        "alpha": 0.0,
        "seed": 42,
    }
    booster = xgb.train(
        params=params,
        dtrain=dtrain,
        num_boost_round=500,
        evals=[(dtrain, "train"), (dvalidation, "validation")],
        early_stopping_rounds=25,
        verbose_eval=False,
    )

    validation_prob = booster.predict(dvalidation)
    test_prob = booster.predict(dtest)
    validation["predicted_probability"] = validation_prob
    test["predicted_probability"] = test_prob

    date_balanced_metrics = date_balanced_bucket_metrics(test, "predicted_probability", 0.1)
    test_metrics = {
        "auc": auc_score(y_test, test_prob),
        "log_loss": log_loss(y_test, test_prob),
        "accuracy_at_0_5": float(((test_prob >= 0.5).astype(int) == y_test).mean()),
        "positive_rate": float(y_test.mean()),
        "average_excess_return_14d": float(test["excess_return_14d"].mean()),
        "top_decile_average_excess_return_14d": date_balanced_metrics["top_decile_average_excess_return_14d"],
        "top_decile_hit_rate": date_balanced_metrics["top_decile_hit_rate"],
        "bottom_decile_average_excess_return_14d": date_balanced_metrics["bottom_decile_average_excess_return_14d"],
        "top_minus_bottom_decile_excess_return_14d": date_balanced_metrics["top_minus_bottom_decile_excess_return_14d"],
        "global_top_decile_average_excess_return_14d": top_bucket_excess_return(test, "predicted_probability", 0.1),
        "global_top_quintile_average_excess_return_14d": top_bucket_excess_return(test, "predicted_probability", 0.2),
        "prediction_excess_return_correlation": float(test["predicted_probability"].corr(test["excess_return_14d"])),
        "prediction_label_correlation": float(test["predicted_probability"].corr(test[TARGET_COLUMN])),
    }
    validation_metrics = {
        "auc": auc_score(y_validation, validation_prob),
        "log_loss": log_loss(y_validation, validation_prob),
        "accuracy_at_0_5": float(((validation_prob >= 0.5).astype(int) == y_validation).mean()),
    }

    model_base = MODELS_DIR / args.model_name
    model_base.parent.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(model_base.with_suffix(".json")))

    importance = booster.get_score(importance_type="gain")
    top_features = sorted(importance.items(), key=lambda item: item[1], reverse=True)[:25]

    predictions = test[["date", "symbol", "sector", "close", "excess_return_14d", TARGET_COLUMN, "predicted_probability"]].copy()
    predictions = predictions.sort_values(["date", "predicted_probability"], ascending=[True, False])
    predictions.to_csv(REPORTS_DIR / f"{args.model_name}_test_predictions.csv", index=False)

    report = {
        "generated_at": pd.Timestamp.now("UTC").isoformat(),
        "dataset": str(dataset_path.relative_to(ROOT)),
        "model_path": str(model_base.with_suffix(".json").relative_to(ROOT)),
        "train_rows": int(len(train)),
        "validation_rows": int(len(validation)),
        "test_rows": int(len(test)),
        "dropped_train_outliers": dropped_train_outliers,
        "dropped_validation_outliers": dropped_validation_outliers,
        "train_excess_return_cap": args.train_excess_return_cap,
        "validation_start": validation_start.date().isoformat(),
        "test_start": test_start.date().isoformat(),
        "feature_count": len(feature_columns),
        "exclude_macro": bool(args.exclude_macro),
        "best_iteration": int(booster.best_iteration),
        "params": params,
        "validation_metrics": validation_metrics,
        "test_metrics": test_metrics,
        "top_features": [{"feature": feature, "gain": float(gain)} for feature, gain in top_features],
    }
    write_json(report, REPORTS_DIR / f"{args.model_name}_report.json")
    print(f"Saved model to {model_base.with_suffix('.json').relative_to(ROOT)}")
    print(
        f"Test AUC: {test_metrics['auc']:.4f} | "
        f"Date-balanced top decile average excess return: {test_metrics['top_decile_average_excess_return_14d']:.4f}"
    )


if __name__ == "__main__":
    main()
