from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xgboost as xgb


ROOT = Path(__file__).resolve().parents[2]
MODELING_DIR = ROOT / "scripts" / "modeling"
sys.path.insert(0, str(MODELING_DIR))

from model_features import add_cross_sectional_model_features  # noqa: E402
from score_live_rank_model import (  # noqa: E402
    MARKET_STRIP_SYMBOLS,
    SECTOR_ETFS,
    build_base_feature_dataset,
    build_market_context,
    build_spy_context,
    fetch_symbol_frames,
    load_constituents,
)


DEFAULT_MODEL_NAME = "xgboost_rank_sector14_tuned"
DEFAULT_OUTPUT_DIR = ROOT / "analysis" / "model-monitoring" / "output"
DEFAULT_COST_BPS = 15.0
DEFAULT_HORIZON_DAYS = 14


@dataclass(frozen=True)
class WindowResult:
    name: str
    description: str
    warning: str | None
    start_date: str | None
    end_date: str | None
    scoring_dates: int
    rows: int
    decile_summary: pd.DataFrame
    daily_deciles: pd.DataFrame
    top_bottom_daily: pd.DataFrame
    segment_summary: pd.DataFrame
    cohort_summary: pd.DataFrame
    latest_top_decile: pd.DataFrame
    diagnostics: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run recent decile backtests for the production XGBoost rank model.",
    )
    parser.add_argument("--model-dir", default="models/rank", help="Directory containing the production model artifact.")
    parser.add_argument("--model-name", default=DEFAULT_MODEL_NAME, help="Base production model name.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR.relative_to(ROOT)), help="Output directory.")
    parser.add_argument("--years", type=int, default=3, help="Trailing years of price history to fetch.")
    parser.add_argument("--max-workers", type=int, default=8, help="Concurrent Yahoo fetch workers.")
    parser.add_argument("--max-symbols", type=int, default=0, help="Development cap for S&P 500 symbols.")
    parser.add_argument("--recent-dates", type=int, default=60, help="Completed scoring dates in the recent diagnostic window.")
    parser.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON_DAYS, help="Forward outcome horizon.")
    parser.add_argument("--cost-bps", type=float, default=DEFAULT_COST_BPS, help="Round-trip cost for sector-neutral returns.")
    parser.add_argument(
        "--strict-start-date",
        default=None,
        help="Optional strict-window start date. Defaults to the day after the model training end date.",
    )
    return parser.parse_args()


def load_model_metadata(model_dir: Path, model_name: str) -> tuple[dict[str, Any], Path]:
    metadata_path = model_dir / f"{model_name}_metadata.json"
    if not metadata_path.exists():
        raise FileNotFoundError(f"Missing model metadata: {metadata_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    model_path = ROOT / metadata.get("model_path", str(model_dir / f"{model_name}.json"))
    if not model_path.exists():
        model_path = model_dir / f"{model_name}.json"
    if not model_path.exists():
        raise FileNotFoundError(f"Missing model artifact: {model_path}")
    return metadata, model_path


def fetch_monitoring_dataset(args: argparse.Namespace, feature_columns: list[str]) -> tuple[pd.DataFrame, dict[str, Any]]:
    constituents, constituent_source = load_constituents(args.max_symbols)
    reference_symbols = set(constituents["symbol"].astype(str))
    required_symbols = list(
        dict.fromkeys(
            [
                *constituents["symbol"].astype(str).tolist(),
                "SPY",
                *SECTOR_ETFS,
                *MARKET_STRIP_SYMBOLS,
            ],
        ),
    )

    symbol_frames, failures = fetch_symbol_frames(required_symbols, args.years, args.max_workers)
    missing_context = [symbol for symbol in ["SPY", *SECTOR_ETFS] if symbol not in symbol_frames]
    if missing_context:
        raise RuntimeError(f"Missing required context histories: {missing_context}")

    reference_frames = {
        symbol: frame
        for symbol, frame in symbol_frames.items()
        if symbol in reference_symbols or symbol in {"SPY", *SECTOR_ETFS}
    }
    spy = build_spy_context(symbol_frames["SPY"])
    reference_constituents = constituents[constituents["symbol"].isin(reference_symbols)].copy()
    breadth, sector_context = build_market_context(reference_frames, reference_constituents)
    base_dataset = build_base_feature_dataset(symbol_frames, constituents, breadth, sector_context, spy)
    dataset = add_cross_sectional_model_features(base_dataset, round_trip_cost=args.cost_bps / 10000)

    spy_next = symbol_frames["SPY"][["date", "forward_return_14d_next_close"]].rename(
        columns={"forward_return_14d_next_close": "spy_forward_return_14d_next_close"},
    )
    dataset = dataset.merge(spy_next, on="date", how="left")
    dataset["excess_return_14d_next_close"] = (
        dataset["forward_return_14d_next_close"] - dataset["spy_forward_return_14d_next_close"]
    )

    missing_features = [column for column in feature_columns if column not in dataset.columns]
    if missing_features:
        raise RuntimeError(f"Monitoring feature matrix is missing model features: {missing_features}")

    latest_price_dates = {
        symbol: frame["date"].max().date().isoformat()
        for symbol, frame in symbol_frames.items()
        if frame is not None and not frame.empty
    }
    metadata = {
        "constituentSource": constituent_source,
        "requestedSymbols": len(required_symbols),
        "fetchedSymbols": len(symbol_frames),
        "failedSymbols": len(failures),
        "failures": failures,
        "latestPriceDate": max(latest_price_dates.values()) if latest_price_dates else None,
        "latestSpyPriceDate": latest_price_dates.get("SPY"),
        "symbolPriceDates": latest_price_dates,
    }
    return dataset.replace([np.inf, -np.inf], np.nan), metadata


def score_feature_rows(dataset: pd.DataFrame, model_path: Path, feature_columns: list[str]) -> pd.DataFrame:
    scored = dataset.dropna(subset=feature_columns).copy()
    booster = xgb.Booster()
    booster.load_model(str(model_path))
    matrix = xgb.DMatrix(scored[feature_columns].to_numpy(dtype=float), feature_names=feature_columns)
    scored["model_score"] = booster.predict(matrix)
    return scored.sort_values(["date", "model_score"], ascending=[True, False]).reset_index(drop=True)


def filter_completed_outcomes(scored: pd.DataFrame, return_column: str) -> pd.DataFrame:
    required = [
        return_column,
        "forward_return_14d_next_close",
        "excess_return_14d_next_close",
        "sector_neutral_forward_return_14d",
    ]
    completed = scored.dropna(subset=required).copy()
    completed = completed.sort_values(["date", "model_score"], ascending=[True, False]).reset_index(drop=True)
    completed["hit_vs_spy"] = completed["excess_return_14d_next_close"] > 0
    completed["hit_sector_neutral_after_cost"] = completed[return_column] > 0
    return completed


def score_dataset(dataset: pd.DataFrame, model_path: Path, feature_columns: list[str], return_column: str) -> pd.DataFrame:
    return filter_completed_outcomes(score_feature_rows(dataset, model_path, feature_columns), return_column)


def assign_deciles(frame: pd.DataFrame) -> pd.DataFrame:
    pieces = []
    for date_value, group in frame.groupby("date", sort=True):
        ranked = group.sort_values("model_score", ascending=True).copy()
        bucket_count = min(10, len(ranked))
        ranked["model_decile"] = pd.qcut(
            ranked["model_score"].rank(method="first"),
            bucket_count,
            labels=False,
        ) + 1
        ranked["model_rank_on_date"] = ranked["model_score"].rank(method="first", ascending=False).astype(int)
        ranked["model_universe_on_date"] = len(ranked)
        pieces.append(ranked)
    if not pieces:
        return frame.assign(model_decile=pd.Series(dtype=int))
    return pd.concat(pieces, ignore_index=True).sort_values(["date", "model_rank_on_date"]).reset_index(drop=True)


def safe_float(value: Any, digits: int | None = 6) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits) if digits is not None else number


def max_drawdown(values: pd.Series) -> float | None:
    if values.empty:
        return None
    equity = (1 + values.fillna(0)).cumprod()
    drawdown = equity / equity.cummax() - 1
    return safe_float(drawdown.min())


