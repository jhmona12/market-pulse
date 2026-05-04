from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np
import pandas as pd

from common import FEATURES_DIR, REPORTS_DIR, ROOT, write_json
from schema import LEGACY_TARGET_COLUMN, RANK_RETURN_COLUMN, SECTOR_POSITIVE_TARGET_COLUMN


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
    parser.add_argument("--return-column", default=None, help="Return column to evaluate. Defaults to sector-neutral after-cost return when available.")
    parser.add_argument("--target-column", default=None, help="Binary hit-rate column. Defaults to sector-neutral positive label when available.")
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
            "excess_return_14d",
            LEGACY_TARGET_COLUMN,
            "forward_return_14d_next_close",
            "sector_forward_return_14d_next_close",
            "sector_neutral_forward_return_14d",
            RANK_RETURN_COLUMN,
            SECTOR_POSITIVE_TARGET_COLUMN,
            "label_sector_neutral_hurdle_14d",
            "relevance_grade_sector_neutral_14d",
            "candidate_momentum_setup",
            "meta_label_momentum_success",
            "ret_20d",
            "ret_60d",
            "ret_120d",
            "momentum_126d_skip_10",
            "momentum_252d_skip_20",
            "ret_20d_vol_adj",
            "ret_60d_vol_adj",
            "rel_ret_20d_vs_spy",
            "rel_ret_60d_vs_spy",
            "idiosyncratic_ret_20d",
            "idiosyncratic_ret_60d",
            "ret_20d_pct_rank",
            "ret_60d_pct_rank",
            "ret_120d_pct_rank",
            "momentum_126d_skip_10_pct_rank",
            "momentum_252d_skip_20_pct_rank",
            "ret_20d_vol_adj_pct_rank",
            "ret_60d_vol_adj_pct_rank",
            "ret_20d_sector_pct_rank",
            "ret_60d_sector_pct_rank",
            "ret_120d_sector_pct_rank",
            "momentum_126d_skip_10_sector_pct_rank",
            "momentum_252d_skip_20_sector_pct_rank",
            "ret_20d_vol_adj_sector_pct_rank",
            "ret_60d_vol_adj_sector_pct_rank",
            "ret_20d_minus_sector_median",
            "ret_60d_minus_sector_median",
            "ret_120d_minus_sector_median",
            "momentum_126d_skip_10_minus_sector_median",
            "momentum_252d_skip_20_minus_sector_median",
            "ret_20d_vol_adj_minus_sector_median",
            "ret_60d_vol_adj_minus_sector_median",
            "rel_ret_20d_vs_sector_etf",
            "rel_ret_60d_vs_sector_etf",
            "rel_ret_120d_vs_sector_etf",
            "rel_momentum_126d_vs_sector_etf",
            "rel_momentum_252d_vs_sector_etf",
            "technical_composite_score",
            "price_vs_sma_200",
            "distance_to_52w_high",
            "log_dollar_volume_20d",
            "amihud_20d",
            "volume_ratio_20",
        ]
        if column in dataset.columns
    ]
    frame = predictions.merge(dataset[keep_columns], on=["date", "symbol"], how="left", suffixes=("", "_feature"))
    for column in list(frame.columns):
        if column.endswith("_feature"):
            base = column.removesuffix("_feature")
            if base not in frame.columns:
                frame[base] = frame[column]
            else:
                frame[base] = frame[base].combine_first(frame[column])
            frame = frame.drop(columns=[column])
    return frame.sort_values(["date", "symbol"]).reset_index(drop=True)


def available_score_specs(frame: pd.DataFrame) -> list[ScoreSpec]:
    candidates = []
    if "predicted_rank_score" in frame.columns and frame["predicted_rank_score"].notna().any():
        candidates.extend(
            [
                ScoreSpec("rank_model", "predicted_rank_score"),
                ScoreSpec("rank_model_inverse", "predicted_rank_score", descending=False),
            ]
        )
    elif "predicted_probability" in frame.columns and frame["predicted_probability"].notna().any():
        candidates.extend(
            [
                ScoreSpec("xgboost", "predicted_probability"),
                ScoreSpec("xgboost_inverse", "predicted_probability", descending=False),
            ]
        )
    candidates.extend(
        [
            ScoreSpec("momentum_60d", "ret_60d"),
            ScoreSpec("momentum_20d", "ret_20d"),
            ScoreSpec("momentum_120d", "ret_120d"),
            ScoreSpec("momentum_126d_skip_10", "momentum_126d_skip_10"),
            ScoreSpec("momentum_252d_skip_20", "momentum_252d_skip_20"),
            ScoreSpec("risk_adjusted_momentum_60d", "ret_60d_vol_adj"),
            ScoreSpec("relative_strength_60d_vs_spy", "rel_ret_60d_vs_spy"),
            ScoreSpec("idiosyncratic_momentum_60d", "idiosyncratic_ret_60d"),
            ScoreSpec("sector_neutral_60d_rank", "ret_60d_sector_pct_rank"),
            ScoreSpec("sector_neutral_120d_rank", "ret_120d_sector_pct_rank"),
            ScoreSpec("sector_neutral_skip_momentum_rank", "momentum_126d_skip_10_sector_pct_rank"),
            ScoreSpec("sector_neutral_60d_spread", "ret_60d_minus_sector_median"),
            ScoreSpec("relative_strength_60d_vs_sector_etf", "rel_ret_60d_vs_sector_etf"),
            ScoreSpec("technical_composite", "technical_composite_score"),
        ]
    )
    return [spec for spec in candidates if spec.column in frame.columns and frame[spec.column].notna().any()]


