from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from common import REPORTS_DIR, ROOT, write_json


FEATURE_DESCRIPTIONS = {
    "amihud_20d": "20-day Amihud-style illiquidity estimate; higher values mean price moved more per dollar traded",
    "volatility_60d_minus_sector_median": "60-day volatility compared with the stock's sector median",
    "volatility_60d_pct_rank": "60-day volatility rank across the current stock universe",
    "downside_volatility_20d_pct_rank": "20-day downside-volatility rank across the current stock universe",
    "amihud_20d_pct_rank": "20-day illiquidity rank across the current stock universe",
    "log_dollar_volume_20d_pct_rank": "20-day dollar-volume rank across the current stock universe",
    "distance_to_52w_low_pct_rank": "ranked distance from each stock's 52-week low",
    "distance_to_52w_low_sector_pct_rank": "distance from each stock's 52-week low ranked within its sector",
    "distance_to_52w_low_minus_sector_median": "distance from the 52-week low compared with the stock's sector median",
    "downside_volatility_60d_sector_pct_rank": "60-day downside-volatility rank within the stock's sector",
    "downside_volatility_60d_minus_sector_median": "60-day downside volatility compared with the stock's sector median",
    "breadth_ret_60d_median": "median 60-day return across the S&P 500 universe",
    "breadth_ret_120d_median": "median 120-day return across the S&P 500 universe",
    "breadth_volatility_60d_median": "median 60-day volatility across the S&P 500 universe",
    "sma_50_vs_200": "50-day moving average compared with the 200-day moving average",
    "rel_momentum_252d_vs_sector_etf": "12-month skip-window momentum versus the sector ETF",
    "idiosyncratic_ret_60d": "60-day stock return after adjusting for estimated SPY beta",
    "breadth_volatility_20d_median": "median 20-day volatility across the S&P 500 universe",
    "sma_20_vs_50": "20-day moving average compared with the 50-day moving average",
    "momentum_252d_skip_20_sector_pct_rank": "12-month skip-window momentum rank within the stock's sector",
    "downside_volatility_60d_pct_rank": "60-day downside-volatility rank across the current stock universe",
    "spy_volatility_20d": "20-day SPY volatility backdrop",
    "spy_volatility_60d": "60-day SPY volatility backdrop",
    "rel_volatility_60d_vs_spy_pct_rank": "60-day volatility versus SPY rank across the stock universe",
    "sector_momentum_252d_skip_20": "sector ETF 12-month momentum excluding the most recent 20 trading days",
    "momentum_252d_skip_20_pct_rank": "stock 12-month momentum rank across the universe, excluding the most recent 20 trading days",
    "distance_to_52w_high_minus_sector_median": "distance from the 52-week high compared with sector median",
    "distance_to_52w_high": "distance from the stock's 52-week high",
    "volatility_60d_vs_sector_etf": "60-day volatility compared with the sector ETF",
    "distance_to_52w_low": "distance from the stock's 52-week low",
    "rel_ret_60d_vs_sector_etf": "60-day return versus the sector ETF",
    "rel_volatility_60d_vs_spy": "60-day volatility compared with SPY",
    "distance_to_52w_high_pct_rank": "distance from the 52-week high rank across the stock universe",
    "log_dollar_volume_20d_sector_pct_rank": "20-day dollar-volume rank within the stock's sector",
    "downside_volatility_60d": "60-day downside volatility",
    "sector_volatility_60d": "sector ETF 60-day volatility backdrop",
    "ret_120d_minus_sector_median": "120-day stock return compared with the stock's sector median",
    "sector_price_vs_sma_200": "sector ETF price compared with its 200-day moving average",
    "beta_60d_minus_sector_median": "60-day beta compared with the stock's sector median",
    "momentum_252d_skip_20": "stock 12-month momentum excluding the most recent 20 trading days",
    "momentum_252d_skip_20_minus_sector_median": "12-month skip-window momentum versus sector median",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a compact explainability artifact from a SHAP summary report.")
    parser.add_argument(
        "--source-model",
        default="xgboost_rank_sector14_feature_v2_tuned_holdout",
        help="Model name for the SHAP summary inside data/modeling/reports.",
    )
    parser.add_argument(
        "--output",
        default="models/rank/xgboost_rank_sector14_tuned_explainability.json",
        help="Committed compact explainability artifact path.",
    )
    parser.add_argument("--top-n", type=int, default=15, help="Number of SHAP rows to export.")
    return parser.parse_args()


def describe_feature(feature: str) -> str:
    return FEATURE_DESCRIPTIONS.get(feature, feature.replace("_", " "))


def main() -> None:
    args = parse_args()
    source_path = REPORTS_DIR / f"{args.source_model}_shap_summary.json"
    with source_path.open(encoding="utf-8") as handle:
        summary = json.load(handle)

    top_features = []
    for row in summary.get("top_features", [])[: args.top_n]:
        top_features.append(
            {
                "feature": row["feature"],
                "description": describe_feature(row["feature"]),
                "meanAbsShap": row["mean_abs_shap"],
                "meanShap": row["mean_shap"],
                "positiveShare": row["positive_share"],
                "p05Shap": row["p05_shap"],
                "p50Shap": row["p50_shap"],
                "p95Shap": row["p95_shap"],
            }
        )

    payload = {
        "generatedAt": pd.Timestamp.now("UTC").isoformat(),
        "sourceModel": args.source_model,
        "sourceReport": str(source_path.relative_to(ROOT)),
        "modelPath": summary.get("model_path"),
        "rowsExplained": summary.get("rows_explained"),
        "featureCount": summary.get("feature_count"),
        "method": "XGBoost pred_contribs SHAP-style margin contributions on holdout predictions.",
        "notes": (
            "Mean absolute SHAP ranks the features that most changed model scores in the holdout sample. "
            "Positive or negative mean values should be read as directional tendencies, not as standalone trading rules."
        ),
        "topFeatures": top_features,
    }
    output_path = ROOT / args.output
    write_json(payload, output_path)
    print(f"Wrote compact explainability artifact to {output_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
