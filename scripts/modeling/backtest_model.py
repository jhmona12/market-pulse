from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np
import pandas as pd

from common import FEATURES_DIR, REPORTS_DIR, ROOT, write_json


TARGET_COLUMN = "label_outperform_spy_14d"


@dataclass(frozen=True)
class ScoreSpec:
    name: str
    column: str
    descending: bool = True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest model predictions against simple S&P 500 momentum baselines.")
    parser.add_argument("--predictions", default="xgboost_spy14_test_predictions.csv", help="Prediction CSV inside data/modeling/reports.")
    parser.add_argument("--dataset", default="training_dataset.csv.gz", help="Feature dataset inside data/modeling/features.")
    parser.add_argument("--output-name", default="xgboost_spy14_backtest", help="Base filename for backtest outputs.")
    parser.add_argument("--horizon-days", type=int, default=14, help="Forward return horizon used by the label.")
    parser.add_argument("--cost-bps", type=float, default=15.0, help="Round-trip trading cost deducted from selected long baskets.")
    parser.add_argument("--bucket-fraction", type=float, default=0.1, help="Top and bottom bucket size.")
    return parser.parse_args()


def load_backtest_frame(args: argparse.Namespace) -> pd.DataFrame:
    predictions = pd.read_csv(REPORTS_DIR / args.predictions)
    predictions["date"] = pd.to_datetime(predictions["date"])

    dataset = pd.read_csv(FEATURES_DIR / args.dataset, compression="gzip")
    dataset["date"] = pd.to_datetime(dataset["date"])
    keep_columns = [
        column
        for column in [
            "date",
            "symbol",
            "forward_return_14d",
            "spy_forward_return_14d",
            "ret_20d",
            "ret_60d",
            "rel_ret_20d_vs_spy",
            "rel_ret_60d_vs_spy",
            "ret_20d_pct_rank",
            "ret_60d_pct_rank",
            "ret_20d_sector_pct_rank",
            "ret_60d_sector_pct_rank",
            "ret_20d_minus_sector_median",
            "ret_60d_minus_sector_median",
            "rel_ret_20d_vs_sector_etf",
            "rel_ret_60d_vs_sector_etf",
            "technical_composite_score",
            "price_vs_sma_200",
            "distance_to_52w_high",
            "volume_ratio_20",
        ]
        if column in dataset.columns
    ]
    frame = predictions.merge(dataset[keep_columns], on=["date", "symbol"], how="left", suffixes=("", "_feature"))
    return frame.sort_values(["date", "symbol"]).reset_index(drop=True)


def available_score_specs(frame: pd.DataFrame) -> list[ScoreSpec]:
    candidates = [
        ScoreSpec("xgboost", "predicted_probability"),
        ScoreSpec("xgboost_inverse", "predicted_probability", descending=False),
        ScoreSpec("momentum_60d", "ret_60d"),
        ScoreSpec("momentum_20d", "ret_20d"),
        ScoreSpec("relative_strength_60d_vs_spy", "rel_ret_60d_vs_spy"),
        ScoreSpec("sector_neutral_60d_rank", "ret_60d_sector_pct_rank"),
        ScoreSpec("sector_neutral_60d_spread", "ret_60d_minus_sector_median"),
        ScoreSpec("relative_strength_60d_vs_sector_etf", "rel_ret_60d_vs_sector_etf"),
        ScoreSpec("technical_composite", "technical_composite_score"),
    ]
    return [spec for spec in candidates if spec.column in frame.columns and frame[spec.column].notna().any()]


def max_drawdown(returns: pd.Series) -> float:
    cumulative = (1 + returns.fillna(0)).cumprod()
    running_max = cumulative.cummax()
    drawdown = cumulative / running_max - 1
    return float(drawdown.min())


def selected_symbol_turnover(daily_symbols: list[set[str]]) -> float:
    turnovers: list[float] = []
    previous: set[str] | None = None
    for symbols in daily_symbols:
        if previous is None or not previous:
            previous = symbols
            continue
        overlap = len(previous & symbols)
        turnovers.append(1 - overlap / max(1, len(symbols)))
        previous = symbols
    return float(np.mean(turnovers)) if turnovers else float("nan")


