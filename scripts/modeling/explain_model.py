from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from common import FEATURES_DIR, MODELS_DIR, REPORTS_DIR, ROOT, write_json
from schema import NON_FEATURE_COLUMNS

try:
    import xgboost as xgb
except ModuleNotFoundError as error:  # pragma: no cover - handled at runtime
    raise SystemExit(
        "xgboost is not installed in the current Python environment. "
        "Run scripts/modeling/setup_training_env.sh before explaining a model."
    ) from error


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate XGBoost SHAP-style contribution summaries for model features.")
    parser.add_argument("--model-name", default="xgboost_spy14", help="Base model name inside data/modeling/models.")
    parser.add_argument(
        "--model-path",
        default=None,
        help="Optional explicit model JSON path relative to the repo root. Defaults to data/modeling/models/<model-name>.json.",
    )
    parser.add_argument("--dataset", default="training_dataset.csv.gz", help="Feature dataset inside data/modeling/features.")
    parser.add_argument("--predictions", default=None, help="Prediction CSV inside data/modeling/reports. Defaults to <model-name>_test_predictions.csv.")
    parser.add_argument("--top-n", type=int, default=25, help="Number of features to print.")
    return parser.parse_args()


def load_frame(args: argparse.Namespace, feature_names: list[str]) -> pd.DataFrame:
    prediction_name = args.predictions or f"{args.model_name}_test_predictions.csv"
    predictions = pd.read_csv(REPORTS_DIR / prediction_name, usecols=["date", "symbol", "predicted_probability"])
    predictions["date"] = pd.to_datetime(predictions["date"])

    needed_columns = ["date", "symbol", *feature_names]
    dataset = pd.read_csv(FEATURES_DIR / args.dataset, compression="gzip", usecols=needed_columns)
    dataset["date"] = pd.to_datetime(dataset["date"])

    return predictions.merge(dataset, on=["date", "symbol"], how="inner").sort_values(["date", "symbol"]).reset_index(drop=True)


def main() -> None:
    args = parse_args()
    model_path = ROOT / args.model_path if args.model_path else MODELS_DIR / f"{args.model_name}.json"
    booster = xgb.Booster()
    booster.load_model(str(model_path))

    feature_names = booster.feature_names
    if not feature_names:
        dataset_columns = pd.read_csv(FEATURES_DIR / args.dataset, compression="gzip", nrows=0).columns.tolist()
        feature_names = [column for column in dataset_columns if column not in NON_FEATURE_COLUMNS]

    frame = load_frame(args, feature_names)
    dmatrix = xgb.DMatrix(frame[feature_names].to_numpy(dtype=float), feature_names=feature_names)
    contributions = booster.predict(dmatrix, pred_contribs=True)
    feature_contributions = contributions[:, :-1]
    bias = contributions[:, -1]

    summary_rows = []
    for index, feature in enumerate(feature_names):
        values = feature_contributions[:, index]
        summary_rows.append(
            {
                "feature": feature,
                "mean_abs_shap": float(np.mean(np.abs(values))),
                "mean_shap": float(np.mean(values)),
                "positive_share": float(np.mean(values > 0)),
                "p05_shap": float(np.percentile(values, 5)),
                "p50_shap": float(np.percentile(values, 50)),
                "p95_shap": float(np.percentile(values, 95)),
            }
        )

    summary = pd.DataFrame(summary_rows).sort_values("mean_abs_shap", ascending=False).reset_index(drop=True)
    summary_path = REPORTS_DIR / f"{args.model_name}_shap_summary.csv"
    summary.to_csv(summary_path, index=False)

    payload = {
        "generated_at": pd.Timestamp.now("UTC").isoformat(),
        "model_path": str(model_path.relative_to(ROOT)),
        "dataset": str((FEATURES_DIR / args.dataset).relative_to(ROOT)),
        "rows_explained": int(len(frame)),
        "feature_count": int(len(feature_names)),
        "mean_bias_margin": float(np.mean(bias)),
        "notes": (
            "XGBoost pred_contribs values are SHAP-style margin contributions; "
            "positive values push the model's rank score higher."
        ),
        "top_features": summary.head(args.top_n).to_dict(orient="records"),
    }
    write_json(payload, REPORTS_DIR / f"{args.model_name}_shap_summary.json")

    print(f"Wrote SHAP summary to {summary_path.relative_to(ROOT)}")
    for row in payload["top_features"]:
        print(
            f"{row['feature']}: mean_abs={row['mean_abs_shap']:.5f}, "
            f"mean={row['mean_shap']:.5f}, p95={row['p95_shap']:.5f}, p05={row['p05_shap']:.5f}"
        )


if __name__ == "__main__":
    main()
