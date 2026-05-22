from __future__ import annotations

import argparse
import re

import numpy as np
import pandas as pd

from common import FEATURES_DIR, MODELS_DIR, REPORTS_DIR, ROOT, write_json
from schema import RANK_RETURN_COLUMN as RETURN_COLUMN
from schema import RANK_TARGET_COLUMN as TARGET_COLUMN
from schema import feature_columns_for

try:
    import xgboost as xgb
except ModuleNotFoundError as error:  # pragma: no cover - handled at runtime
    raise SystemExit(
        "xgboost is not installed in the current Python environment. "
        "Run scripts/modeling/setup_training_env.sh before training."
    ) from error


DEFAULT_RANK_PARAMS = {
    "objective": "rank:ndcg",
    "eval_metric": ["ndcg@25", "ndcg@50"],
    "eta": 0.04,
    "max_depth": 3,
    "min_child_weight": 80,
    "subsample": 0.85,
    "colsample_bytree": 0.8,
    "lambda": 4.0,
    "alpha": 0.0,
    "seed": 42,
}
DEFAULT_NUM_BOOST_ROUND = 700
DEFAULT_EARLY_STOPPING_ROUNDS = 40
EXTRA_DIAGNOSTIC_COLUMNS = [
    "sector_neutral_forward_return_252d_after_cost",
    "drawdown_adjusted_sector_neutral_return_252d_after_cost",
    "max_drawdown_252d_next_close",
    "sector_max_drawdown_252d_next_close",
    "relative_max_drawdown_252d_next_close",
    "relevance_grade_sector_neutral_252d",
    "relevance_grade_drawdown_adjusted_252d",
]


def add_rank_hyperparameter_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--eta", type=float, default=DEFAULT_RANK_PARAMS["eta"], help="XGBoost learning rate.")
    parser.add_argument("--max-depth", type=int, default=DEFAULT_RANK_PARAMS["max_depth"], help="Maximum tree depth.")
    parser.add_argument(
        "--min-child-weight",
        type=float,
        default=DEFAULT_RANK_PARAMS["min_child_weight"],
        help="Minimum child weight regularization.",
    )
    parser.add_argument("--subsample", type=float, default=DEFAULT_RANK_PARAMS["subsample"], help="Row subsample ratio.")
    parser.add_argument(
        "--colsample-bytree",
        type=float,
        default=DEFAULT_RANK_PARAMS["colsample_bytree"],
        help="Column subsample ratio per tree.",
    )
    parser.add_argument("--lambda-reg", type=float, default=DEFAULT_RANK_PARAMS["lambda"], help="L2 regularization.")
    parser.add_argument("--alpha", type=float, default=DEFAULT_RANK_PARAMS["alpha"], help="L1 regularization.")
    parser.add_argument("--seed", type=int, default=DEFAULT_RANK_PARAMS["seed"], help="Random seed.")
    parser.add_argument("--num-boost-round", type=int, default=DEFAULT_NUM_BOOST_ROUND, help="Maximum boosting rounds.")
    parser.add_argument(
        "--early-stopping-rounds",
        type=int,
        default=DEFAULT_EARLY_STOPPING_ROUNDS,
        help="Validation rounds without improvement before stopping.",
    )