def bootstrap_mean_ci(series: pd.Series, samples: int = 2000, seed: int = 42) -> dict[str, float | None]:
    clean = pd.to_numeric(series, errors="coerce").dropna().to_numpy(dtype=float)
    if len(clean) < 3:
        return {"low": None, "high": None}
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(clean), size=(samples, len(clean)))
    means = clean[indices].mean(axis=1)
    return {
        "low": safe_float(np.quantile(means, 0.025)),
        "high": safe_float(np.quantile(means, 0.975)),
    }


def winsorized_mean(series: pd.Series, lower: float = 0.05, upper: float = 0.95) -> float:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return float("nan")
    low = clean.quantile(lower)
    high = clean.quantile(upper)
    return float(clean.clip(low, high).mean())


def top_positive_contribution_share(series: pd.Series, count: int = 5) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    positive = clean[clean.gt(0)].sort_values(ascending=False)
    positive_sum = positive.sum()
    if positive.empty or positive_sum <= 0:
        return None
    return float(positive.head(count).sum() / positive_sum)


def summarize_segments(frame: pd.DataFrame, return_column: str) -> pd.DataFrame:
    rows = []
    if frame.empty:
        return pd.DataFrame(rows)
    for date_value, group in frame.groupby("date", sort=True):
        ranked = group.sort_values("model_score", ascending=False).copy()
        segment_specs = [
            ("top_25", ranked.head(min(25, len(ranked)))),
            ("top_50", ranked.head(min(50, len(ranked)))),
            ("top_decile", ranked[ranked["model_decile"].eq(ranked["model_decile"].max())]),
            ("bottom_decile", ranked[ranked["model_decile"].eq(ranked["model_decile"].min())]),
        ]
        for segment, segment_frame in segment_specs:
            if segment_frame.empty:
                continue
            rows.append(
                {
                    "date": date_value,
                    "segment": segment,
                    "count": int(len(segment_frame)),
                    "avg_sector_neutral_return_14d_after_cost": float(segment_frame[return_column].mean()),
                    "median_sector_neutral_return_14d_after_cost": float(segment_frame[return_column].median()),
                    "winsorized_sector_neutral_return_14d_after_cost": winsorized_mean(segment_frame[return_column]),
                    "hit_rate_sector_neutral_after_cost": float((segment_frame[return_column] > 0).mean()),
                    "top5_positive_contribution_share": top_positive_contribution_share(segment_frame[return_column]),
                }
            )
    daily = pd.DataFrame(rows)
    if daily.empty:
        return daily
    return (
        daily.groupby("segment")
        .agg(
            scoring_dates=("date", "nunique"),
            avg_count=("count", "mean"),
            avg_sector_neutral_return_14d_after_cost=("avg_sector_neutral_return_14d_after_cost", "mean"),
            avg_daily_median_sector_neutral_return_14d_after_cost=(
                "median_sector_neutral_return_14d_after_cost",
                "mean",
            ),
            avg_daily_winsorized_sector_neutral_return_14d_after_cost=(
                "winsorized_sector_neutral_return_14d_after_cost",
                "mean",
            ),
            hit_rate_sector_neutral_after_cost=("hit_rate_sector_neutral_after_cost", "mean"),
            avg_top5_positive_contribution_share=("top5_positive_contribution_share", "mean"),
        )
        .reset_index()
        .sort_values("segment")
    )


def summarize_non_overlapping_cohorts(top_bottom_daily: pd.DataFrame, horizon_days: int) -> pd.DataFrame:
    if top_bottom_daily.empty:
        return pd.DataFrame()
    rows = []
    ordered = top_bottom_daily.sort_values("date").reset_index(drop=True)
    for offset in range(horizon_days):
        cohort = ordered.iloc[offset::horizon_days].copy()
        if cohort.empty:
            continue
        rows.append(
            {
                "offset": offset,
                "periods": int(len(cohort)),
                "start_date": cohort["date"].min(),
                "end_date": cohort["date"].max(),
                "avg_top_sector_neutral_return_14d_after_cost": float(
                    cohort["avg_sector_neutral_return_14d_after_cost_top"].mean()
                ),
                "median_top_sector_neutral_return_14d_after_cost": float(
                    cohort["avg_sector_neutral_return_14d_after_cost_top"].median()
                ),
                "avg_top_minus_bottom_sector_neutral_14d": float(
                    cohort["top_minus_bottom_sector_neutral_14d"].mean()
                ),
                "positive_spread_rate": float((cohort["top_minus_bottom_sector_neutral_14d"] > 0).mean()),
                "max_drawdown_top_return_approx": max_drawdown(
                    cohort["avg_sector_neutral_return_14d_after_cost_top"] / horizon_days
                ),
            }
        )
    result = pd.DataFrame(rows)
    if result.empty:
        return result
    return result.sort_values("offset").reset_index(drop=True)


