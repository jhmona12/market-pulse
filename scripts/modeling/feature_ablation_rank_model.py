from __future__ import annotations

import argparse

import pandas as pd

from common import FEATURES_DIR, REPORTS_DIR, ROOT, write_json
from schema import FEATURE_GROUP_PATTERNS, drop_feature_groups
from train_rank_model import (
    RETURN_COLUMN,
    TARGET_COLUMN,
    add_rank_hyperparameter_args,
    evaluate_ranked,
    feature_columns_for,
    filter_by_symbol_history,
    rank_params_from_args,
)
from walk_forward_rank_model import fold_slices, train_fold

try:
    import xgboost as xgb
except ModuleNotFoundError as error:  # pragma: no cover - handled at runtime
    raise SystemExit(
        "xgboost is not installed in the current Python environment. "
        "Run scripts/modeling/setup_training_env.sh before running ablations."
    ) from error


ABLATION_SPECS = {
    "full": (),
    "no_volatility_risk": ("volatility_risk",),
    "no_liquidity": ("liquidity",),
    "no_long_momentum": ("long_momentum",),
    "no_sector_relative": ("sector_relative",),
    "no_market_context": ("market_context",),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run feature-group ablations for the sector-neutral rank model.")
    parser.add_argument("--dataset", default="training_dataset.csv.gz", help="Dataset filename inside data/modeling/features.")
    parser.add_argument("--output-name", default="xgboost_rank_sector14_feature_ablation", help="Base name for ablation reports.")
    parser.add_argument(
        "--ablation",
        action="append",
        choices=sorted(ABLATION_SPECS),
        help="Ablation spec to run. Repeat for multiple specs. Defaults to all specs.",
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
        help="Drop symbols with fewer than this many feature-complete rows before ablation testing.",
    )
    add_rank_hyperparameter_args(parser)
    return parser.parse_args()


def run_ablation(
    dataset: pd.DataFrame,
    folds: list[dict],
    feature_columns: list[str],
    params: dict,
    args: argparse.Namespace,
) -> tuple[dict, pd.DataFrame]:
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
            params=params,
            num_boost_round=args.num_boost_round,
            early_stopping_rounds=args.early_stopping_rounds,
        )

        ordered_test = test.sort_values(["date", "symbol"]).reset_index(drop=True)
        dtest = xgb.DMatrix(ordered_test[feature_columns].to_numpy(dtype=float), feature_names=feature_columns)
        ordered_test["predicted_rank_score"] = booster.predict(dtest)
        ordered_test["fold"] = fold["fold"]
        predictions.append(
            ordered_test[
                [
                    "fold",
                    "date",
                    "symbol",
                    "sector",
                    RETURN_COLUMN,
                    TARGET_COLUMN,
                    "predicted_rank_score",
                ]
            ].copy()
        )
        fold_reports.append(
            {
                "fold": fold["fold"],
                "best_iteration": int(booster.best_iteration),
                "split": {key: value for key, value in fold.items() if not key.endswith("_dates")},
                "test_metrics": evaluate_ranked(ordered_test, "predicted_rank_score"),
            }
        )

    combined = pd.concat(predictions, ignore_index=True)
    combined = combined.sort_values(["date", "predicted_rank_score"], ascending=[True, False])
    report = {
        "feature_count": len(feature_columns),
        "combined_test_metrics": evaluate_ranked(combined, "predicted_rank_score"),
        "folds": fold_reports,
    }
    return report, combined


def main() -> None:
    args = parse_args()
    dataset_path = FEATURES_DIR / args.dataset
    dataset = pd.read_csv(dataset_path, compression="gzip")
    dataset["date"] = pd.to_datetime(dataset["date"])
    dataset, removed_symbols = filter_by_symbol_history(dataset, args.min_symbol_rows)
    base_feature_columns = feature_columns_for(args.dataset)
    unique_dates = pd.DatetimeIndex(sorted(dataset["date"].drop_duplicates()))
    folds = fold_slices(unique_dates, args)
    if not folds:
        raise ValueError("No valid walk-forward folds could be built with the requested settings.")

    params = rank_params_from_args(args)
    ablation_names = args.ablation or list(ABLATION_SPECS)
    reports = []
    summary_rows = []

    for name in ablation_names:
        dropped_groups = list(ABLATION_SPECS[name])
        feature_columns, dropped_features = drop_feature_groups(base_feature_columns, dropped_groups)
        report, _ = run_ablation(dataset, folds, feature_columns, params, args)
        metrics = report["combined_test_metrics"]
        row = {
            "ablation": name,
            "dropped_groups": ",".join(dropped_groups),
            "feature_count": len(feature_columns),
            "dropped_feature_count": len(dropped_features),
            "top_decile_sector_neutral_return_14d": metrics["top_decile_sector_neutral_return_14d"],
            "top_decile_hit_rate": metrics["top_decile_hit_rate"],
            "top_minus_bottom_sector_neutral_return_14d": metrics["top_minus_bottom_sector_neutral_return_14d"],
        }
        summary_rows.append(row)
        reports.append(
            {
                **row,
                "dropped_features": dropped_features,
                "folds": report["folds"],
            }
        )
        print(
            f"{name}: features {len(feature_columns)} | "
            f"top return {metrics['top_decile_sector_neutral_return_14d']:.4f} | "
            f"hit {metrics['top_decile_hit_rate']:.3f} | "
            f"spread {metrics['top_minus_bottom_sector_neutral_return_14d']:.4f}"
        )

    summary = pd.DataFrame(summary_rows)
    if "full" in summary["ablation"].values:
        full_return = float(summary.loc[summary["ablation"].eq("full"), "top_decile_sector_neutral_return_14d"].iloc[0])
        summary["return_delta_vs_full"] = summary["top_decile_sector_neutral_return_14d"] - full_return
    summary_path = REPORTS_DIR / f"{args.output_name}_summary.csv"
    summary.to_csv(summary_path, index=False)

    payload = {
        "generated_at": pd.Timestamp.now("UTC").isoformat(),
        "dataset": str(dataset_path.relative_to(ROOT)),
        "target_column": TARGET_COLUMN,
        "return_column": RETURN_COLUMN,
        "base_feature_count": len(base_feature_columns),
        "fold_count": len(folds),
        "test_days": args.test_days,
        "validation_days": args.validation_days,
        "embargo_days": args.embargo_days,
        "min_symbol_rows": args.min_symbol_rows,
        "removed_symbol_count": len(removed_symbols),
        "removed_symbols": removed_symbols,
        "feature_group_patterns": FEATURE_GROUP_PATTERNS,
        "params": params,
        "summary_path": str(summary_path.relative_to(ROOT)),
        "ablations": reports,
    }
    write_json(payload, REPORTS_DIR / f"{args.output_name}_report.json")
    print(f"Wrote feature ablation summary to {summary_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
