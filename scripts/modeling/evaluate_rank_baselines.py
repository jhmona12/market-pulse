from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np
import pandas as pd

from common import FEATURES_DIR, REPORTS_DIR, ROOT, write_json
from schema import LONG_RANK_RETURN_COLUMN, LONG_RANK_TARGET_COLUMN


@dataclass(frozen=True)
class Strategy:
    key: str
    label: str
    description: str
    score_column: str


BASELINE_INPUT_COLUMNS = {
    "volatility_60d_pct_rank",
    "volatility_60d_minus_sector_median",
    "amihud_20d_pct_rank",
    "log_dollar_volume_20d_pct_rank",
    "rel_volatility_60d_vs_spy_pct_rank",
    "momentum_252d_skip_20_pct_rank",
    "momentum_252d_skip_20_sector_pct_rank",
    "momentum_252d_skip_20_minus_sector_median",
    "rel_momentum_252d_vs_sector_etf",
    "ret_60d_vol_adj_pct_rank",
    "ret_120d_pct_rank",
    "distance_to_52w_high_pct_rank",
    "technical_composite_score",
    "sector_momentum_252d_skip_20",
}


STRATEGIES = [
    Strategy(
        key="xgboost_rank",
        label="XGBoost rank model",
        description="Research rank model trained on 156 price, liquidity, volatility, sector, and market-context features.",
        score_column="score_xgboost_rank",
    ),
    Strategy(
        key="low_volatility",
        label="Low volatility",
        description="Simple baseline that favors lower 60-day realized volatility.",
        score_column="score_low_volatility",
    ),
    Strategy(
        key="liquidity_quality",
        label="Liquidity plus low risk",
        description="Baseline that favors liquid, heavily traded stocks with lower volatility and lower illiquidity.",
        score_column="score_liquidity_quality",
    ),
    Strategy(
        key="twelve_one_momentum",
        label="12-1 month momentum",
        description="Classic momentum baseline: 12-month performance excluding the most recent month.",
        score_column="score_twelve_one_momentum",
    ),
    Strategy(
        key="sector_relative_momentum",
        label="Sector-relative momentum",
        description="Baseline that favors stocks with stronger 12-1 month momentum versus sector peers and sector ETF.",
        score_column="score_sector_relative_momentum",
    ),
    Strategy(
        key="risk_adjusted_momentum",
        label="Risk-adjusted momentum",
        description="Momentum baseline that blends 12-1 month momentum, 60-day volatility-adjusted return, and lower volatility.",
        score_column="score_risk_adjusted_momentum",
    ),
    Strategy(
        key="technical_composite",
        label="Technical composite",
        description="Existing rules-style composite built from relative return, moving-average, and momentum ranks.",
        score_column="score_technical_composite",
    ),
    Strategy(
        key="sector_momentum",
        label="Sector momentum",
        description="Baseline that ranks stocks by the 12-1 month momentum of their sector ETF.",
        score_column="score_sector_momentum",
    ),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare an XGBoost rank model with simple baseline ranking rules on the same holdout rows."
    )
    parser.add_argument(
        "--dataset",
        default="long_horizon_training_dataset.csv.gz",
        help="Feature dataset inside data/modeling/features.",
    )
    parser.add_argument(
        "--predictions",
        default="xgboost_rank_sector252_research_test_predictions.csv",
        help="Model prediction CSV inside data/modeling/reports.",
    )
    parser.add_argument(
        "--output-name",
        default="xgboost_rank_sector252_baseline_comparison",
        help="Base name for report outputs.",
    )
    parser.add_argument("--target-column", default=LONG_RANK_TARGET_COLUMN, help="Relevance-grade target column.")
    parser.add_argument("--return-column", default=LONG_RANK_RETURN_COLUMN, help="Forward return column to evaluate.")
    parser.add_argument(
        "--artifact-output",
        default="models/long-horizon/xgboost_rank_sector252_baseline_comparison.json",
        help="Small committed JSON artifact with the headline comparison. Use an empty string to skip.",
    )
    parser.add_argument("--latest-top-n", type=int, default=50, help="Rows to include in the latest-score snapshot.")
    return parser.parse_args()


