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
    parser.add_argument("--skip-train", action="store_true", help="Fetch data and build dataset without training.")
    return parser.parse_args()


def run_step(*args: str) -> None:
    command = [sys.executable, *args]
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> None:
    args = parse_args()
    run_step("scripts/modeling/fetch_training_data.py", "--years", str(args.years))
    run_step("scripts/modeling/build_training_dataset.py", "--output-name", args.dataset_name)
    if not args.skip_train:
        run_step("scripts/modeling/train_xgboost_model.py", "--dataset", args.dataset_name, "--model-name", args.model_name)


if __name__ == "__main__":
    main()