def rank_params_from_args(args: argparse.Namespace) -> dict:
    return {
        "objective": "rank:ndcg",
        "eval_metric": ["ndcg@25", "ndcg@50"],
        "eta": args.eta,
        "max_depth": args.max_depth,
        "min_child_weight": args.min_child_weight,
        "subsample": args.subsample,
        "colsample_bytree": args.colsample_bytree,
        "lambda": args.lambda_reg,
        "alpha": args.alpha,
        "seed": args.seed,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train an XGBoost learning-to-rank model for sector-neutral momentum selection.")
    parser.add_argument("--dataset", default="training_dataset.csv.gz", help="Dataset filename inside data/modeling/features.")
    parser.add_argument("--model-name", default="xgboost_rank_sector14", help="Base name for model outputs.")
    parser.add_argument("--target-column", default=TARGET_COLUMN, help="Relevance-grade target column.")
    parser.add_argument("--return-column", default=RETURN_COLUMN, help="Forward return column used for economic evaluation.")
    parser.add_argument("--test-days", type=int, default=252, help="Approximate number of trading days to reserve for test.")
    parser.add_argument("--validation-days", type=int, default=252, help="Approximate number of trading days to reserve for validation.")
    parser.add_argument("--embargo-days", type=int, default=14, help="Trading-date embargo between train/validation/test windows.")
    parser.add_argument(
        "--min-symbol-rows",
        type=int,
        default=0,
        help="Drop symbols with fewer than this many feature-complete rows before splitting. Disabled by default.",
    )
    parser.add_argument(
        "--train-sample-frequency",
        choices=["daily", "weekly", "monthly"],
        default="daily",
        help=(
            "Sample train and validation dates before fitting to reduce dependence from overlapping labels. "
            "The holdout test window is never sampled."
        ),
    )
    add_rank_hyperparameter_args(parser)
    return parser.parse_args()


def filter_by_symbol_history(dataset: pd.DataFrame, min_symbol_rows: int) -> tuple[pd.DataFrame, list[dict]]:
    if min_symbol_rows <= 0:
        return dataset, []

    coverage = (
        dataset.groupby("symbol")
        .agg(rows=("date", "size"), start=("date", "min"), end=("date", "max"))
        .reset_index()
    )
    removed = coverage[coverage["rows"] < min_symbol_rows].sort_values(["rows", "symbol"]).copy()
    keep_symbols = set(coverage.loc[coverage["rows"] >= min_symbol_rows, "symbol"])
    filtered = dataset[dataset["symbol"].isin(keep_symbols)].copy()
    removed_rows = removed.to_dict(orient="records")
    for row in removed_rows:
        row["rows"] = int(row["rows"])
        row["start"] = row["start"].date().isoformat()
        row["end"] = row["end"].date().isoformat()
    return filtered, removed_rows


def split_dates(dataset: pd.DataFrame, validation_days: int, test_days: int, embargo_days: int) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex, pd.DatetimeIndex, dict]:
    unique_dates = pd.DatetimeIndex(sorted(dataset["date"].drop_duplicates()))
    if len(unique_dates) <= validation_days + test_days + embargo_days * 2 + 10:
        raise ValueError("Not enough history to create embargoed train/validation/test splits.")

    test_start_index = len(unique_dates) - test_days
    validation_end_index = test_start_index - embargo_days
    validation_start_index = validation_end_index - validation_days
    train_end_index = max(0, validation_start_index - embargo_days)
    if train_end_index <= 0 or validation_start_index < 0 or validation_end_index <= validation_start_index:
        raise ValueError("Not enough history to create non-empty embargoed train/validation/test splits.")

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


def make_dmatrix(frame: pd.DataFrame, feature_columns: list[str], target_column: str = TARGET_COLUMN) -> xgb.DMatrix:
    ordered = frame.sort_values(["date", "symbol"]).reset_index(drop=True)
    matrix = xgb.DMatrix(
        ordered[feature_columns].to_numpy(dtype=float),
        label=ordered[target_column].to_numpy(dtype=float),
        feature_names=feature_columns,
    )
    matrix.set_group(group_sizes(ordered))
    return matrix


def sample_dates(dates: pd.DatetimeIndex, frequency: str) -> pd.DatetimeIndex:
    if frequency == "daily" or len(dates) == 0:
        return dates
    frame = pd.DataFrame({"date": dates})
    if frequency == "weekly":
        frame["bucket"] = frame["date"].dt.to_period("W-FRI")
    elif frequency == "monthly":
        frame["bucket"] = frame["date"].dt.to_period("M")
    else:
        raise ValueError(f"Unsupported sample frequency: {frequency}")
    sampled = frame.groupby("bucket", sort=True)["date"].first()
    return pd.DatetimeIndex(sampled.tolist())


def horizon_suffix(return_column: str) -> str:
    match = re.search(r"_(\d+d)(?:_|$)", return_column)
    return match.group(1) if match else "forward"