def summarize_window(
    name: str,
    description: str,
    warning: str | None,
    frame: pd.DataFrame,
    return_column: str,
    horizon_days: int,
) -> WindowResult:
    if frame.empty:
        empty = pd.DataFrame()
        return WindowResult(
            name,
            description,
            warning,
            None,
            None,
            0,
            0,
            empty,
            empty,
            empty,
            empty,
            empty,
            empty,
            {"status": "empty"},
        )

    daily_rows = []
    for (date_value, decile), group in frame.groupby(["date", "model_decile"], sort=True):
        daily_rows.append(
            {
                "date": date_value,
                "model_decile": int(decile),
                "count": int(len(group)),
                "avg_model_score": float(group["model_score"].mean()),
                "avg_stock_return_14d": float(group["forward_return_14d_next_close"].mean()),
                "median_stock_return_14d": float(group["forward_return_14d_next_close"].median()),
                "avg_excess_vs_spy_14d": float(group["excess_return_14d_next_close"].mean()),
                "median_excess_vs_spy_14d": float(group["excess_return_14d_next_close"].median()),
                "avg_sector_neutral_return_14d_after_cost": float(group[return_column].mean()),
                "median_sector_neutral_return_14d_after_cost": float(group[return_column].median()),
                "winsorized_sector_neutral_return_14d_after_cost": winsorized_mean(group[return_column]),
                "hit_rate_vs_spy": float(group["hit_vs_spy"].mean()),
                "hit_rate_sector_neutral_after_cost": float(group["hit_sector_neutral_after_cost"].mean()),
                "top5_positive_contribution_share": top_positive_contribution_share(group[return_column]),
            },
        )
    daily_deciles = pd.DataFrame(daily_rows)

    decile_summary = (
        daily_deciles.groupby("model_decile")
        .agg(
            scoring_dates=("date", "nunique"),
            avg_count=("count", "mean"),
            avg_model_score=("avg_model_score", "mean"),
            avg_stock_return_14d=("avg_stock_return_14d", "mean"),
            avg_daily_median_stock_return_14d=("median_stock_return_14d", "mean"),
            avg_excess_vs_spy_14d=("avg_excess_vs_spy_14d", "mean"),
            avg_daily_median_excess_vs_spy_14d=("median_excess_vs_spy_14d", "mean"),
            avg_sector_neutral_return_14d_after_cost=("avg_sector_neutral_return_14d_after_cost", "mean"),
            avg_daily_median_sector_neutral_return_14d_after_cost=(
                "median_sector_neutral_return_14d_after_cost",
                "mean",
            ),
            avg_daily_winsorized_sector_neutral_return_14d_after_cost=(
                "winsorized_sector_neutral_return_14d_after_cost",
                "mean",
            ),
            hit_rate_vs_spy=("hit_rate_vs_spy", "mean"),
            hit_rate_sector_neutral_after_cost=("hit_rate_sector_neutral_after_cost", "mean"),
            avg_top5_positive_contribution_share=("top5_positive_contribution_share", "mean"),
        )
        .reset_index()
        .sort_values("model_decile", ascending=False)
    )

    top_decile = int(decile_summary["model_decile"].max())
    bottom_decile = int(decile_summary["model_decile"].min())
    top_daily = daily_deciles[daily_deciles["model_decile"].eq(top_decile)].copy()
    bottom_daily = daily_deciles[daily_deciles["model_decile"].eq(bottom_decile)].copy()
    top_bottom_daily = top_daily.merge(
        bottom_daily,
        on="date",
        suffixes=("_top", "_bottom"),
    )
    top_bottom_daily["top_minus_bottom_sector_neutral_14d"] = (
        top_bottom_daily["avg_sector_neutral_return_14d_after_cost_top"]
        - top_bottom_daily["avg_sector_neutral_return_14d_after_cost_bottom"]
    )
    top_bottom_daily["top_minus_bottom_median_sector_neutral_14d"] = (
        top_bottom_daily["median_sector_neutral_return_14d_after_cost_top"]
        - top_bottom_daily["median_sector_neutral_return_14d_after_cost_bottom"]
    )
    top_bottom_daily["top_minus_bottom_excess_vs_spy_14d"] = (
        top_bottom_daily["avg_excess_vs_spy_14d_top"] - top_bottom_daily["avg_excess_vs_spy_14d_bottom"]
    )
    segment_summary = summarize_segments(frame, return_column)
    cohort_summary = summarize_non_overlapping_cohorts(top_bottom_daily, horizon_days)

    latest_date = frame["date"].max()
    latest_top_decile = (
        frame[frame["date"].eq(latest_date) & frame["model_decile"].eq(top_decile)]
        .sort_values("model_score", ascending=False)
        .head(25)
        [
            [
                "date",
                "symbol",
                "sector",
                "model_score",
                "model_rank_on_date",
                "forward_return_14d_next_close",
                "excess_return_14d_next_close",
                return_column,
            ]
        ]
        .copy()
    )

    decile_for_corr = decile_summary.sort_values("model_decile")
    monotonicity = decile_for_corr["model_decile"].corr(
        decile_for_corr["avg_sector_neutral_return_14d_after_cost"],
        method="spearman",
    )
    top_summary = decile_summary[decile_summary["model_decile"].eq(top_decile)].iloc[0]
    bottom_summary = decile_summary[decile_summary["model_decile"].eq(bottom_decile)].iloc[0]
    diagnostics = {
        "status": "ready",
        "topDecile": top_decile,
        "bottomDecile": bottom_decile,
        "topDecileAvgSectorNeutralReturn14dAfterCost": safe_float(
            top_summary["avg_sector_neutral_return_14d_after_cost"],
        ),
        "topDecileAvgDailyMedianSectorNeutralReturn14dAfterCost": safe_float(
            top_summary["avg_daily_median_sector_neutral_return_14d_after_cost"],
        ),
        "topDecileAvgDailyWinsorizedSectorNeutralReturn14dAfterCost": safe_float(
            top_summary["avg_daily_winsorized_sector_neutral_return_14d_after_cost"],
        ),
        "topDecileAvgTop5PositiveContributionShare": safe_float(
            top_summary["avg_top5_positive_contribution_share"],
        ),
        "bottomDecileAvgSectorNeutralReturn14dAfterCost": safe_float(
            bottom_summary["avg_sector_neutral_return_14d_after_cost"],
        ),
        "bottomDecileAvgDailyMedianSectorNeutralReturn14dAfterCost": safe_float(
            bottom_summary["avg_daily_median_sector_neutral_return_14d_after_cost"],
        ),
        "topMinusBottomAvgSectorNeutralReturn14dAfterCost": safe_float(
            top_summary["avg_sector_neutral_return_14d_after_cost"]
            - bottom_summary["avg_sector_neutral_return_14d_after_cost"],
        ),
        "topMinusBottomAvgDailyMedianSectorNeutralReturn14dAfterCost": safe_float(
            top_summary["avg_daily_median_sector_neutral_return_14d_after_cost"]
            - bottom_summary["avg_daily_median_sector_neutral_return_14d_after_cost"],
        ),
        "topMinusBottomAvgDailyWinsorizedSectorNeutralReturn14dAfterCost": safe_float(
            top_summary["avg_daily_winsorized_sector_neutral_return_14d_after_cost"]
            - bottom_summary["avg_daily_winsorized_sector_neutral_return_14d_after_cost"],
        ),
        "topDecileHitRateVsSpy": safe_float(top_summary["hit_rate_vs_spy"]),
        "topDecileSectorNeutralHitRateAfterCost": safe_float(
            top_summary["hit_rate_sector_neutral_after_cost"],
        ),
        "decileReturnSpearman": safe_float(monotonicity),
        "topMinusBottomPositiveDateRate": safe_float(
            (top_bottom_daily["top_minus_bottom_sector_neutral_14d"] > 0).mean(),
        ),
        "topMinusBottomMaxDrawdownApprox": max_drawdown(
            top_bottom_daily["top_minus_bottom_sector_neutral_14d"] / horizon_days,
        ),
        "topDecileMeanBootstrapCi95": bootstrap_mean_ci(
            top_bottom_daily["avg_sector_neutral_return_14d_after_cost_top"]
        ),
        "topMinusBottomMeanBootstrapCi95": bootstrap_mean_ci(
            top_bottom_daily["top_minus_bottom_sector_neutral_14d"]
        ),
        "nonOverlappingCohortCount": int(len(cohort_summary)) if not cohort_summary.empty else 0,
        "nonOverlappingMeanTopReturn": safe_float(
            cohort_summary["avg_top_sector_neutral_return_14d_after_cost"].mean()
            if not cohort_summary.empty
            else None
        ),
        "nonOverlappingMeanSpread": safe_float(
            cohort_summary["avg_top_minus_bottom_sector_neutral_14d"].mean()
            if not cohort_summary.empty
            else None
        ),
    }

    return WindowResult(
        name=name,
        description=description,
        warning=warning,
        start_date=frame["date"].min().date().isoformat(),
        end_date=frame["date"].max().date().isoformat(),
        scoring_dates=int(frame["date"].nunique()),
        rows=int(len(frame)),
        decile_summary=decile_summary,
        daily_deciles=daily_deciles,
        top_bottom_daily=top_bottom_daily,
        segment_summary=segment_summary,
        cohort_summary=cohort_summary,
        latest_top_decile=latest_top_decile,
        diagnostics=diagnostics,
    )


def window_payload(result: WindowResult) -> dict[str, Any]:
    return {
        "name": result.name,
        "description": result.description,
        "warning": result.warning,
        "startDate": result.start_date,
        "endDate": result.end_date,
        "scoringDates": result.scoring_dates,
        "rows": result.rows,
        "diagnostics": result.diagnostics,
    }