def evaluate_score(frame: pd.DataFrame, spec: ScoreSpec, bucket_fraction: float, cost_bps: float, horizon_days: int) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    cost = cost_bps / 10000
    daily_rows = []
    selected_sets: list[set[str]] = []

    for date, group in frame.dropna(subset=[spec.column, "excess_return_14d", TARGET_COLUMN]).groupby("date", sort=True):
        ranked = group.sort_values(spec.column, ascending=not spec.descending)
        bucket_count = max(1, int(np.ceil(len(ranked) * bucket_fraction)))
        top = ranked.head(bucket_count)
        bottom = ranked.tail(bucket_count)
        selected_sets.append(set(top["symbol"].astype(str)))
        daily_rows.append(
            {
                "date": date,
                "score": spec.name,
                "universe_count": int(len(ranked)),
                "selected_count": int(len(top)),
                "top_avg_excess_return_14d": float(top["excess_return_14d"].mean()),
                "top_avg_cost_adjusted_excess_return_14d": float(top["excess_return_14d"].mean() - cost),
                "top_hit_rate": float(top[TARGET_COLUMN].mean()),
                "bottom_avg_excess_return_14d": float(bottom["excess_return_14d"].mean()),
                "bottom_hit_rate": float(bottom[TARGET_COLUMN].mean()),
                "top_minus_bottom_excess_return_14d": float(top["excess_return_14d"].mean() - bottom["excess_return_14d"].mean()),
                "top_symbols": ",".join(top["symbol"].astype(str).tolist()),
            }
        )

    daily = pd.DataFrame(daily_rows)
    if daily.empty:
        return {"score": spec.name, "error": "No rows to evaluate."}, daily, pd.DataFrame()

    daily_equivalent = (1 + daily["top_avg_cost_adjusted_excess_return_14d"]).clip(lower=0.01) ** (1 / horizon_days) - 1
    spread_daily_equivalent = (1 + daily["top_minus_bottom_excess_return_14d"]).clip(lower=0.01) ** (1 / horizon_days) - 1

    bucket_rows = []
    for date, group in frame.dropna(subset=[spec.column, "excess_return_14d", TARGET_COLUMN]).groupby("date", sort=True):
        ranked = group.sort_values(spec.column, ascending=True)
        bucket_count = min(10, len(ranked))
        ranked = ranked.assign(bucket=pd.qcut(ranked[spec.column].rank(method="first"), bucket_count, labels=False) + 1)
        if not spec.descending:
            ranked["bucket"] = bucket_count + 1 - ranked["bucket"]
        for bucket, bucket_frame in ranked.groupby("bucket"):
            bucket_rows.append(
                {
                    "date": date,
                    "score": spec.name,
                    "bucket": int(bucket),
                    "avg_excess_return_14d": float(bucket_frame["excess_return_14d"].mean()),
                    "hit_rate": float(bucket_frame[TARGET_COLUMN].mean()),
                    "count": int(len(bucket_frame)),
                }
            )
    buckets = pd.DataFrame(bucket_rows)
    bucket_summary = (
        buckets.groupby(["score", "bucket"])
        .agg(
            avg_excess_return_14d=("avg_excess_return_14d", "mean"),
            hit_rate=("hit_rate", "mean"),
            avg_count=("count", "mean"),
        )
        .reset_index()
        .to_dict(orient="records")
    )

    summary = {
        "score": spec.name,
        "score_column": spec.column,
        "dates": int(daily["date"].nunique()),
        "avg_universe_count": float(daily["universe_count"].mean()),
        "avg_selected_count": float(daily["selected_count"].mean()),
        "avg_top_excess_return_14d": float(daily["top_avg_excess_return_14d"].mean()),
        "median_top_excess_return_14d": float(daily["top_avg_excess_return_14d"].median()),
        "avg_top_cost_adjusted_excess_return_14d": float(daily["top_avg_cost_adjusted_excess_return_14d"].mean()),
        "positive_top_period_rate": float((daily["top_avg_excess_return_14d"] > 0).mean()),
        "avg_top_hit_rate": float(daily["top_hit_rate"].mean()),
        "avg_bottom_excess_return_14d": float(daily["bottom_avg_excess_return_14d"].mean()),
        "avg_top_minus_bottom_excess_return_14d": float(daily["top_minus_bottom_excess_return_14d"].mean()),
        "avg_turnover": selected_symbol_turnover(selected_sets),
        "approx_cumulative_top_excess_return": float((1 + daily_equivalent).prod() - 1),
        "approx_cumulative_spread_return": float((1 + spread_daily_equivalent).prod() - 1),
        "approx_max_drawdown_top_excess": max_drawdown(daily_equivalent),
        "bucket_summary": bucket_summary,
    }
    return summary, daily, buckets


def main() -> None:
    args = parse_args()
    frame = load_backtest_frame(args)
    score_specs = available_score_specs(frame)
    if not score_specs:
        raise SystemExit("No score columns found for backtesting.")

    summaries = []
    daily_frames = []
    bucket_frames = []
    for spec in score_specs:
        summary, daily, buckets = evaluate_score(frame, spec, args.bucket_fraction, args.cost_bps, args.horizon_days)
        summaries.append(summary)
        if not daily.empty:
            daily_frames.append(daily)
        if not buckets.empty:
            bucket_frames.append(buckets)

    summary_payload = {
        "generated_at": pd.Timestamp.now("UTC").isoformat(),
        "predictions": str((REPORTS_DIR / args.predictions).relative_to(ROOT)),
        "dataset": str((FEATURES_DIR / args.dataset).relative_to(ROOT)),
        "horizon_days": args.horizon_days,
        "cost_bps": args.cost_bps,
        "bucket_fraction": args.bucket_fraction,
        "notes": "Daily returns are approximate because 14-day forward labels overlap across entry dates.",
        "scores": sorted(summaries, key=lambda item: item.get("avg_top_cost_adjusted_excess_return_14d", -999), reverse=True),
    }
    write_json(summary_payload, REPORTS_DIR / f"{args.output_name}_summary.json")

    if daily_frames:
        pd.concat(daily_frames, ignore_index=True).to_csv(REPORTS_DIR / f"{args.output_name}_daily.csv", index=False)
    if bucket_frames:
        pd.concat(bucket_frames, ignore_index=True).to_csv(REPORTS_DIR / f"{args.output_name}_buckets.csv", index=False)

    print(f"Wrote backtest summary to {(REPORTS_DIR / f'{args.output_name}_summary.json').relative_to(ROOT)}")
    for item in summary_payload["scores"]:
        print(
            f"{item['score']}: top 14d excess {item['avg_top_excess_return_14d']:.4f}, "
            f"hit rate {item['avg_top_hit_rate']:.3f}, spread {item['avg_top_minus_bottom_excess_return_14d']:.4f}"
        )


if __name__ == "__main__":
    main()