def choose_return_column(frame: pd.DataFrame, requested: str | None) -> str:
    if requested:
        return requested
    if RANK_RETURN_COLUMN in frame.columns:
        return RANK_RETURN_COLUMN
    return "excess_return_14d"


def choose_target_column(frame: pd.DataFrame, requested: str | None) -> str:
    if requested:
        return requested
    if SECTOR_POSITIVE_TARGET_COLUMN in frame.columns:
        return SECTOR_POSITIVE_TARGET_COLUMN
    return LEGACY_TARGET_COLUMN


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


def symbols_from_csv(value: str) -> set[str]:
    if not isinstance(value, str) or not value:
        return set()
    return {symbol for symbol in value.split(",") if symbol}


def non_overlapping_offset_summary(daily: pd.DataFrame, horizon_days: int) -> dict:
    ordered = daily.sort_values("date").reset_index(drop=True)
    if ordered.empty or horizon_days <= 1:
        return {}

    offset_rows = []
    for offset in range(min(horizon_days, len(ordered))):
        sample = ordered.iloc[offset::horizon_days].copy()
        if sample.empty:
            continue
        top_returns = sample["top_avg_cost_adjusted_return_14d"]
        spread_returns = sample["top_minus_bottom_return_14d"]
        selected_sets = [symbols_from_csv(value) for value in sample["top_symbols"].tolist()]
        offset_rows.append(
            {
                "offset": offset,
                "periods": int(len(sample)),
                "start_date": sample["date"].min().date().isoformat(),
                "end_date": sample["date"].max().date().isoformat(),
                "avg_top_return_14d": float(top_returns.mean()),
                "median_top_return_14d": float(top_returns.median()),
                "positive_period_rate": float((top_returns > 0).mean()),
                "avg_top_hit_rate": float(sample["top_hit_rate"].mean()),
                "avg_top_minus_bottom_return_14d": float(spread_returns.mean()),
                "cumulative_top_return": float((1 + top_returns).prod() - 1),
                "cumulative_spread_return": float((1 + spread_returns).prod() - 1),
                "max_drawdown_top_return": max_drawdown(top_returns),
                "avg_turnover": selected_symbol_turnover(selected_sets),
            }
        )

    offsets = pd.DataFrame(offset_rows)
    return {
        "offset_count": int(len(offsets)),
        "avg_periods_per_offset": float(offsets["periods"].mean()),
        "mean_top_return_14d": float(offsets["avg_top_return_14d"].mean()),
        "median_top_return_14d": float(offsets["avg_top_return_14d"].median()),
        "min_top_return_14d": float(offsets["avg_top_return_14d"].min()),
        "max_top_return_14d": float(offsets["avg_top_return_14d"].max()),
        "mean_top_hit_rate": float(offsets["avg_top_hit_rate"].mean()),
        "mean_top_minus_bottom_return_14d": float(offsets["avg_top_minus_bottom_return_14d"].mean()),
        "mean_cumulative_top_return": float(offsets["cumulative_top_return"].mean()),
        "min_cumulative_top_return": float(offsets["cumulative_top_return"].min()),
        "max_cumulative_top_return": float(offsets["cumulative_top_return"].max()),
        "worst_offset_max_drawdown_top_return": float(offsets["max_drawdown_top_return"].min()),
        "offsets": offset_rows,
    }