def rank_by_date(frame: pd.DataFrame, column: str, ascending: bool = True) -> pd.Series:
    return frame.groupby("date")[column].rank(pct=True, ascending=ascending)


def required_columns(dataset_path) -> list[str]:
    available = set(pd.read_csv(dataset_path, compression="gzip", nrows=0).columns)
    missing = sorted(BASELINE_INPUT_COLUMNS - available)
    if missing:
        raise ValueError(f"Dataset is missing baseline input columns: {missing}")
    return ["date", "symbol", *sorted(BASELINE_INPUT_COLUMNS)]


def load_comparison_frame(args: argparse.Namespace) -> pd.DataFrame:
    dataset_path = FEATURES_DIR / args.dataset
    predictions_path = REPORTS_DIR / args.predictions

    predictions = pd.read_csv(predictions_path)
    predictions["date"] = pd.to_datetime(predictions["date"])
    predictions = predictions.rename(columns={"predicted_rank_score": "score_xgboost_rank"})

    keep_prediction_columns = [
        column
        for column in [
            "date",
            "symbol",
            "sector",
            "close",
            args.return_column,
            args.target_column,
            "score_xgboost_rank",
        ]
        if column in predictions.columns
    ]
    predictions = predictions[keep_prediction_columns].copy()

    feature_frame = pd.read_csv(dataset_path, compression="gzip", usecols=required_columns(dataset_path))
    feature_frame["date"] = pd.to_datetime(feature_frame["date"])
    frame = predictions.merge(feature_frame, on=["date", "symbol"], how="left", validate="one_to_one")
    missing_features = frame[sorted(BASELINE_INPUT_COLUMNS)].isna().sum().sum()
    if missing_features:
        raise ValueError(f"Merged comparison frame has {int(missing_features)} missing baseline feature cells.")
    return frame


