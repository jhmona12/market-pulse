from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the full Market Pulse modeling pipeline.")
    parser.add_argument("--years", type=int, default=10, help="Trailing years of raw history to cache.")
    parser.add_argument("--dataset-name", default="training_dataset.csv.gz", help="Output dataset name.")
    parser.add_argument("--model-name", default="xgboost_spy14", help="Base name for model outputs.")
    parser.add_argument("--skip-fetch", action="store_true", help="Reuse cached raw data and skip the fetch step.")
    parser.add_argument("--skip-train", action="store_true", help="Fetch data and build dataset without training.")
    parser.add_argument("--skip-backtest", action="store_true", help="Skip the post-training backtest report.")
    parser.add_argument("--skip-explain", action="store_true", help="Skip the post-training SHAP-style explanation report.")
    parser.add_argument("--exclude-macro", action="store_true", help="Train without macro feature columns.")
    return parser.parse_args()


def run_step(*args: str) -> None:
    command = [sys.executable, *args]
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> None:
    args = parse_args()
    if not args.skip_fetch:
        run_step("scripts/modeling/fetch_training_data.py", "--years", str(args.years))
    run_step("scripts/modeling/build_training_dataset.py", "--output-name", args.dataset_name)
    if not args.skip_train:
        train_args = ["scripts/modeling/train_xgboost_model.py", "--dataset", args.dataset_name, "--model-name", args.model_name]
        if args.exclude_macro:
            train_args.append("--exclude-macro")
        run_step(*train_args)
        if not args.skip_backtest:
            run_step(
                "scripts/modeling/backtest_model.py",
                "--dataset",
                args.dataset_name,
                "--predictions",
                f"{args.model_name}_test_predictions.csv",
                "--output-name",
                f"{args.model_name}_backtest",
            )
        if not args.skip_explain:
            run_step("scripts/modeling/explain_model.py", "--dataset", args.dataset_name, "--model-name", args.model_name)


if __name__ == "__main__":
    main()