def evaluate_score(
    frame: pd.DataFrame,
    spec: ScoreSpec,
    bucket_fraction: float,
    cost_bps: float,
    horizon_days: int,
    return_column: str,
    target_column: str,
) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    cost = cost_bps / 10000
    cost_adjustment = 0.0 if "after_cost" in return_column else cost
    daily_rows = []
    selected_sets: list[set[str]] = []

    for date, group in frame.dropna(subset=[spec.column, return_column, target_column]).groupby("date", sort=True):
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
                "top_avg_return_14d": float(top[return_column].mean()),
                "top_avg_cost_adjusted_return_14d": float(top[return_column].mean() - cost_adjustment),
                "top_hit_rate": float(top[target_column].mean()),
                "bottom_avg_return_14d": float(bottom[return_column].mean()),
                "bottom_hit_rate": float(bottom[target_column].mean()),
                "top_minus_bottom_return_14d": float(top[return_column].mean() - bottom[return_column].mean()),
                "top_symbols": ",".join(top["symbol"].astype(str).tolist()),
            }
        )

    daily = pd.DataFrame(daily_rows)
    if daily.empty:
        return {"score": spec.name, "error": "No rows to evaluate."}, daily, pd.DataFrame()

    daily_equivalent = (1 + daily["top_avg_cost_adjusted_return_14d"]).clip(lower=0.01) ** (1 / horizon_days) - 1
    spread_daily_equivalent = (1 + daily["top_minus_bottom_return_14d"]).clip(lower=0.01) ** (1 / horizon_days) - 1

    bucket_rows = []
    for date, group in frame.dropna(subset=[spec.column, return_column, target_column]).groupby("date", sort=True):
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
                    "avg_return_14d": float(bucket_frame[return_column].mean()),
                    "hit_rate": float(bucket_frame[target_column].mean()),
                    "count": int(len(bucket_frame)),
                }
            )
    buckets = pd.DataFrame(bucket_rows)
    bucket_summary = (
        buckets.groupby(["score", "bucket"])
        .agg(
            avg_return_14d=("avg_return_14d", "mean"),
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
        "avg_top_return_14d": float(daily["top_avg_return_14d"].mean()),
        "median_top_return_14d": float(daily["top_avg_return_14d"].median()),
        "avg_top_cost_adjusted_return_14d": float(daily["top_avg_cost_adjusted_return_14d"].mean()),
        "positive_top_period_rate": float((daily["top_avg_return_14d"] > 0).mean()),
        "avg_top_hit_rate": float(daily["top_hit_rate"].mean()),
        "avg_bottom_return_14d": float(daily["bottom_avg_return_14d"].mean()),
        "avg_top_minus_bottom_return_14d": float(daily["top_minus_bottom_return_14d"].mean()),
        "avg_turnover": selected_symbol_turnover(selected_sets),
        "approx_cumulative_top_return": float((1 + daily_equivalent).prod() - 1),
        "approx_cumulative_spread_return": float((1 + spread_daily_equivalent).prod() - 1),
        "approx_max_drawdown_top_return": max_drawdown(daily_equivalent),
        "non_overlapping_offsets": non_overlapping_offset_summary(daily, horizon_days),
        "bucket_summary": bucket_summary,
    }
    return summary, daily, buckets


def main() -> None:
    args = parse_args()
    frame = load_backtest_frame(args)
    return_column = choose_return_column(frame, args.return_column)
    target_column = choose_target_column(frame, args.target_column)
    score_specs = available_score_specs(frame)
    if not score_specs:
        raise SystemExit("No score columns found for backtesting.")

    summaries = []
    daily_frames = []
    bucket_frames = []
    for spec in score_specs:
        summary, daily, buckets = evaluate_score(
            frame,
            spec,
            args.bucket_fraction,
            args.cost_bps,
            args.horizon_days,
            return_column,
            target_column,
        )
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
        "return_column": return_column,
        "target_column": target_column,
        "notes": (
            "Daily rows use overlapping forward labels. non_overlapping_offsets evaluates every possible "
            "14-trading-day rebalance offset separately to reduce overlap bias."
        ),
        "scores": sorted(summaries, key=lambda item: item.get("avg_top_cost_adjusted_return_14d", -999), reverse=True),
    }
    write_json(summary_payload, REPORTS_DIR / f"{args.output_name}_summary.json")

    if daily_frames:
        pd.concat(daily_frames, ignore_index=True).to_csv(REPORTS_DIR / f"{args.output_name}_daily.csv", index=False)
    if bucket_frames:
        pd.concat(bucket_frames, ignore_index=True).to_csv(REPORTS_DIR / f"{args.output_name}_buckets.csv", index=False)

    print(f"Wrote backtest summary to {(REPORTS_DIR / f'{args.output_name}_summary.json').relative_to(ROOT)}")
    for item in summary_payload["scores"]:
        print(
            f"{item['score']}: top 14d return {item['avg_top_return_14d']:.4f}, "
            f"hit rate {item['avg_top_hit_rate']:.3f}, spread {item['avg_top_minus_bottom_return_14d']:.4f}"
        )


if __name__ == "__main__":
    main()