def evaluate_ranked(
    frame: pd.DataFrame,
    score_column: str,
    return_column: str = RETURN_COLUMN,
    target_column: str = TARGET_COLUMN,
) -> dict:
    daily_rows = []
    for _, group in frame.groupby("date", sort=True):
        ranked = group.sort_values(score_column, ascending=False)
        bucket_count = max(1, int(np.ceil(len(ranked) * 0.1)))
        top = ranked.head(bucket_count)
        bottom = ranked.tail(bucket_count)
        daily_rows.append(
            {
                "top_return": top[return_column].mean(),
                "top_return_median": top[return_column].median(),
                "top_hit_rate": (top[return_column] > 0).mean(),
                "bottom_return": bottom[return_column].mean(),
                "bottom_return_median": bottom[return_column].median(),
                "spread": top[return_column].mean() - bottom[return_column].mean(),
                "spread_median": top[return_column].median() - bottom[return_column].median(),
                "avg_grade": top[target_column].mean(),
            }
        )
    daily = pd.DataFrame(daily_rows)
    suffix = horizon_suffix(return_column)
    return {
        "top_decile_return": float(daily["top_return"].mean()),
        "top_decile_return_median": float(daily["top_return"].median()),
        "bottom_decile_return": float(daily["bottom_return"].mean()),
        "bottom_decile_return_median": float(daily["bottom_return"].median()),
        "top_minus_bottom_return": float(daily["spread"].mean()),
        "top_minus_bottom_return_median": float(daily["spread"].median()),
        f"top_decile_sector_neutral_return_{suffix}": float(daily["top_return"].mean()),
        f"top_decile_sector_neutral_return_{suffix}_median": float(daily["top_return"].median()),
        "top_decile_hit_rate": float(daily["top_hit_rate"].mean()),
        f"bottom_decile_sector_neutral_return_{suffix}": float(daily["bottom_return"].mean()),
        f"bottom_decile_sector_neutral_return_{suffix}_median": float(daily["bottom_return"].median()),
        f"top_minus_bottom_sector_neutral_return_{suffix}": float(daily["spread"].mean()),
        f"top_minus_bottom_sector_neutral_return_{suffix}_median": float(daily["spread"].median()),
        "top_decile_average_relevance_grade": float(daily["avg_grade"].mean()),
    }


def main() -> None:
    args = parse_args()
    dataset_path = FEATURES_DIR / args.dataset
    dataset = pd.read_csv(dataset_path, compression="gzip")
    dataset["date"] = pd.to_datetime(dataset["date"])
    dataset, removed_symbols = filter_by_symbol_history(dataset, args.min_symbol_rows)
    feature_columns = feature_columns_for(args.dataset)

    train_dates, validation_dates, test_dates, split_metadata = split_dates(
        dataset,
        args.validation_days,
        args.test_days,
        args.embargo_days,
    )
    original_train_date_count = len(train_dates)
    original_validation_date_count = len(validation_dates)
    train_dates = sample_dates(train_dates, args.train_sample_frequency)
    validation_dates = sample_dates(validation_dates, args.train_sample_frequency)
    train = dataset[dataset["date"].isin(train_dates)].copy()
    validation = dataset[dataset["date"].isin(validation_dates)].copy()
    test = dataset[dataset["date"].isin(test_dates)].copy()

    dtrain = make_dmatrix(train, feature_columns, args.target_column)
    dvalidation = make_dmatrix(validation, feature_columns, args.target_column)

    params = rank_params_from_args(args)
    booster = xgb.train(
        params=params,
        dtrain=dtrain,
        num_boost_round=args.num_boost_round,
        evals=[(dtrain, "train"), (dvalidation, "validation")],
        early_stopping_rounds=args.early_stopping_rounds,
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
        args.return_column,
        args.target_column,
        *[column for column in EXTRA_DIAGNOSTIC_COLUMNS if column in ordered_test.columns],
        "predicted_rank_score",
        "predicted_probability",
    ]
    if "candidate_momentum_setup" in ordered_test.columns:
        prediction_columns.insert(-2, "candidate_momentum_setup")
    prediction_columns = list(dict.fromkeys(prediction_columns))
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
        "train_date_count": int(len(train_dates)),
        "validation_date_count": int(len(validation_dates)),
        "test_date_count": int(len(test_dates)),
        "original_train_date_count": int(original_train_date_count),
        "original_validation_date_count": int(original_validation_date_count),
        "train_sample_frequency": args.train_sample_frequency,
        "min_symbol_rows": args.min_symbol_rows,
        "removed_symbol_count": len(removed_symbols),
        "removed_symbols": removed_symbols,
        "feature_count": len(feature_columns),
        "split": split_metadata,
        "target_column": args.target_column,
        "return_column": args.return_column,
        "best_iteration": int(booster.best_iteration),
        "params": params,
        "test_metrics": evaluate_ranked(ordered_test, "predicted_rank_score", args.return_column, args.target_column),
        "top_features": [
            {"feature": feature, "gain": float(gain)}
            for feature, gain in sorted(booster.get_score(importance_type="gain").items(), key=lambda item: item[1], reverse=True)[:25]
        ],
    }
    write_json(report, REPORTS_DIR / f"{args.model_name}_report.json")
    print(
        f"Saved rank model to {model_base.with_suffix('.json').relative_to(ROOT)} | "
        f"top decile return {report['test_metrics']['top_decile_return']:.4f}"
    )


if __name__ == "__main__":
    main()