def add_baseline_scores(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()

    frame["_rel_momentum_vs_sector_rank"] = rank_by_date(frame, "rel_momentum_252d_vs_sector_etf")
    frame["_momentum_minus_sector_rank"] = rank_by_date(frame, "momentum_252d_skip_20_minus_sector_median")
    frame["_sector_momentum_rank"] = rank_by_date(frame, "sector_momentum_252d_skip_20")
    frame["_technical_composite_rank"] = rank_by_date(frame, "technical_composite_score")

    frame["score_low_volatility"] = 1.0 - frame["volatility_60d_pct_rank"]
    frame["score_liquidity_quality"] = (
        frame["log_dollar_volume_20d_pct_rank"]
        + (1.0 - frame["amihud_20d_pct_rank"])
        + (1.0 - frame["volatility_60d_pct_rank"])
        + (1.0 - frame["rel_volatility_60d_vs_spy_pct_rank"])
    ) / 4.0
    frame["score_twelve_one_momentum"] = frame["momentum_252d_skip_20_pct_rank"]
    frame["score_sector_relative_momentum"] = (
        frame["momentum_252d_skip_20_sector_pct_rank"]
        + frame["_rel_momentum_vs_sector_rank"]
        + frame["_momentum_minus_sector_rank"]
    ) / 3.0
    frame["score_risk_adjusted_momentum"] = (
        frame["momentum_252d_skip_20_pct_rank"]
        + frame["ret_60d_vol_adj_pct_rank"]
        + (1.0 - frame["volatility_60d_pct_rank"])
    ) / 3.0
    frame["score_technical_composite"] = frame["_technical_composite_rank"]
    frame["score_sector_momentum"] = frame["_sector_momentum_rank"]

    for strategy in STRATEGIES:
        percentile_column = f"{strategy.key}_percentile"
        frame[percentile_column] = rank_by_date(frame, strategy.score_column)
    return frame


def cohort_dates(frame: pd.DataFrame, period: str) -> list[pd.Timestamp]:
    if period == "daily":
        return sorted(frame["date"].drop_duplicates())
    periods = frame[["date"]].drop_duplicates().copy()
    periods["period"] = periods["date"].dt.to_period(period)
    return periods.groupby("period")["date"].min().sort_values().tolist()


def decile_rows(frame: pd.DataFrame, score_column: str, return_column: str, target_column: str, dates: list[pd.Timestamp]) -> list[dict]:
    rows = []
    for date in dates:
        group = frame[frame["date"] == date].dropna(subset=[score_column, return_column, target_column])
        if group.empty:
            continue
        ranked = group.sort_values(score_column, ascending=False)
        bucket_count = max(1, int(np.ceil(len(ranked) * 0.1)))
        top = ranked.head(bucket_count)
        bottom = ranked.tail(bucket_count)
        rows.append(
            {
                "date": pd.Timestamp(date).date().isoformat(),
                "count": int(len(ranked)),
                "bucket_count": int(bucket_count),
                "top_decile_return": float(top[return_column].mean()),
                "top_decile_return_median": float(top[return_column].median()),
                "top_decile_hit_rate": float((top[return_column] > 0).mean()),
                "bottom_decile_return": float(bottom[return_column].mean()),
                "bottom_decile_return_median": float(bottom[return_column].median()),
                "top_minus_bottom_return": float(top[return_column].mean() - bottom[return_column].mean()),
                "top_minus_bottom_median_return": float(top[return_column].median() - bottom[return_column].median()),
                "top_decile_average_relevance_grade": float(top[target_column].mean()),
            }
        )
    return rows


def summarize_rows(rows: list[dict]) -> dict:
    if not rows:
        return {
            "cohort_count": 0,
            "top_decile_return_mean": None,
            "top_decile_return_median": None,
            "top_decile_hit_rate_mean": None,
            "top_minus_bottom_return_mean": None,
            "top_minus_bottom_return_median": None,
            "top_decile_average_relevance_grade_mean": None,
        }
    frame = pd.DataFrame(rows)
    return {
        "cohort_count": int(len(frame)),
        "top_decile_return_mean": float(frame["top_decile_return"].mean()),
        "top_decile_return_median": float(frame["top_decile_return"].median()),
        "top_decile_hit_rate_mean": float(frame["top_decile_hit_rate"].mean()),
        "top_minus_bottom_return_mean": float(frame["top_minus_bottom_return"].mean()),
        "top_minus_bottom_return_median": float(frame["top_minus_bottom_return"].median()),
        "top_decile_average_relevance_grade_mean": float(frame["top_decile_average_relevance_grade"].mean()),
    }


def evaluate_strategies(frame: pd.DataFrame, args: argparse.Namespace) -> tuple[pd.DataFrame, pd.DataFrame]:
    summary_rows = []
    detail_rows = []
    for strategy in STRATEGIES:
        for period_key, period_label in [
            ("daily", "Daily overlapping"),
            ("M", "Monthly first trading day"),
            ("Q", "Quarterly first trading day"),
            ("Y", "Yearly first trading day"),
        ]:
            rows = decile_rows(
                frame=frame,
                score_column=strategy.score_column,
                return_column=args.return_column,
                target_column=args.target_column,
                dates=cohort_dates(frame, period_key),
            )
            for row in rows:
                detail_rows.append(
                    {
                        "strategy": strategy.key,
                        "strategy_label": strategy.label,
                        "cohort": period_label,
                        **row,
                    }
                )
            summary_rows.append(
                {
                    "strategy": strategy.key,
                    "strategy_label": strategy.label,
                    "description": strategy.description,
                    "cohort": period_label,
                    **summarize_rows(rows),
                }
            )
    summary = pd.DataFrame(summary_rows)
    summary = summary.sort_values(["cohort", "top_decile_return_median"], ascending=[True, False])
    details = pd.DataFrame(detail_rows)
    return summary, details


def latest_snapshot(frame: pd.DataFrame, top_n: int) -> pd.DataFrame:
    latest_date = frame["date"].max()
    latest = frame[frame["date"] == latest_date].copy()
    latest["baseline_agreement_label"] = "Monitor"
    xgb_top = latest["xgboost_rank_percentile"] >= 0.9
    sector_momentum_confirmed = latest["sector_relative_momentum_percentile"] >= 0.7
    classic_momentum_confirmed = latest["twelve_one_momentum_percentile"] >= 0.7
    rules_top = latest["sector_relative_momentum_percentile"] >= 0.9
    latest.loc[xgb_top & sector_momentum_confirmed, "baseline_agreement_label"] = "Consensus Long-Horizon Candidate"
    latest.loc[
        xgb_top & ~sector_momentum_confirmed & classic_momentum_confirmed,
        "baseline_agreement_label",
    ] = "Model Candidate With Classic Momentum"
    latest.loc[xgb_top & ~sector_momentum_confirmed & ~classic_momentum_confirmed, "baseline_agreement_label"] = (
        "Model-Only Candidate"
    )
    latest.loc[~xgb_top & rules_top, "baseline_agreement_label"] = "Rules Candidate"

    output_columns = ["date", "symbol", "sector", "close", "score_xgboost_rank"]
    output_columns.extend(f"{strategy.key}_percentile" for strategy in STRATEGIES)
    output_columns.append("baseline_agreement_label")
    output = latest[output_columns].sort_values("score_xgboost_rank", ascending=False).head(top_n)
    output["date"] = output["date"].dt.date.astype(str)
    return output


def compact_artifact(summary: pd.DataFrame, details: pd.DataFrame, snapshot: pd.DataFrame, args: argparse.Namespace) -> dict:
    daily = summary[summary["cohort"] == "Daily overlapping"].copy()
    monthly = summary[summary["cohort"] == "Monthly first trading day"].copy()
    quarterly = summary[summary["cohort"] == "Quarterly first trading day"].copy()
    latest_date = snapshot["date"].iloc[0] if not snapshot.empty else None

    return {
        "generatedAt": pd.Timestamp.now("UTC").isoformat(),
        "dataset": str((FEATURES_DIR / args.dataset).relative_to(ROOT)),
        "predictions": str((REPORTS_DIR / args.predictions).relative_to(ROOT)),
        "targetColumn": args.target_column,
        "returnColumn": args.return_column,
        "notes": (
            "Daily rows are overlapping 252-trading-day outcomes. Monthly, quarterly, and yearly cohorts are "
            "first-trading-day snapshots that reduce overlap and are more useful for long-horizon interpretation."
        ),
        "dailySummary": daily.to_dict(orient="records"),
        "monthlySummary": monthly.to_dict(orient="records"),
        "quarterlySummary": quarterly.to_dict(orient="records"),
        "latestSnapshotDate": latest_date,
        "latestXgboostTop": snapshot.head(25).to_dict(orient="records"),
        "cohortDetailRows": int(len(details)),
    }


def main() -> None:
    args = parse_args()
    frame = add_baseline_scores(load_comparison_frame(args))
    summary, details = evaluate_strategies(frame, args)
    snapshot = latest_snapshot(frame, args.latest_top_n)

    summary_path = REPORTS_DIR / f"{args.output_name}_summary.csv"
    details_path = REPORTS_DIR / f"{args.output_name}_cohort_details.csv"
    snapshot_path = REPORTS_DIR / f"{args.output_name}_latest_snapshot.csv"
    report_path = REPORTS_DIR / f"{args.output_name}_report.json"

    summary.to_csv(summary_path, index=False)
    details.to_csv(details_path, index=False)
    snapshot.to_csv(snapshot_path, index=False)
    report = compact_artifact(summary, details, snapshot, args)
    report.update(
        {
            "summaryPath": str(summary_path.relative_to(ROOT)),
            "detailsPath": str(details_path.relative_to(ROOT)),
            "latestSnapshotPath": str(snapshot_path.relative_to(ROOT)),
        }
    )
    write_json(report, report_path)

    if args.artifact_output:
        write_json(compact_artifact(summary, details, snapshot, args), ROOT / args.artifact_output)

    xgb_daily = summary[(summary["strategy"] == "xgboost_rank") & (summary["cohort"] == "Daily overlapping")].iloc[0]
    xgb_monthly = summary[(summary["strategy"] == "xgboost_rank") & (summary["cohort"] == "Monthly first trading day")].iloc[0]
    print(f"Wrote baseline comparison to {summary_path.relative_to(ROOT)}")
    print(
        "XGBoost daily top-decile mean "
        f"{xgb_daily['top_decile_return_mean']:.4f}; monthly median {xgb_monthly['top_decile_return_median']:.4f}"
    )


if __name__ == "__main__":
    main()