def top_decile_entrant_tables(scored: pd.DataFrame, lookback_dates: int = 5) -> tuple[pd.DataFrame, pd.DataFrame]:
    if scored.empty or "model_decile" not in scored.columns:
        return pd.DataFrame(), pd.DataFrame()
    dates = list(pd.DatetimeIndex(sorted(scored["date"].dropna().unique())))
    if not dates:
        return pd.DataFrame(), pd.DataFrame()
    latest_date = dates[-1]
    latest = scored[scored["date"].eq(latest_date)].copy()
    top_decile = int(latest["model_decile"].max())
    latest_top = latest[latest["model_decile"].eq(top_decile)].sort_values("model_rank_on_date").copy()

    date_offsets = {
        1: dates[-2] if len(dates) >= 2 else None,
        lookback_dates: dates[-(lookback_dates + 1)] if len(dates) >= lookback_dates + 1 else None,
        10: dates[-11] if len(dates) >= 11 else None,
    }
    history = {
        date_value: scored[scored["date"].eq(date_value)].set_index("symbol")
        for date_value in date_offsets.values()
        if date_value is not None
    }
    symbol_history = scored[["date", "symbol", "model_decile", "model_rank_on_date", "model_score"]].copy()

    rows = []
    for row in latest_top.itertuples(index=False):
        symbol = str(getattr(row, "symbol"))
        current_rank = int(getattr(row, "model_rank_on_date"))
        current_score = float(getattr(row, "model_score"))
        prior_values: dict[str, Any] = {}
        for offset, date_value in date_offsets.items():
            prior = history.get(date_value)
            prefix = f"prior_{offset}d"
            if prior is not None and symbol in prior.index:
                prior_row = prior.loc[symbol]
                prior_values[f"{prefix}_date"] = date_value.date().isoformat()
                prior_values[f"{prefix}_rank"] = int(prior_row["model_rank_on_date"])
                prior_values[f"{prefix}_decile"] = int(prior_row["model_decile"])
                prior_values[f"{prefix}_score"] = float(prior_row["model_score"])
                prior_values[f"{prefix}_rank_improvement"] = int(prior_row["model_rank_on_date"] - current_rank)
            else:
                prior_values[f"{prefix}_date"] = date_value.date().isoformat() if date_value is not None else None
                prior_values[f"{prefix}_rank"] = None
                prior_values[f"{prefix}_decile"] = None
                prior_values[f"{prefix}_score"] = None
                prior_values[f"{prefix}_rank_improvement"] = None

        symbol_rows = symbol_history[symbol_history["symbol"].eq(symbol)].sort_values("date")
        top_flags = symbol_rows["model_decile"].eq(top_decile).tolist()
        streak = 0
        for flag in reversed(top_flags):
            if flag:
                streak += 1
            else:
                break
        current_top_rows = symbol_rows[symbol_rows["model_decile"].eq(top_decile)]
        first_top_date = current_top_rows["date"].min().date().isoformat() if not current_top_rows.empty else None
        streak_start_index = max(0, len(symbol_rows) - streak)
        streak_start_date = symbol_rows.iloc[streak_start_index]["date"].date().isoformat() if streak else None

        prior_1_decile = prior_values.get("prior_1d_decile")
        prior_lb_decile = prior_values.get(f"prior_{lookback_dates}d_decile")
        improvement = prior_values.get(f"prior_{lookback_dates}d_rank_improvement")
        if prior_1_decile != top_decile:
            entry_reason = "New since prior scoring date"
        elif prior_lb_decile != top_decile:
            entry_reason = f"New within {lookback_dates} scoring dates"
        elif improvement is not None and improvement >= 50:
            entry_reason = f"Rank improved {improvement} spots over {lookback_dates} scoring dates"
        else:
            entry_reason = "Existing top-decile constituent"

        rows.append(
            {
                "as_of_date": latest_date.date().isoformat(),
                "symbol": symbol,
                "sector": getattr(row, "sector", None),
                "current_rank": current_rank,
                "current_decile": top_decile,
                "current_score": current_score,
                "first_top_decile_date": first_top_date,
                "current_top_decile_streak": streak,
                "current_streak_start_date": streak_start_date,
                "entry_reason": entry_reason,
                **prior_values,
            }
        )

    current = pd.DataFrame(rows).sort_values("current_rank").reset_index(drop=True)
    if current.empty:
        return current, current
    entrants = current[
        current["entry_reason"].ne("Existing top-decile constituent")
    ].copy()
    entrants = entrants.sort_values(
        ["current_top_decile_streak", f"prior_{lookback_dates}d_rank_improvement", "current_rank"],
        ascending=[True, False, True],
    ).reset_index(drop=True)
    return current, entrants


def model_health_status(windows: list[WindowResult]) -> dict[str, Any]:
    recent = next((window for window in windows if window.name == "recent_completed"), None)
    strict = next((window for window in windows if window.name == "strict_post_training"), None)
    if recent is None or recent.diagnostics.get("status") != "ready":
        return {"status": "Unavailable", "detail": "Recent completed monitoring window is empty."}

    diagnostics = recent.diagnostics
    checks = {
        "top_decile_average_positive": diagnostics.get("topDecileAvgSectorNeutralReturn14dAfterCost", 0) > 0,
        "top_decile_median_positive": diagnostics.get("topDecileAvgDailyMedianSectorNeutralReturn14dAfterCost", 0) > 0,
        "top_minus_bottom_spread_positive": diagnostics.get("topMinusBottomAvgSectorNeutralReturn14dAfterCost", 0) > 0,
        "median_spread_positive": diagnostics.get("topMinusBottomAvgDailyMedianSectorNeutralReturn14dAfterCost", 0) > 0,
        "hit_rate_above_52pct": diagnostics.get("topDecileSectorNeutralHitRateAfterCost", 0) >= 0.52,
        "positive_spread_rate_above_70pct": diagnostics.get("topMinusBottomPositiveDateRate", 0) >= 0.70,
    }
    passed = sum(1 for value in checks.values() if value)
    if passed >= 5:
        status = "Healthy"
    elif passed >= 3:
        status = "Watch"
    else:
        status = "Deteriorating"

    strict_note = None
    if strict is not None and strict.scoring_dates < 20:
        strict_note = "Strict post-training sample is still too small for a hard model-change call."
    return {
        "status": status,
        "passedChecks": passed,
        "totalChecks": len(checks),
        "checks": checks,
        "detail": strict_note or "Recent completed diagnostics are the primary health input.",
    }


def format_pct(value: Any) -> str:
    number = safe_float(value, digits=None)
    if number is None:
        return "n/a"
    return f"{number * 100:.2f}%"


def format_number(value: Any) -> str:
    number = safe_float(value, digits=None)
    if number is None:
        return "n/a"
    return f"{number:.3f}"


def format_ci(ci: Any) -> str:
    if not isinstance(ci, dict) or ci.get("low") is None or ci.get("high") is None:
        return "n/a"
    return f"{format_pct(ci.get('low'))} to {format_pct(ci.get('high'))}"


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if frame.empty:
        frame.to_csv(path, index=False)
        return
    clean = frame.copy()
    for column in clean.columns:
        if pd.api.types.is_datetime64_any_dtype(clean[column]):
            clean[column] = clean[column].dt.date.astype(str)
    clean.to_csv(path, index=False)


def svg_escape(value: Any) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def html_escape(value: Any) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def write_bar_svg(
    frame: pd.DataFrame,
    path: Path,
    value_column: str,
    title: str,
    y_label: str,
    percent: bool = True,
) -> None:
    width = 980
    height = 520
    left = 78
    right = 26
    top = 70
    bottom = 82
    plot_width = width - left - right
    plot_height = height - top - bottom
    rows = frame.sort_values("model_decile")
    if rows.empty:
        path.write_text("<svg xmlns=\"http://www.w3.org/2000/svg\"></svg>\n", encoding="utf-8")
        return
    values = rows[value_column].astype(float).to_numpy()
    low = min(0.0, float(np.nanmin(values)))
    high = max(0.0, float(np.nanmax(values)))
    if math.isclose(low, high):
        low -= 0.01
        high += 0.01
    span = high - low

    def y_for(value: float) -> float:
        return top + (high - value) / span * plot_height

    zero_y = y_for(0)
    bar_gap = 12
    bar_width = (plot_width - bar_gap * (len(rows) - 1)) / len(rows)
    pieces = [
        f"<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{width}\" height=\"{height}\" viewBox=\"0 0 {width} {height}\">",
        "<rect width=\"100%\" height=\"100%\" fill=\"#07110d\"/>",
        f"<text x=\"{left}\" y=\"36\" fill=\"#e7fff3\" font-family=\"Arial, sans-serif\" font-size=\"22\" font-weight=\"700\">{svg_escape(title)}</text>",
        f"<text x=\"{left}\" y=\"58\" fill=\"#8fb9a2\" font-family=\"Arial, sans-serif\" font-size=\"13\">{svg_escape(y_label)}</text>",
        f"<line x1=\"{left}\" y1=\"{zero_y:.2f}\" x2=\"{width - right}\" y2=\"{zero_y:.2f}\" stroke=\"#6fbf90\" stroke-opacity=\"0.55\" stroke-width=\"1\"/>",
        f"<line x1=\"{left}\" y1=\"{top}\" x2=\"{left}\" y2=\"{height - bottom}\" stroke=\"#254235\"/>",
        f"<line x1=\"{left}\" y1=\"{height - bottom}\" x2=\"{width - right}\" y2=\"{height - bottom}\" stroke=\"#254235\"/>",
    ]
    for index, row in enumerate(rows.itertuples(index=False)):
        decile = int(getattr(row, "model_decile"))
        value = float(getattr(row, value_column))
        x = left + index * (bar_width + bar_gap)
        y = min(y_for(value), zero_y)
        bar_height = abs(y_for(value) - zero_y)
        color = "#45e38a" if value >= 0 else "#ff647c"
        label = f"{value * 100:.2f}%" if percent else f"{value:.2f}"
        pieces.extend(
            [
                f"<rect x=\"{x:.2f}\" y=\"{y:.2f}\" width=\"{bar_width:.2f}\" height=\"{bar_height:.2f}\" rx=\"4\" fill=\"{color}\" fill-opacity=\"0.86\"/>",
                f"<text x=\"{x + bar_width / 2:.2f}\" y=\"{height - 48}\" text-anchor=\"middle\" fill=\"#cce9d8\" font-family=\"Arial, sans-serif\" font-size=\"13\">D{decile}</text>",
                f"<text x=\"{x + bar_width / 2:.2f}\" y=\"{y - 7 if value >= 0 else y + bar_height + 18:.2f}\" text-anchor=\"middle\" fill=\"#e7fff3\" font-family=\"Arial, sans-serif\" font-size=\"12\">{label}</text>",
            ],
        )
    pieces.append("</svg>")
    path.write_text("\n".join(pieces) + "\n", encoding="utf-8")


