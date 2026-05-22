from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from common import FEATURES_DIR, ROOT, write_json
from train_rank_model import DEFAULT_RANK_PARAMS, RETURN_COLUMN, TARGET_COLUMN, feature_columns_for, make_dmatrix, rank_params_from_args, sample_dates

try:
    import xgboost as xgb
except ModuleNotFoundError as error:  # pragma: no cover - handled at runtime
    raise SystemExit(
        "xgboost is not installed in the current Python environment. "
        "Run scripts/modeling/setup_training_env.sh before exporting a production model."
    ) from error


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train and export the production daily rank model artifact.")
    parser.add_argument("--dataset", default="training_dataset.csv.gz", help="Dataset filename inside data/modeling/features.")
    parser.add_argument("--output-dir", default="models/rank", help="Committed model artifact directory.")
    parser.add_argument("--model-name", default="xgboost_rank_sector14_tuned", help="Base name for exported artifacts.")
    parser.add_argument("--target-column", default=TARGET_COLUMN, help="Relevance-grade target column.")
    parser.add_argument("--return-column", default=RETURN_COLUMN, help="Forward return column used for metadata.")
    parser.add_argument(
        "--num-boost-round",
        type=int,
        default=25,
        help="Fixed production boosting rounds, based on walk-forward early-stopping diagnostics.",
    )
    parser.add_argument(
        "--train-sample-frequency",
        choices=["daily", "weekly", "monthly"],
        default="daily",
        help="Sample training dates before fitting the exported artifact. Use monthly for the long-horizon research model.",
    )
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
    parser.add_argument(
        "--methodology-note",
        action="append",
        default=[],
        help="Methodology note to store in exported metadata. Repeat for multiple notes.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dataset_path = FEATURES_DIR / args.dataset
    dataset = pd.read_csv(dataset_path, compression="gzip")
    dataset["date"] = pd.to_datetime(dataset["date"])
    feature_columns = feature_columns_for(args.dataset)
    original_date_count = int(dataset["date"].nunique())
    sampled_dates = sample_dates(pd.DatetimeIndex(sorted(dataset["date"].drop_duplicates())), args.train_sample_frequency)
    if args.train_sample_frequency != "daily":
        dataset = dataset[dataset["date"].isin(sampled_dates)].copy()

    params = rank_params_from_args(args)
    dtrain = make_dmatrix(dataset, feature_columns, args.target_column)
    booster = xgb.train(
        params=params,
        dtrain=dtrain,
        num_boost_round=args.num_boost_round,
        evals=[(dtrain, "train")],
        verbose_eval=False,
    )

    output_dir = ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / f"{args.model_name}.json"
    metadata_path = output_dir / f"{args.model_name}_metadata.json"
    booster.save_model(str(model_path))

    metadata = {
        "generated_at": pd.Timestamp.now("UTC").isoformat(),
        "dataset": str(dataset_path.relative_to(ROOT)),
        "model_path": str(model_path.relative_to(ROOT)),
        "target_column": args.target_column,
        "return_column": args.return_column,
        "feature_count": len(feature_columns),
        "feature_columns": feature_columns,
        "training_rows": int(len(dataset)),
        "training_symbols": int(dataset["symbol"].nunique()),
        "training_start_date": str(dataset["date"].min().date()),
        "training_end_date": str(dataset["date"].max().date()),
        "training_date_count": int(len(sampled_dates)),
        "original_training_date_count": original_date_count,
        "train_sample_frequency": args.train_sample_frequency,
        "params": params,
        "num_boost_round": args.num_boost_round,
        "notes": (
            "Exported rank model artifact. For the long-horizon model, monthly sampling is used to reduce dependence "
            "from overlapping 252-trading-day labels."
        ),
        "methodology_rationale": args.methodology_note,
    }
    write_json(metadata, metadata_path)
    print(f"Exported production rank model to {model_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
