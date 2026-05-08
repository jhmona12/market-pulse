from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from common import PRICES_DIR, REPORTS_DIR, ROOT


PREDICTIONS_PATH = REPORTS_DIR / "xgboost_rank_sector14_feature_v2_tuned_test_predictions.csv"
FEATURES_PATH = ROOT / "data" / "modeling" / "features" / "training_dataset.csv.gz"
ROUND_TRIP_COST = 0.0015
DEFAULT_OUTPUT_CSV = REPORTS_DIR / "rebound_activation_backtest_rules.csv"
DEFAULT_OUTPUT_JSON = REPORTS_DIR / "rebound_activation_backtest_summary.json"
DEFAULT_OUTPUT_SVG = ROOT / "docs" / "rebound-activation-backtest.svg"

SECTOR_TO_ETF = {
    "Information Technology": "XLK",
    "Communication Services": "XLC",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Financials": "XLF",
    "Health Care": "XLV",
    "Industrials": "XLI",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Materials": "XLB",
    "Real Estate": "XLRE",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest rebound activation rules for model-ranked non-momentum names.")
    parser.add_argument("--predictions", default=str(PREDICTIONS_PATH.relative_to(ROOT)))
    parser.add_argument("--features", default=str(FEATURES_PATH.relative_to(ROOT)))
    parser.add_argument("--output-csv", default=str(DEFAULT_OUTPUT_CSV.relative_to(ROOT)))
    parser.add_argument("--output-json", default=str(DEFAULT_OUTPUT_JSON.relative_to(ROOT)))
    parser.add_argument("--output-svg", default=str(DEFAULT_OUTPUT_SVG.relative_to(ROOT)))
    return parser.parse_args()


def load_predictions(path: Path) -> pd.DataFrame:
    predictions = pd.read_csv(
        path,
        usecols=[
            "date",
            "symbol",
            "sector",
            "close",
            "sector_neutral_forward_return_14d_after_cost",
            "predicted_rank_score",
        ],
        parse_dates=["date"],
    )
    predictions["model_rank"] = predictions.groupby("date")["predicted_rank_score"].rank(
        method="first",
        ascending=False,
    )
    predictions["model_universe_count"] = predictions.groupby("date")["symbol"].transform("count")
    predictions["top_decile"] = predictions["model_rank"] <= np.ceil(predictions["model_universe_count"] * 0.10)
    return predictions


def load_feature_subset(path: Path) -> pd.DataFrame:
    return pd.read_csv(
        path,
        usecols=[
            "date",
            "symbol",
            "ret_20d",
            "ret_60d",
            "volatility_20d",
            "rsi_14",
            "price_vs_sma_20",
            "price_vs_sma_50",
            "price_vs_sma_200",
        ],
        parse_dates=["date"],
    )


def load_prices(symbols: set[str]) -> dict[str, pd.DataFrame]:
    prices = {}
    for symbol in sorted(symbols):
        path = PRICES_DIR / f"{symbol}.csv"
        if not path.exists():
            continue
        frame = pd.read_csv(path, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        frame = frame.dropna(subset=["close"]).reset_index(drop=True)
        if not frame.empty:
            prices[symbol] = frame
    return prices


def index_after(prices: dict[str, pd.DataFrame], symbol: str, date: pd.Timestamp) -> int | None:
    frame = prices.get(symbol)
    if frame is None:
        return None
    index = int(np.searchsorted(frame["date"].values, np.datetime64(date), side="right"))
    return index if index < len(frame) else None


def index_on_or_before(prices: dict[str, pd.DataFrame], symbol: str, date: pd.Timestamp) -> int | None:
    frame = prices.get(symbol)
    if frame is None:
        return None
    index = int(np.searchsorted(frame["date"].values, np.datetime64(date), side="right")) - 1
    return index if index >= 0 else None


def sector_neutral_return(
    prices: dict[str, pd.DataFrame],
    symbol: str,
    sector: str,
    entry_index: int,
    entry_price: float,
    holding_days: int = 14,
) -> float | None:
    frame = prices.get(symbol)
    if frame is None:
        return None
    exit_index = entry_index + holding_days
    if exit_index >= len(frame):
        return None

    stock_return = float(frame.loc[exit_index, "close"]) / entry_price - 1
    sector_return = 0.0
    sector_etf = SECTOR_TO_ETF.get(sector)
    if sector_etf in prices:
        entry_date = frame.loc[entry_index, "date"]
        exit_date = frame.loc[exit_index, "date"]
        sector_entry = index_on_or_before(prices, sector_etf, entry_date)
        sector_exit = index_on_or_before(prices, sector_etf, exit_date)
        if sector_entry is not None and sector_exit is not None and sector_exit > sector_entry:
            sector_return = float(prices[sector_etf].loc[sector_exit, "close"]) / float(
                prices[sector_etf].loc[sector_entry, "close"]
            ) - 1
    return stock_return - sector_return - ROUND_TRIP_COST


def activation_price(row: pd.Series, rule: str, multiple: float | None = None) -> float | None:
    close = float(row["close"])
    if rule == "vol":
        volatility = float(row["volatility_20d"])
        if not np.isfinite(volatility) or volatility <= 0:
            return None
        return close * (1 + float(multiple) * volatility)
    if rule == "sma20":
        price_vs_sma_20 = float(row["price_vs_sma_20"])
        if not np.isfinite(price_vs_sma_20) or (1 + price_vs_sma_20) <= 0:
            return None
        return close / (1 + price_vs_sma_20)
    if rule in {"prior_close_high_5", "prior_close_high_10"}:
        return None
    raise ValueError(f"Unsupported activation rule: {rule}")


def prior_close_high(prices: dict[str, pd.DataFrame], symbol: str, date: pd.Timestamp, lookback: int) -> float | None:
    frame = prices.get(symbol)
    index = index_on_or_before(prices, symbol, date)
    if frame is None or index is None:
        return None
    start = max(0, index - lookback + 1)
    return float(frame.loc[start:index, "close"].max())


def trigger_result(
    prices: dict[str, pd.DataFrame],
    row: pd.Series,
    trigger_price: float,
    window: int,
    entry_at_close: bool = True,
) -> float | None:
    frame = prices.get(str(row["symbol"]))
    start = index_after(prices, str(row["symbol"]), row["date"])
    if frame is None or start is None:
        return None
    candidates = frame.iloc[start : min(len(frame), start + window)]
    hits = candidates[candidates["close"] >= trigger_price]
    if hits.empty:
        return None
    entry_index = int(hits.index[0])
    entry_price = float(frame.loc[entry_index, "close"]) if entry_at_close else trigger_price
    return sector_neutral_return(prices, str(row["symbol"]), str(row["sector"]), entry_index, entry_price)


def summarize(values: pd.Series | list[float]) -> dict:
    series = pd.Series(values).dropna()
    if series.empty:
        return {"n": 0, "mean": None, "median": None, "hitRate": None, "p25": None, "p75": None}
    return {
        "n": int(len(series)),
        "mean": float(series.mean()),
        "median": float(series.median()),
        "hitRate": float((series > 0).mean()),
        "p25": float(series.quantile(0.25)),
        "p75": float(series.quantile(0.75)),
    }


def evaluate(candidates: pd.DataFrame, prices: dict[str, pd.DataFrame], setup_name: str) -> list[dict]:
    baseline = summarize(candidates["sector_neutral_forward_return_14d_after_cost"])
    rows = [
        {
            "setup": setup_name,
            "rule": "buy_immediately",
            "windowDays": 0,
            "volMultiple": None,
            "coverage": 1.0,
            "avgTriggerPct": 0.0,
            **baseline,
        }
    ]

    for window in (3, 5, 10):
        for multiple in (0.25, 0.50, 0.75, 1.00, 1.25, 1.50, 2.00):
            returns = []
            trigger_pcts = []
            for _, row in candidates.iterrows():
                trigger = activation_price(row, "vol", multiple)
                if trigger is None:
                    continue
                trigger_pcts.append(trigger / float(row["close"]) - 1)
                result = trigger_result(prices, row, trigger, window)
                if result is not None:
                    returns.append(result)
            rows.append(
                {
                    "setup": setup_name,
                    "rule": f"vol_{multiple:g}",
                    "windowDays": window,
                    "volMultiple": multiple,
                    "coverage": len(returns) / max(1, len(candidates)),
                    "avgTriggerPct": float(np.mean(trigger_pcts)) if trigger_pcts else None,
                    **summarize(returns),
                }
            )

    comparison_rules = (("prior_close_high_5", 5), ("prior_close_high_10", 5), ("sma20", 10))
    for rule, window in comparison_rules:
        returns = []
        trigger_pcts = []
        for _, row in candidates.iterrows():
            if rule == "sma20":
                trigger = activation_price(row, rule)
            else:
                trigger = prior_close_high(prices, str(row["symbol"]), row["date"], int(rule.rsplit("_", 1)[-1]))
            if trigger is None:
                continue
            trigger_pcts.append(trigger / float(row["close"]) - 1)
            result = trigger_result(prices, row, trigger, window)
            if result is not None:
                returns.append(result)
        rows.append(
            {
                "setup": setup_name,
                "rule": rule,
                "windowDays": window,
                "volMultiple": None,
                "coverage": len(returns) / max(1, len(candidates)),
                "avgTriggerPct": float(np.mean(trigger_pcts)) if trigger_pcts else None,
                **summarize(returns),
            }
        )
    return rows


def fmt_pct(value: float | None) -> str:
    if value is None or not np.isfinite(value):
        return "n/a"
    return f"{value * 100:.2f}%"


def write_svg(rows: pd.DataFrame, output_path: Path) -> None:
    chart_rows = rows[
        (rows["setup"] == "strict_rebound_watch")
        & (
            (rows["rule"] == "buy_immediately")
            | ((rows["rule"].isin(["vol_0.5", "vol_0.75", "vol_1"])) & (rows["windowDays"] == 5))
            | ((rows["rule"].isin(["prior_close_high_5", "sma20"])) & (rows["windowDays"].isin([5, 10])))
        )
    ].copy()
    labels = {
        "buy_immediately": "Immediate",
        "vol_0.5": "0.50x Vol",
        "vol_0.75": "0.75x Vol",
        "vol_1": "1.00x Vol",
        "prior_close_high_5": "5D High",
        "sma20": "20D SMA",
    }
    chart_rows["label"] = chart_rows["rule"].map(labels)
    chart_rows = chart_rows.dropna(subset=["mean"])
    width, height = 920, 440
    margin_left, margin_bottom, margin_top = 76, 88, 50
    chart_width = width - margin_left - 42
    chart_height = height - margin_top - margin_bottom
    max_value = max(0.03, float(chart_rows["mean"].max()) * 1.2)
    bar_width = chart_width / max(1, len(chart_rows)) * 0.58

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        "<title>Rebound activation backtest</title>",
        "<desc>Average 14 trading day sector-neutral return by activation rule for strict rebound-watch signals.</desc>",
        '<rect width="100%" height="100%" fill="#06110c"/>',
        '<text x="40" y="32" fill="#e8fff0" font-family="Arial, sans-serif" font-size="22" font-weight="700">Rebound Activation Backtest</text>',
        '<text x="40" y="55" fill="#8ea99a" font-family="Arial, sans-serif" font-size="13">Strict rebound-watch signals, 2025-04-09 to 2026-04-10; return is 14D sector-neutral after 15 bps cost.</text>',
    ]
    axis_y = margin_top + chart_height
    parts.append(f'<line x1="{margin_left}" y1="{axis_y}" x2="{width - 28}" y2="{axis_y}" stroke="#244133"/>')
    for tick in np.linspace(0, max_value, 4):
        y = axis_y - (tick / max_value) * chart_height
        parts.append(f'<line x1="{margin_left}" y1="{y:.1f}" x2="{width - 28}" y2="{y:.1f}" stroke="#10261b"/>')
        parts.append(
            f'<text x="{margin_left - 12}" y="{y + 4:.1f}" fill="#8ea99a" font-family="Arial, sans-serif" font-size="12" text-anchor="end">{tick * 100:.1f}%</text>'
        )
    for index, row in enumerate(chart_rows.itertuples(index=False)):
        slot = chart_width / max(1, len(chart_rows))
        x = margin_left + index * slot + (slot - bar_width) / 2
        bar_height = float(row.mean) / max_value * chart_height
        y = axis_y - bar_height
        fill = "#39ff88" if row.rule == "vol_0.75" else "#4fd8ff"
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" rx="5" fill="{fill}" opacity="0.86"/>')
        parts.append(
            f'<text x="{x + bar_width / 2:.1f}" y="{y - 8:.1f}" fill="#e8fff0" font-family="Arial, sans-serif" font-size="12" text-anchor="middle">{row.mean * 100:.2f}%</text>'
        )
        parts.append(
            f'<text x="{x + bar_width / 2:.1f}" y="{axis_y + 22}" fill="#cbe3d5" font-family="Arial, sans-serif" font-size="12" text-anchor="middle">{row.label}</text>'
        )
        parts.append(
            f'<text x="{x + bar_width / 2:.1f}" y="{axis_y + 40}" fill="#8ea99a" font-family="Arial, sans-serif" font-size="11" text-anchor="middle">hit {row.hitRate * 100:.1f}%</text>'
        )
        parts.append(
            f'<text x="{x + bar_width / 2:.1f}" y="{axis_y + 56}" fill="#8ea99a" font-family="Arial, sans-serif" font-size="11" text-anchor="middle">cov {row.coverage * 100:.1f}%</text>'
        )
    parts.append("</svg>")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def json_clean(value):
    if isinstance(value, dict):
        return {key: json_clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_clean(item) for item in value]
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def main() -> None:
    args = parse_args()
    predictions_path = ROOT / args.predictions
    features_path = ROOT / args.features
    output_csv = ROOT / args.output_csv
    output_json = ROOT / args.output_json
    output_svg = ROOT / args.output_svg

    predictions = load_predictions(predictions_path)
    features = load_feature_subset(features_path)
    dataset = predictions.merge(features, on=["date", "symbol"], how="left")

    broad = dataset["top_decile"] & ((dataset["price_vs_sma_50"] < 0) | (dataset["price_vs_sma_200"] < 0))
    strict = (
        dataset["top_decile"]
        & (dataset["price_vs_sma_50"] < 0)
        & (dataset["price_vs_sma_200"] < 0)
        & (dataset["ret_20d"] < 0)
        & (dataset["ret_60d"] < 0)
    )
    oversold = strict & (dataset["rsi_14"] <= 35)

    symbols = set(dataset.loc[broad | strict | oversold, "symbol"]) | set(SECTOR_TO_ETF.values())
    prices = load_prices(symbols)

    rows = []
    for setup_name, mask in [
        ("broad_non_momentum_top_decile", broad),
        ("strict_rebound_watch", strict),
        ("oversold_strict_rebound_watch", oversold),
    ]:
        rows.extend(evaluate(dataset.loc[mask].copy(), prices, setup_name))

    result = pd.DataFrame(rows)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_csv, index=False)
    write_svg(result, output_svg)

    preferred = result[
        (result["setup"] == "strict_rebound_watch")
        & (result["rule"].isin(["buy_immediately", "vol_0.5", "vol_0.75", "vol_1", "sma20", "prior_close_high_5"]))
        & (result["windowDays"].isin([0, 5, 10]))
    ].copy()
    payload = {
        "generatedAt": pd.Timestamp.now("UTC").isoformat(),
        "predictionsPath": str(predictions_path.relative_to(ROOT)),
        "featuresPath": str(features_path.relative_to(ROOT)),
        "dateRange": {
            "start": predictions["date"].min().date().isoformat(),
            "end": predictions["date"].max().date().isoformat(),
            "uniqueDates": int(predictions["date"].nunique()),
        },
        "methodology": {
            "label": "14 trading day sector-neutral return after 15 bps round-trip cost",
            "strictReboundWatch": "Top-decile model rank, below 50D and 200D trend, negative 20D and 60D returns.",
            "activationRule": "Close above current close plus k times 20-day realized daily volatility within the activation window.",
            "recommendedRule": "0.75x 20-day realized daily volatility within 5 trading days.",
        },
        "preferredRows": preferred.to_dict(orient="records"),
        "files": {
            "csv": str(output_csv.relative_to(ROOT)),
            "svg": str(output_svg.relative_to(ROOT)),
        },
    }
    output_json.write_text(json.dumps(json_clean(payload), indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {output_csv.relative_to(ROOT)}")
    print(f"Wrote {output_json.relative_to(ROOT)}")
    print(f"Wrote {output_svg.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