def write_line_svg(frame: pd.DataFrame, path: Path, title: str) -> None:
    width = 980
    height = 520
    left = 78
    right = 26
    top = 70
    bottom = 98
    plot_width = width - left - right
    plot_height = height - top - bottom
    if frame.empty:
        path.write_text("<svg xmlns=\"http://www.w3.org/2000/svg\"></svg>\n", encoding="utf-8")
        return
    rows = frame.sort_values("date").copy()
    values = rows["top_minus_bottom_sector_neutral_14d"].astype(float).to_numpy()
    low = min(0.0, float(np.nanmin(values)))
    high = max(0.0, float(np.nanmax(values)))
    if math.isclose(low, high):
        low -= 0.01
        high += 0.01
    span = high - low

    def x_for(index: int) -> float:
        if len(rows) == 1:
            return left + plot_width / 2
        return left + index / (len(rows) - 1) * plot_width

    def y_for(value: float) -> float:
        return top + (high - value) / span * plot_height

    points = " ".join(f"{x_for(index):.2f},{y_for(value):.2f}" for index, value in enumerate(values))
    zero_y = y_for(0)
    pieces = [
        f"<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{width}\" height=\"{height}\" viewBox=\"0 0 {width} {height}\">",
        "<rect width=\"100%\" height=\"100%\" fill=\"#07110d\"/>",
        f"<text x=\"{left}\" y=\"36\" fill=\"#e7fff3\" font-family=\"Arial, sans-serif\" font-size=\"22\" font-weight=\"700\">{svg_escape(title)}</text>",
        f"<text x=\"{left}\" y=\"58\" fill=\"#8fb9a2\" font-family=\"Arial, sans-serif\" font-size=\"13\">Top decile minus bottom decile, 14-trading-day sector-neutral after-cost return</text>",
        f"<line x1=\"{left}\" y1=\"{zero_y:.2f}\" x2=\"{width - right}\" y2=\"{zero_y:.2f}\" stroke=\"#6fbf90\" stroke-opacity=\"0.55\" stroke-width=\"1\"/>",
        f"<line x1=\"{left}\" y1=\"{top}\" x2=\"{left}\" y2=\"{height - bottom}\" stroke=\"#254235\"/>",
        f"<line x1=\"{left}\" y1=\"{height - bottom}\" x2=\"{width - right}\" y2=\"{height - bottom}\" stroke=\"#254235\"/>",
        f"<polyline points=\"{points}\" fill=\"none\" stroke=\"#45e38a\" stroke-width=\"3\" stroke-linejoin=\"round\" stroke-linecap=\"round\"/>",
    ]
    for index, row in enumerate(rows.itertuples(index=False)):
        value = float(getattr(row, "top_minus_bottom_sector_neutral_14d"))
        x = x_for(index)
        y = y_for(value)
        color = "#45e38a" if value >= 0 else "#ff647c"
        pieces.append(f"<circle cx=\"{x:.2f}\" cy=\"{y:.2f}\" r=\"4\" fill=\"{color}\"/>")
        if index in {0, len(rows) - 1}:
            date_text = pd.to_datetime(getattr(row, "date")).date().isoformat()
            pieces.append(
                f"<text x=\"{x:.2f}\" y=\"{height - 62}\" text-anchor=\"middle\" fill=\"#cce9d8\" font-family=\"Arial, sans-serif\" font-size=\"12\">{date_text}</text>",
            )
    pieces.append("</svg>")
    path.write_text("\n".join(pieces) + "\n", encoding="utf-8")


def load_shap_features(model_dir: Path, model_name: str, limit: int = 18) -> pd.DataFrame:
    path = model_dir / f"{model_name}_explainability.json"
    if not path.exists():
        return pd.DataFrame()
    payload = json.loads(path.read_text(encoding="utf-8"))
    features = pd.DataFrame(payload.get("topFeatures", []))
    if features.empty:
        return features
    return features.head(limit).copy()


def write_shap_svg(features: pd.DataFrame, path: Path) -> None:
    width = 1120
    row_height = 34
    top = 78
    left = 350
    right = 34
    bottom = 36
    height = top + bottom + max(1, len(features)) * row_height
    if features.empty:
        path.write_text("<svg xmlns=\"http://www.w3.org/2000/svg\"></svg>\n", encoding="utf-8")
        return
    rows = features.sort_values("meanAbsShap", ascending=True).reset_index(drop=True)
    max_value = float(rows["meanAbsShap"].max())
    plot_width = width - left - right
    pieces = [
        f"<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{width}\" height=\"{height}\" viewBox=\"0 0 {width} {height}\">",
        "<rect width=\"100%\" height=\"100%\" fill=\"#07110d\"/>",
        f"<text x=\"28\" y=\"36\" fill=\"#e7fff3\" font-family=\"Arial, sans-serif\" font-size=\"22\" font-weight=\"700\">Model Feature Influence</text>",
        f"<text x=\"28\" y=\"58\" fill=\"#8fb9a2\" font-family=\"Arial, sans-serif\" font-size=\"13\">Mean absolute SHAP contribution from the tuned holdout explanation artifact</text>",
    ]
    for index, row in enumerate(rows.itertuples(index=False)):
        y = top + index * row_height
        value = float(getattr(row, "meanAbsShap"))
        mean_value = float(getattr(row, "meanShap", 0) or 0)
        bar_width = 0 if max_value <= 0 else value / max_value * plot_width
        color = "#45e38a" if mean_value >= 0 else "#ff647c"
        label = str(getattr(row, "feature"))
        pieces.extend(
            [
                f"<text x=\"28\" y=\"{y + 19}\" fill=\"#dff8ea\" font-family=\"Arial, sans-serif\" font-size=\"13\">{svg_escape(label)}</text>",
                f"<rect x=\"{left}\" y=\"{y + 5}\" width=\"{bar_width:.2f}\" height=\"20\" rx=\"4\" fill=\"{color}\" fill-opacity=\"0.86\"/>",
                f"<text x=\"{left + bar_width + 8:.2f}\" y=\"{y + 20}\" fill=\"#cce9d8\" font-family=\"Arial, sans-serif\" font-size=\"12\">{value:.4f}</text>",
            ]
        )
    pieces.append("</svg>")
    path.write_text("\n".join(pieces) + "\n", encoding="utf-8")


def write_window_outputs(result: WindowResult, output_dir: Path) -> None:
    prefix = result.name
    write_csv(result.decile_summary, output_dir / f"{prefix}_decile_summary.csv")
    write_csv(result.daily_deciles, output_dir / f"{prefix}_daily_deciles.csv")
    write_csv(result.top_bottom_daily, output_dir / f"{prefix}_top_bottom_daily.csv")
    write_csv(result.segment_summary, output_dir / f"{prefix}_segment_summary.csv")
    write_csv(result.cohort_summary, output_dir / f"{prefix}_non_overlapping_cohorts.csv")
    write_csv(result.latest_top_decile, output_dir / f"{prefix}_latest_top_decile.csv")
    if not result.decile_summary.empty:
        write_bar_svg(
            result.decile_summary,
            output_dir / f"{prefix}_sector_neutral_return.svg",
            "avg_sector_neutral_return_14d_after_cost",
            f"{result.description}: Decile Returns",
            "Average realized 14D sector-neutral return after estimated trading cost",
        )
        write_bar_svg(
            result.decile_summary,
            output_dir / f"{prefix}_median_sector_neutral_return.svg",
            "avg_daily_median_sector_neutral_return_14d_after_cost",
            f"{result.description}: Median Decile Returns",
            "Average daily median realized 14D sector-neutral return after estimated trading cost",
        )
        write_bar_svg(
            result.decile_summary,
            output_dir / f"{prefix}_hit_rate.svg",
            "hit_rate_sector_neutral_after_cost",
            f"{result.description}: Hit Rate",
            "Share of names with positive sector-neutral return after estimated trading cost",
        )
        write_line_svg(
            result.top_bottom_daily,
            output_dir / f"{prefix}_top_bottom_spread.svg",
            f"{result.description}: Top-Bottom Spread",
        )


