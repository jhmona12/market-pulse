from __future__ import annotations

import argparse

import pandas as pd

from common import FEATURES_DIR, MODELS_DIR, REPORTS_DIR, ROOT, write_json
from train_rank_model import (
    DEFAULT_EARLY_STOPPING_ROUNDS,
    DEFAULT_NUM_BOOST_ROUND,
    DEFAULT_RANK_PARAMS,
    RETURN_COLUMN,
    TARGET_COLUMN,
    add_rank_hyperparameter_args,
    evaluate_ranked,
    feature_columns_for,
    filter_by_symbol_history,
    make_dmatrix,
    rank_params_from_args,
)

try:
    import xgboost as xgb
except ModuleNotFoundError as error:  # pragma: no cover - handled at runtime
    raise SystemExit(
        "xgboost is not installed in the current Python environment. "
        "Run scripts/modeling/setup_training_env.sh before training."
    ) from error


EXTRA_DIAGNOSTIC_COLUMNS = [
    "sector_neutral_forward_return_252d_after_cost",
    "drawdown_adjusted_sector_neutral_return_252d_after_cost",
    "max_drawdown_252d_next_close",
    "sector_max_drawdown_252d_next_close",
    "relative_max_drawdown_252d_next_close",
    "relevance_grade_sector_neutral_252d",
    "relevance_grade_drawdown_adjusted_252d",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run embargoed walk-forward XGBoost rank-model evaluation.")
    parser.add_argument("--dataset", default="training_dataset.csv.gz", help="Dataset filename inside data/modeling/features.")
    parser.add_argument("--model-name", default="xgboost_rank_sector14_walk_forward", help="Base name for report outputs.")
    parser.add_argument("--target-column", default=TARGET_COLUMN, help="Relevance-grade target column.")
    parser.add_argument("--return-column", default=RETURN_COLUMN, help="Forward return column used for economic evaluation.")
    parser.add_argument("--folds", type=int, default=4, help="Number of recent non-overlapping test folds.")
    parser.add_argument("--test-days", type=int, default=63, help="Trading days per test fold.")
    parser.add_argument("--validation-days", type=int, default=126, help="Trading days per validation fold.")
    parser.add_argument("--embargo-days", type=int, default=14, help="Trading-date embargo around validation/test windows.")
    parser.add_argument("--min-train-days", type=int, default=756, help="Minimum expanding-window train size.")
    parser.add_argument(
        "--min-symbol-rows",
        type=int,
        default=0,
        help="Drop symbols with fewer than this many feature-complete rows before walk-forward splitting.",
    )
    parser.add_argument(
        "--train-sample-frequency",
        choices=["daily", "weekly", "monthly"],
        default="daily",
        help=(
            "Sample train dates before fitting to reduce dependence from overlapping labels. "
            "Test dates are never sampled; validation uses the same sampling frequency as training."
        ),
    )
    add_rank_hyperparameter_args(parser)
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


def train_fold(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    feature_columns: list[str],
    target_column: str = TARGET_COLUMN,
    params: dict | None = None,
    num_boost_round: int = DEFAULT_NUM_BOOST_ROUND,
    early_stopping_rounds: int = DEFAULT_EARLY_STOPPING_ROUNDS,
) -> xgb.Booster:
    dtrain = make_dmatrix(train, feature_columns, target_column)
    dvalidation = make_dmatrix(validation, feature_columns, target_column)
    return xgb.train(
        params=params or DEFAULT_RANK_PARAMS,
        dtrain=dtrain,
        num_boost_round=num_boost_round,
        evals=[(dtrain, "train"), (dvalidation, "validation")],
        early_stopping_rounds=early_stopping_rounds,
        verbose_eval=False,
    )


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


def main() -> None:
    args = parse_args()
    dataset_path = FEATURES_DIR / args.dataset
    dataset = pd.read_csv(dataset_path, compression="gzip")
    dataset["date"] = pd.to_datetime(dataset["date"])
    dataset, removed_symbols = filter_by_symbol_history(dataset, args.min_symbol_rows)
    feature_columns = feature_columns_for(args.dataset)
    params = rank_params_from_args(args)
    unique_dates = pd.DatetimeIndex(sorted(dataset["date"].drop_duplicates()))
    folds = fold_slices(unique_dates, args)
    if not folds:
        raise ValueError("No valid walk-forward folds could be built with the requested settings.")

    predictions = []
    fold_reports = []
    for fold in folds:
        train_dates = sample_dates(fold["train_dates"], args.train_sample_frequency)
        validation_dates = sample_dates(fold["validation_dates"], args.train_sample_frequency)
        train = dataset[dataset["date"].isin(train_dates)].copy()
        validation = dataset[dataset["date"].isin(validation_dates)].copy()
        test = dataset[dataset["date"].isin(fold["test_dates"])].copy()
        booster = train_fold(
            train,
            validation,
            feature_columns,
            target_column=args.target_column,
            params=params,
            num_boost_round=args.num_boost_round,
            early_stopping_rounds=args.early_stopping_rounds,
        )

        ordered_test = test.sort_values(["date", "symbol"]).reset_index(drop=True)
        dtest = xgb.DMatrix(ordered_test[feature_columns].to_numpy(dtype=float), feature_names=feature_columns)
        ordered_test["predicted_rank_score"] = booster.predict(dtest)
        ordered_test["predicted_probability"] = ordered_test["predicted_rank_score"]
        ordered_test["fold"] = fold["fold"]
        prediction_columns = [
            "fold",
            "date",
            "symbol",
            "sector",
            "close",
            args.return_column,
            args.target_column,
            *[column for column in EXTRA_DIAGNOSTIC_COLUMNS if column in ordered_test.columns],
            "predicted_rank_score",
            "predicted_probability",
        ] + (["candidate_momentum_setup"] if "candidate_momentum_setup" in ordered_test.columns else [])
        prediction_columns = list(dict.fromkeys(prediction_columns))
        predictions.append(ordered_test[prediction_columns].copy())
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
                "test_metrics": evaluate_ranked(ordered_test, "predicted_rank_score", args.return_column, args.target_column),
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
        "train_sample_frequency": args.train_sample_frequency,
        "min_symbol_rows": args.min_symbol_rows,
        "removed_symbol_count": len(removed_symbols),
        "removed_symbols": removed_symbols,
        "target_column": args.target_column,
        "return_column": args.return_column,
        "params": params,
        "num_boost_round": args.num_boost_round,
        "early_stopping_rounds": args.early_stopping_rounds,
        "combined_test_metrics": evaluate_ranked(combined, "predicted_rank_score", args.return_column, args.target_column),
        "folds": fold_reports,
    }
    write_json(report, REPORTS_DIR / f"{args.model_name}_report.json")
    print(
        f"Wrote walk-forward rank predictions to {(REPORTS_DIR / f'{args.model_name}_test_predictions.csv').relative_to(ROOT)} | "
        f"combined top decile return {report['combined_test_metrics']['top_decile_return']:.4f}"
    )


if __name__ == "__main__":
    main()