def markdown_table(frame: pd.DataFrame, columns: list[str]) -> str:
    if frame.empty:
        return "_No rows._"
    rows = frame[columns].copy()
    headers = [column.replace("_", " ") for column in columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows.itertuples(index=False):
        values = []
        for value in row:
            if isinstance(value, float):
                values.append(f"{value:.4f}")
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def html_metric(label: str, value: str) -> str:
    return f"""
      <div class="metric">
        <span>{html_escape(label)}</span>
        <strong>{html_escape(value)}</strong>
      </div>
    """


def html_table(frame: pd.DataFrame, columns: list[str], percent_columns: set[str] | None = None) -> str:
    percent_columns = percent_columns or set()
    if frame.empty:
        return "<p class=\"muted\">No rows.</p>"
    header = "".join(f"<th>{html_escape(column.replace('_', ' '))}</th>" for column in columns)
    rows = []
    for row in frame[columns].itertuples(index=False):
        cells = []
        for column, value in zip(columns, row):
            if isinstance(value, float):
                text = format_pct(value) if column in percent_columns else f"{value:.4f}"
            else:
                text = str(value)
            cells.append(f"<td>{html_escape(text)}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return f"""
      <div class="table-wrap">
        <table>
          <thead><tr>{header}</tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
      </div>
    """


def window_html(result: WindowResult) -> str:
    diagnostics = result.diagnostics
    percent_columns = {
        "avg_sector_neutral_return_14d_after_cost",
        "avg_daily_median_sector_neutral_return_14d_after_cost",
        "avg_daily_winsorized_sector_neutral_return_14d_after_cost",
        "avg_excess_vs_spy_14d",
        "avg_daily_median_excess_vs_spy_14d",
        "hit_rate_sector_neutral_after_cost",
        "hit_rate_vs_spy",
        "avg_top5_positive_contribution_share",
        "positive_spread_rate",
        "avg_top_sector_neutral_return_14d_after_cost",
        "median_top_sector_neutral_return_14d_after_cost",
        "avg_top_minus_bottom_sector_neutral_14d",
        "forward_return_14d_next_close",
        "excess_return_14d_next_close",
        "sector_neutral_forward_return_14d_after_cost",
    }
    decile_table = html_table(
        result.decile_summary,
        [
            "model_decile",
            "scoring_dates",
            "avg_count",
            "avg_sector_neutral_return_14d_after_cost",
            "avg_daily_median_sector_neutral_return_14d_after_cost",
            "avg_daily_winsorized_sector_neutral_return_14d_after_cost",
            "avg_excess_vs_spy_14d",
            "avg_daily_median_excess_vs_spy_14d",
            "hit_rate_sector_neutral_after_cost",
            "hit_rate_vs_spy",
            "avg_top5_positive_contribution_share",
        ],
        percent_columns=percent_columns,
    )
    segment_table = html_table(
        result.segment_summary,
        [
            "segment",
            "scoring_dates",
            "avg_count",
            "avg_sector_neutral_return_14d_after_cost",
            "avg_daily_median_sector_neutral_return_14d_after_cost",
            "avg_daily_winsorized_sector_neutral_return_14d_after_cost",
            "hit_rate_sector_neutral_after_cost",
            "avg_top5_positive_contribution_share",
        ],
        percent_columns=percent_columns,
    )
    cohort_table = html_table(
        result.cohort_summary,
        [
            "offset",
            "periods",
            "start_date",
            "end_date",
            "avg_top_sector_neutral_return_14d_after_cost",
            "median_top_sector_neutral_return_14d_after_cost",
            "avg_top_minus_bottom_sector_neutral_14d",
            "positive_spread_rate",
        ],
        percent_columns=percent_columns,
    )
    top_table = html_table(
        result.latest_top_decile,
        [
            "date",
            "symbol",
            "sector",
            "model_score",
            "model_rank_on_date",
            "forward_return_14d_next_close",
            "excess_return_14d_next_close",
            "sector_neutral_forward_return_14d_after_cost",
        ],
        percent_columns=percent_columns,
    )
    warning = f"<p class=\"warning\">{html_escape(result.warning)}</p>" if result.warning else ""
    return f"""
    <section id="{html_escape(result.name)}" class="window">
      <div class="section-title">
        <p>Window</p>
        <h2>{html_escape(result.description)}</h2>
      </div>
      {warning}
      <div class="metrics">
        {html_metric("Date range", f"{result.start_date} to {result.end_date}")}
        {html_metric("Scoring dates", str(result.scoring_dates))}
        {html_metric("Rows scored", f"{result.rows:,}")}
        {html_metric("Top decile 14D sector-neutral return", format_pct(diagnostics.get("topDecileAvgSectorNeutralReturn14dAfterCost")))}
        {html_metric("Top decile median 14D return", format_pct(diagnostics.get("topDecileAvgDailyMedianSectorNeutralReturn14dAfterCost")))}
        {html_metric("Top decile winsorized 14D return", format_pct(diagnostics.get("topDecileAvgDailyWinsorizedSectorNeutralReturn14dAfterCost")))}
        {html_metric("Top decile bootstrap range", format_ci(diagnostics.get("topDecileMeanBootstrapCi95")))}
        {html_metric("Top-minus-bottom spread", format_pct(diagnostics.get("topMinusBottomAvgSectorNeutralReturn14dAfterCost")))}
        {html_metric("Median top-minus-bottom spread", format_pct(diagnostics.get("topMinusBottomAvgDailyMedianSectorNeutralReturn14dAfterCost")))}
        {html_metric("Winsorized top-minus-bottom spread", format_pct(diagnostics.get("topMinusBottomAvgDailyWinsorizedSectorNeutralReturn14dAfterCost")))}
        {html_metric("Spread bootstrap range", format_ci(diagnostics.get("topMinusBottomMeanBootstrapCi95")))}
        {html_metric("Top decile hit rate vs SPY", format_pct(diagnostics.get("topDecileHitRateVsSpy")))}
        {html_metric("Top 5 positive contribution share", format_pct(diagnostics.get("topDecileAvgTop5PositiveContributionShare")))}
        {html_metric("Decile return Spearman", format_number(diagnostics.get("decileReturnSpearman")))}
      </div>
      <div class="charts">
        <figure>
          <img src="{html_escape(result.name)}_sector_neutral_return.svg" alt="{html_escape(result.description)} sector-neutral decile returns">
          <figcaption>Average realized 14-trading-day sector-neutral return after estimated trading cost.</figcaption>
        </figure>
        <figure>
          <img src="{html_escape(result.name)}_median_sector_neutral_return.svg" alt="{html_escape(result.description)} median sector-neutral decile returns">
          <figcaption>Average daily median return. This shows whether the typical name in each decile worked, not just the equal-weight basket average.</figcaption>
        </figure>
        <figure>
          <img src="{html_escape(result.name)}_hit_rate.svg" alt="{html_escape(result.description)} hit rate">
          <figcaption>Share of names with positive sector-neutral return after estimated trading cost.</figcaption>
        </figure>
        <figure class="wide">
          <img src="{html_escape(result.name)}_top_bottom_spread.svg" alt="{html_escape(result.description)} top-bottom spread">
          <figcaption>Daily top-decile minus bottom-decile spread over the measured forward window.</figcaption>
        </figure>
      </div>
      <h3>Decile Summary</h3>
      {decile_table}
      <h3>Top Bucket Robustness</h3>
      {segment_table}
      <h3>Non-Overlapping Cohorts</h3>
      {cohort_table}
      <h3>Latest Top-Decile Constituents</h3>
      {top_table}
    </section>
    """


def entrants_html(current_top_decile: pd.DataFrame, entrants: pd.DataFrame) -> str:
    percent_columns: set[str] = set()
    entrant_table = html_table(
        entrants.head(30),
        [
            "as_of_date",
            "symbol",
            "sector",
            "current_rank",
            "prior_1d_decile",
            "prior_5d_decile",
            "prior_5d_rank_improvement",
            "current_top_decile_streak",
            "entry_reason",
        ],
        percent_columns=percent_columns,
    )
    current_table = html_table(
        current_top_decile.head(25),
        [
            "as_of_date",
            "symbol",
            "sector",
            "current_rank",
            "prior_5d_rank",
            "prior_5d_decile",
            "prior_5d_rank_improvement",
            "current_top_decile_streak",
        ],
        percent_columns=percent_columns,
    )
    latest_date = current_top_decile["as_of_date"].iloc[0] if not current_top_decile.empty else "n/a"
    return f"""
    <section id="top_decile_entrants" class="window">
      <div class="section-title">
        <p>Upgrade Tracking</p>
        <h2>Recent Top-Decile Entrants</h2>
      </div>
      <p class="muted">
        This uses all scored feature-complete dates through the latest price date, not just dates with completed 14-day outcomes.
        A name is highlighted when it is currently top decile and was not top decile recently, or when its rank improved sharply.
      </p>
      <div class="metrics">
        {html_metric("As-of date", str(latest_date))}
        {html_metric("Current top-decile names", str(len(current_top_decile)))}
        {html_metric("Recent entrants / upgrades", str(len(entrants)))}
      </div>
      <h3>Recent Entrants And Sharp Upgrades</h3>
      {entrant_table}
      <h3>Current Top-Decile Snapshot</h3>
      {current_table}
    </section>
    """


def shap_html(features: pd.DataFrame) -> str:
    if features.empty:
        return ""
    table = html_table(
        features,
        [
            "feature",
            "description",
            "meanAbsShap",
            "meanShap",
            "positiveShare",
        ],
        percent_columns={"positiveShare"},
    )
    return f"""
    <section id="shap" class="window">
      <div class="section-title">
        <p>Explainability</p>
        <h2>SHAP Feature Influence</h2>
      </div>
      <p class="muted">
        These values come from the stored tuned-holdout explanation artifact. Mean absolute SHAP ranks which features moved model scores the most; the sign of mean SHAP is directional context, not a standalone trading rule.
      </p>
      <div class="charts">
        <figure class="wide">
          <img src="shap_feature_influence.svg" alt="SHAP feature influence">
          <figcaption>Higher bars indicate features that changed model ranking scores more in the holdout explanation sample.</figcaption>
        </figure>
      </div>
      <h3>Top SHAP Features</h3>
      {table}
    </section>
    """


def write_latest_html(
    output_dir: Path,
    metadata: dict[str, Any],
    model_metadata: dict[str, Any],
    windows: list[WindowResult],
    shap_features: pd.DataFrame,
    current_top_decile: pd.DataFrame,
    entrants: pd.DataFrame,
    health: dict[str, Any],
) -> None:
    generated = pd.Timestamp.now(tz=timezone.utc).isoformat()
    nav = "".join(
        f"<a href=\"#{html_escape(result.name)}\">{html_escape(result.description)}</a>"
        for result in windows
    )
    nav += "<a href=\"#top_decile_entrants\">Top-Decile Entrants</a><a href=\"#shap\">SHAP</a>"
    body = "\n".join(window_html(result) for result in windows)
    body = body + "\n" + entrants_html(current_top_decile, entrants) + "\n" + shap_html(shap_features)
    health_checks = health.get("checks", {})
    health_detail = "; ".join(
        f"{key.replace('_', ' ')}: {'pass' if value else 'fail'}"
        for key, value in health_checks.items()
    )
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Market Pulse Model Monitoring</title>
  <style>
    :root {{
      --bg: #050b08;
      --panel: #0a1510;
      --panel-2: #0d1e16;
      --line: #1f3b2e;
      --text: #e7fff3;
      --muted: #91b7a2;
      --green: #45e38a;
      --red: #ff647c;
      --amber: #ffd166;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: radial-gradient(circle at top left, rgba(69, 227, 138, 0.09), transparent 38%), var(--bg);
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.5;
    }}
    main {{ width: min(1180px, calc(100% - 32px)); margin: 0 auto; padding: 28px 0 60px; }}
    header {{
      border: 1px solid var(--line);
      background: linear-gradient(135deg, rgba(10, 21, 16, 0.94), rgba(7, 17, 13, 0.82));
      padding: 28px;
      border-radius: 10px;
    }}
    .eyebrow, .section-title p {{
      margin: 0 0 8px;
      color: var(--green);
      text-transform: uppercase;
      letter-spacing: 0.12em;
      font-size: 12px;
      font-weight: 800;
    }}
    h1 {{ margin: 0; font-size: clamp(32px, 5vw, 58px); line-height: 1.02; letter-spacing: 0; }}
    h2 {{ margin: 0; font-size: clamp(24px, 3vw, 34px); letter-spacing: 0; }}
    h3 {{ margin: 28px 0 12px; font-size: 18px; color: var(--text); }}
    .lede {{ max-width: 880px; color: #c9e7d6; font-size: 17px; margin: 16px 0 0; }}
    nav {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 22px; }}
    nav a {{
      color: var(--text);
      text-decoration: none;
      border: 1px solid var(--line);
      background: #08130e;
      padding: 9px 12px;
      border-radius: 8px;
      font-size: 14px;
    }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin-top: 18px;
    }}
    .metric {{
      border: 1px solid var(--line);
      background: var(--panel-2);
      padding: 14px;
      border-radius: 8px;
      min-height: 86px;
    }}
    .metric span {{ display: block; color: var(--muted); font-size: 12px; text-transform: uppercase; font-weight: 800; }}
    .metric strong {{ display: block; margin-top: 9px; font-size: 22px; letter-spacing: 0; }}
    .window {{
      margin-top: 24px;
      padding: 24px;
      border: 1px solid var(--line);
      background: rgba(10, 21, 16, 0.86);
      border-radius: 10px;
    }}
    .warning {{
      margin: 16px 0 0;
      padding: 12px 14px;
      border-left: 3px solid var(--amber);
      color: #ffe8a7;
      background: rgba(255, 209, 102, 0.08);
      border-radius: 6px;
    }}
    .charts {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
      margin-top: 22px;
    }}
    figure {{
      margin: 0;
      border: 1px solid var(--line);
      background: #06100b;
      border-radius: 8px;
      overflow: hidden;
    }}
    figure.wide {{ grid-column: 1 / -1; }}
    img {{ display: block; width: 100%; height: auto; }}
    figcaption {{ padding: 10px 12px 13px; color: var(--muted); font-size: 13px; }}
    .table-wrap {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }}
    table {{ width: 100%; border-collapse: collapse; min-width: 820px; background: #07110d; }}
    th, td {{ padding: 10px 11px; border-bottom: 1px solid rgba(31, 59, 46, 0.9); text-align: right; white-space: nowrap; }}
    th:first-child, td:first-child, td:nth-child(2), td:nth-child(3) {{ text-align: left; }}
    th {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; background: #0b1912; }}
    td {{ color: #dff8ea; font-size: 14px; }}
    .muted {{ color: var(--muted); }}
    footer {{ color: var(--muted); margin-top: 20px; font-size: 13px; }}
    @media (max-width: 760px) {{
      main {{ width: min(100% - 20px, 1180px); padding-top: 10px; }}
      header, .window {{ padding: 18px; border-radius: 8px; }}
      .charts {{ grid-template-columns: 1fr; }}
      .metric strong {{ font-size: 18px; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <p class="eyebrow">Market Pulse Analysis</p>
      <h1>Production Model Monitoring</h1>
      <p class="lede">
        Recent decile backtests for the frozen XGBoost rank model. This report is local-only and is not embedded in the public dashboard.
      </p>
      <div class="metrics">
        {html_metric("Generated", generated)}
        {html_metric("Model", str(model_metadata.get("model_path")))}
        {html_metric("Training window", f"{model_metadata.get('training_start_date')} to {model_metadata.get('training_end_date')}")}
        {html_metric("Latest SPY price date", str(metadata.get("latestSpyPriceDate")))}
        {html_metric("Symbols fetched", f"{metadata.get('fetchedSymbols')} / {metadata.get('requestedSymbols')}")}
        {html_metric("Label", str(model_metadata.get("return_column")))}
        {html_metric("Model health", f"{health.get('status')} ({health.get('passedChecks')}/{health.get('totalChecks')})")}
      </div>
      <p class="warning">{html_escape(health.get("detail", ""))} {html_escape(health_detail)}</p>
      <nav>{nav}</nav>
    </header>
    {body}
    <footer>
      Generated by <code>analysis/model-monitoring/run_recent_decile_backtest.py</code>. Daily 14-trading-day outcomes overlap, so short windows should be read as calibration checks rather than definitive proof.
    </footer>
  </main>
</body>
</html>
"""
    (output_dir / "latest_summary.html").write_text(html, encoding="utf-8")


def write_latest_markdown(
    output_dir: Path,
    metadata: dict[str, Any],
    model_metadata: dict[str, Any],
    windows: list[WindowResult],
) -> None:
    lines = [
        "# Model Monitoring Summary",
        "",
        f"Generated: {pd.Timestamp.now(tz=timezone.utc).isoformat()}",
        f"Model: `{model_metadata.get('model_path')}`",
        f"Training window: `{model_metadata.get('training_start_date')}` to `{model_metadata.get('training_end_date')}`",
        f"Label: `{model_metadata.get('return_column')}` over 14 trading days",
        f"Latest fetched SPY price date: `{metadata.get('latestSpyPriceDate')}`",
        f"Fetched symbols: `{metadata.get('fetchedSymbols')}` / requested `{metadata.get('requestedSymbols')}`",
        "",
        "## Windows",
        "",
    ]
    for result in windows:
        lines.extend(
            [
                f"### {result.description}",
                "",
                result.warning or "No window warning.",
                "",
                f"- Date range: `{result.start_date}` to `{result.end_date}`",
                f"- Scoring dates: `{result.scoring_dates}`",
                f"- Rows: `{result.rows}`",
                f"- Top decile average sector-neutral 14D return after cost: {format_pct(result.diagnostics.get('topDecileAvgSectorNeutralReturn14dAfterCost'))}",
                f"- Top decile average daily median sector-neutral 14D return after cost: {format_pct(result.diagnostics.get('topDecileAvgDailyMedianSectorNeutralReturn14dAfterCost'))}",
                f"- Top-minus-bottom average sector-neutral spread: {format_pct(result.diagnostics.get('topMinusBottomAvgSectorNeutralReturn14dAfterCost'))}",
                f"- Top-minus-bottom average daily median sector-neutral spread: {format_pct(result.diagnostics.get('topMinusBottomAvgDailyMedianSectorNeutralReturn14dAfterCost'))}",
                f"- Top decile hit rate vs SPY: {format_pct(result.diagnostics.get('topDecileHitRateVsSpy'))}",
                f"- Decile return Spearman rank correlation: {format_number(result.diagnostics.get('decileReturnSpearman'))}",
                "",
                "Top-level decile table:",
                "",
                markdown_table(
                    result.decile_summary,
                    [
                        "model_decile",
                        "scoring_dates",
                        "avg_sector_neutral_return_14d_after_cost",
                        "avg_daily_median_sector_neutral_return_14d_after_cost",
                        "avg_excess_vs_spy_14d",
                        "avg_daily_median_excess_vs_spy_14d",
                        "hit_rate_sector_neutral_after_cost",
                        "hit_rate_vs_spy",
                    ],
                ),
                "",
            ],
        )
    (output_dir / "latest_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def to_jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return safe_float(value, digits=None)
    if isinstance(value, float):
        return safe_float(value, digits=None)
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    return value


def frame_records_for_summary(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    clean = frame.copy()
    for column in clean.columns:
        if pd.api.types.is_datetime64_any_dtype(clean[column]):
            clean[column] = clean[column].dt.date.astype(str)
    return to_jsonable(clean.replace([np.inf, -np.inf], np.nan).where(pd.notna(clean), None).to_dict(orient="records"))


def main() -> None:
    args = parse_args()
    model_dir = ROOT / args.model_dir
    output_dir = ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    model_metadata, model_path = load_model_metadata(model_dir, args.model_name)
    feature_columns = list(model_metadata["feature_columns"])
    return_column = model_metadata.get("return_column", "sector_neutral_forward_return_14d_after_cost")

    print(f"Fetching fresh price history for model monitoring ({args.years} years)...")
    dataset, fetch_metadata = fetch_monitoring_dataset(args, feature_columns)
    all_scored = assign_deciles(score_feature_rows(dataset, model_path, feature_columns))
    scored = filter_completed_outcomes(all_scored, return_column)

    training_end = pd.to_datetime(model_metadata["training_end_date"])
    strict_start = pd.to_datetime(args.strict_start_date) if args.strict_start_date else training_end + pd.Timedelta(days=1)
    strict = scored[scored["date"].gt(training_end) & scored["date"].ge(strict_start)].copy()
    recent_dates = sorted(scored["date"].dropna().unique())[-args.recent_dates :]
    recent = scored[scored["date"].isin(recent_dates)].copy()

    strict_result = summarize_window(
        "strict_post_training",
        "Strict Post-Training",
        (
            "This is the cleanest live-style check because it excludes dates used for training. "
            "It may be small until more 14-trading-day outcomes complete."
        ),
        strict,
        return_column,
        args.horizon_days,
    )
    recent_warning = None
    if not recent.empty and recent["date"].min() <= training_end:
        recent_warning = (
            "This window overlaps the training period because the production model was trained very recently. "
            "Use it as a drift diagnostic, not as a clean performance claim."
        )
    recent_result = summarize_window(
        "recent_completed",
        "Recent Completed",
        recent_warning,
        recent,
        return_column,
        args.horizon_days,
    )
    windows = [strict_result, recent_result]
    health = model_health_status(windows)
    current_top_decile, top_decile_entrants = top_decile_entrant_tables(all_scored)
    shap_features = load_shap_features(model_dir, args.model_name)

    for result in windows:
        write_window_outputs(result, output_dir)
    write_csv(current_top_decile, output_dir / "current_top_decile.csv")
    write_csv(top_decile_entrants, output_dir / "recent_top_decile_entrants.csv")
    write_csv(shap_features, output_dir / "shap_feature_summary.csv")
    write_shap_svg(shap_features, output_dir / "shap_feature_influence.svg")

    summary = {
        "generatedAt": pd.Timestamp.now(tz=timezone.utc).isoformat(),
        "model": {
            "name": args.model_name,
            "path": str(model_path.relative_to(ROOT)),
            "trainingStartDate": model_metadata.get("training_start_date"),
            "trainingEndDate": model_metadata.get("training_end_date"),
            "targetColumn": model_metadata.get("target_column"),
            "returnColumn": return_column,
            "featureCount": len(feature_columns),
        },
        "inputs": {
            "years": args.years,
            "horizonDays": args.horizon_days,
            "costBps": args.cost_bps,
            "recentDates": args.recent_dates,
            "maxSymbols": args.max_symbols,
        },
        "data": fetch_metadata,
        "windows": [window_payload(result) for result in windows],
        "health": health,
        "topDecileEntrants": {
            "asOfDate": current_top_decile["as_of_date"].iloc[0] if not current_top_decile.empty else None,
            "currentTopDecileCount": int(len(current_top_decile)),
            "recentEntrantCount": int(len(top_decile_entrants)),
            "recentEntrants": frame_records_for_summary(top_decile_entrants.head(25)),
        },
        "shap": {
            "featureCount": int(len(shap_features)),
            "topFeatures": frame_records_for_summary(shap_features.head(15)),
        },
        "notes": [
            "Strict post-training results are the cleanest, but may have few dates immediately after retraining.",
            "Daily 14-trading-day outcomes overlap. Treat short windows as calibration checks, not definitive proof.",
            "Recent completed results are useful for drift monitoring even when they overlap the training period.",
        ],
    }
    (output_dir / "summary.json").write_text(json.dumps(to_jsonable(summary), indent=2) + "\n", encoding="utf-8")
    write_latest_markdown(output_dir, fetch_metadata, model_metadata, windows)
    write_latest_html(
        output_dir,
        fetch_metadata,
        model_metadata,
        windows,
        shap_features,
        current_top_decile,
        top_decile_entrants,
        health,
    )

    for result in windows:
        print(
            f"{result.description}: {result.scoring_dates} scoring dates, "
            f"top decile return {format_pct(result.diagnostics.get('topDecileAvgSectorNeutralReturn14dAfterCost'))}, "
            f"spread {format_pct(result.diagnostics.get('topMinusBottomAvgSectorNeutralReturn14dAfterCost'))}",
        )
    print(f"Wrote model monitoring outputs to {output_dir.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
