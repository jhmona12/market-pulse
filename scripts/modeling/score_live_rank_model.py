from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from build_training_dataset import build_market_context, enrich_price_features, sector_name_for_etf
from common import ROOT, SECTOR_ETFS, fetch_sp500_constituents, fetch_yahoo_history, run_config_for_years, write_json
from model_features import add_cross_sectional_model_features, model_feature_columns

try:
    import xgboost as xgb
except ModuleNotFoundError as error:  # pragma: no cover - handled at runtime
    raise SystemExit(
        "xgboost is not installed in the current Python environment. "
        "Run scripts/modeling/setup_training_env.sh before scoring the live model."
    ) from error


MODEL_DIR = ROOT / "models" / "rank"
DEFAULT_MODEL_NAME = "xgboost_rank_sector14_tuned"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score the latest S&P 500 universe with the production rank model.")
    parser.add_argument("--model-dir", default=str(MODEL_DIR.relative_to(ROOT)), help="Committed model artifact directory.")
    parser.add_argument("--model-name", default=DEFAULT_MODEL_NAME, help="Base model artifact name.")
    parser.add_argument("--output", default="data/model-rank-scores.json", help="Output JSON path.")
    parser.add_argument("--years", type=int, default=3, help="Trailing years of daily history to fetch for live features.")
    parser.add_argument("--max-workers", type=int, default=8, help="Concurrent Yahoo history fetch workers.")
    parser.add_argument("--max-symbols", type=int, default=0, help="Optional development cap for stock symbols.")
    parser.add_argument(
        "--focus-symbols",
        default="",
        help="Optional comma/space separated symbols to score against the S&P 500 reference universe.",
    )
    return parser.parse_args()


def parse_focus_symbols(value: str) -> list[str]:
    symbols = []
    for token in value.replace(",", " ").replace(";", " ").split():
        symbol = token.strip().upper().lstrip("$")
        if not symbol:
            continue
        if not all(character.isalnum() or character in ".-^=" for character in symbol):
            continue
        symbols.append(symbol)
    return list(dict.fromkeys(symbols))[:25]


def load_fallback_constituents() -> pd.DataFrame:
    config = json.loads((ROOT / "config" / "universe.json").read_text(encoding="utf-8"))
    frame = pd.DataFrame(config["fallbackStocks"])
    frame["sub_industry"] = "Fallback"
    return frame[["symbol", "name", "sector", "sub_industry"]]


def load_constituents(max_symbols: int) -> tuple[pd.DataFrame, str]:
    try:
        constituents = fetch_sp500_constituents()
        source = "wikipedia"
    except Exception:  # noqa: BLE001 - fallback keeps the dashboard alive
        constituents = load_fallback_constituents()
        source = "fallback_universe"
    constituents["symbol"] = constituents["symbol"].astype(str)
    if max_symbols > 0:
        constituents = constituents.head(max_symbols).copy()
    return constituents, source


def load_price_frame(symbol: str, years: int) -> tuple[str, pd.DataFrame | None, str | None]:
    config = run_config_for_years(years)
    try:
        frame = fetch_yahoo_history(symbol, config.start_date, config.end_date)
        if frame.empty:
            raise ValueError("empty price history")
        frame["date"] = pd.to_datetime(frame["date"])
        for column in ("open", "high", "low", "close", "volume"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
        frame["symbol"] = symbol
        return symbol, enrich_price_features(frame), None
    except Exception as error:  # noqa: BLE001
        return symbol, None, str(error)


def fetch_symbol_frames(symbols: list[str], years: int, max_workers: int) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    frames: dict[str, pd.DataFrame] = {}
    failures: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(load_price_frame, symbol, years) for symbol in symbols]
        for future in as_completed(futures):
            symbol, frame, error = future.result()
            if frame is None:
                failures[symbol] = error or "unknown error"
            else:
                frames[symbol] = frame
    return frames, failures


def build_spy_context(spy_frame: pd.DataFrame) -> pd.DataFrame:
    spy = spy_frame[
        [
            "date",
            "forward_return_14d",
            "ret_1d",
            "ret_20d",
            "ret_60d",
            "ret_120d",
            "volatility_20d",
            "volatility_60d",
            "price_vs_sma_50",
            "price_vs_sma_200",
            "rsi_14",
        ]
    ].copy()
    return spy.rename(
        columns={
            "forward_return_14d": "spy_forward_return_14d",
            "ret_1d": "spy_ret_1d",
            "ret_20d": "spy_ret_20d",
            "ret_60d": "spy_ret_60d",
            "ret_120d": "spy_ret_120d",
            "volatility_20d": "spy_volatility_20d",
            "volatility_60d": "spy_volatility_60d",
            "price_vs_sma_50": "spy_price_vs_sma_50",
            "price_vs_sma_200": "spy_price_vs_sma_200",
            "rsi_14": "spy_rsi_14",
        }
    )


def infer_sector_for_symbol(symbol: str, frame: pd.DataFrame, symbol_frames: dict[str, pd.DataFrame]) -> dict:
    stock_returns = frame[["date", "ret_1d"]].dropna().tail(252).rename(columns={"ret_1d": "stock_ret"})
    correlations = []
    for etf in SECTOR_ETFS:
        sector_frame = symbol_frames.get(etf)
        if sector_frame is None:
            continue
        merged = stock_returns.merge(
            sector_frame[["date", "ret_1d"]].dropna().tail(252).rename(columns={"ret_1d": "sector_ret"}),
            on="date",
            how="inner",
        )
        correlation = merged["stock_ret"].corr(merged["sector_ret"]) if len(merged) >= 60 else np.nan
        correlations.append(
            {
                "sector": sector_name_for_etf(etf),
                "sectorEtf": etf,
                "correlation": correlation,
                "observations": len(merged),
            }
        )

    clean = [item for item in correlations if np.isfinite(item["correlation"])]
    if not clean:
        return {
            "symbol": symbol,
            "sector": "Information Technology",
            "sectorEtf": "XLK",
            "sectorSource": "fallback",
            "sectorCorrelation": None,
            "sectorCorrelationObservations": 0,
        }

    best = sorted(clean, key=lambda item: item["correlation"], reverse=True)[0]
    return {
        "symbol": symbol,
        "sector": best["sector"],
        "sectorEtf": best["sectorEtf"],
        "sectorSource": "return_correlation",
        "sectorCorrelation": round(float(best["correlation"]), 4),
        "sectorCorrelationObservations": int(best["observations"]),
    }


def append_focus_constituents(
    constituents: pd.DataFrame,
    focus_symbols: list[str],
    symbol_frames: dict[str, pd.DataFrame],
) -> tuple[pd.DataFrame, dict[str, dict]]:
    existing = set(constituents["symbol"].astype(str))
    additions = []
    sector_diagnostics: dict[str, dict] = {}
    for symbol in focus_symbols:
        if symbol in existing:
            sector_diagnostics[symbol] = {
                "symbol": symbol,
                "sector": constituents.loc[constituents["symbol"].eq(symbol), "sector"].iloc[0],
                "sectorSource": "sp500_constituent",
                "sectorCorrelation": None,
                "sectorCorrelationObservations": None,
            }
            continue
        frame = symbol_frames.get(symbol)
        if frame is None:
            continue
        diagnostic = infer_sector_for_symbol(symbol, frame, symbol_frames)
        sector_diagnostics[symbol] = diagnostic
        additions.append(
            {
                "symbol": symbol,
                "name": symbol,
                "sector": diagnostic["sector"],
                "sub_industry": "Focus ticker",
            }
        )
    if additions:
        constituents = pd.concat([constituents, pd.DataFrame(additions)], ignore_index=True)
    return constituents, sector_diagnostics


def assemble_live_features(
    symbol_frames: dict[str, pd.DataFrame],
    constituents: pd.DataFrame,
    reference_symbols: set[str] | None = None,
) -> pd.DataFrame:
    spy = build_spy_context(symbol_frames["SPY"])
    reference_symbols = reference_symbols or set(constituents["symbol"].astype(str))
    reference_frames = {
        symbol: frame
        for symbol, frame in symbol_frames.items()
        if symbol in reference_symbols or symbol in {"SPY", *SECTOR_ETFS}
    }
    reference_constituents = constituents[constituents["symbol"].isin(reference_symbols)].copy()
    breadth, sector_context = build_market_context(reference_frames, reference_constituents)
    sector_lookup = constituents.set_index("symbol")["sector"].to_dict()
    dataset_frames = []
    for symbol in constituents["symbol"].tolist():
        frame = symbol_frames.get(symbol)
        if frame is None:
            continue
        frame = frame.copy()
        frame["sector"] = sector_lookup[symbol]
        frame["symbol"] = symbol
        frame = frame.merge(spy, on="date", how="left")
        frame = frame.merge(breadth, on="date", how="left")
        frame = frame.merge(sector_context, on=["date", "sector"], how="left")
        spy_variance_60d = frame["spy_ret_1d"].rolling(60).var()
        frame["beta_60d"] = frame["ret_1d"].rolling(60).cov(frame["spy_ret_1d"]) / spy_variance_60d.replace(0, np.nan)
        frame["excess_return_14d"] = frame["forward_return_14d"] - frame["spy_forward_return_14d"]
        frame["rel_ret_20d_vs_spy"] = frame["ret_20d"] - frame["spy_ret_20d"]
        frame["rel_ret_60d_vs_spy"] = frame["ret_60d"] - frame["spy_ret_60d"]
        frame["rel_ret_120d_vs_spy"] = frame["ret_120d"] - frame["spy_ret_120d"]
        frame["rel_rsi_vs_spy"] = frame["rsi_14"] - frame["spy_rsi_14"]
        frame["rel_volatility_20d_vs_spy"] = frame["volatility_20d"] - frame["spy_volatility_20d"]
        frame["rel_volatility_60d_vs_spy"] = frame["volatility_60d"] - frame["spy_volatility_60d"]
        frame["idiosyncratic_ret_20d"] = frame["ret_20d"] - frame["beta_60d"] * frame["spy_ret_20d"]
        frame["idiosyncratic_ret_60d"] = frame["ret_60d"] - frame["beta_60d"] * frame["spy_ret_60d"]
        dataset_frames.append(frame)

    dataset = pd.concat(dataset_frames, ignore_index=True).sort_values(["date", "symbol"]).reset_index(drop=True)
    dataset = add_cross_sectional_model_features(dataset, round_trip_cost=None)
    return dataset


def reason_tags(row: pd.Series) -> list[str]:
    reasons = []
    if row.get("volatility_60d_minus_sector_median", 0) < 0:
        reasons.append("Lower 60d volatility than sector median")
    if row.get("momentum_252d_skip_20_minus_sector_median", 0) > 0:
        reasons.append("Long-horizon momentum ahead of sector median")
    if row.get("ret_120d_sector_pct_rank", 0) >= 0.7:
        reasons.append("Top-sector 120d return rank")
    if row.get("price_vs_sma_200", 0) > 0:
        reasons.append("Above 200d trend")
    if row.get("distance_to_52w_high", -1) > -0.08:
        reasons.append("Within 8% of 52w high")
    if row.get("log_dollar_volume_20d_pct_rank", 0) >= 0.7:
        reasons.append("Liquid versus universe")
    return reasons[:4]


def risk_flags(row: pd.Series) -> list[str]:
    flags = []
    if row.get("rsi_14", 0) > 76:
        flags.append("RSI extended")
    if row.get("volatility_60d_minus_sector_median", 0) > 0:
        flags.append("Above-sector volatility")
    if row.get("price_vs_sma_50", 0) < 0:
        flags.append("Below 50d trend")
    if row.get("price_vs_sma_200", 0) < 0:
        flags.append("Below 200d trend")
    if row.get("volume_ratio_20", 1) < 0.7:
        flags.append("Weak volume confirmation")
    return flags[:4]


def finite_or_none(value: object, digits: int | None = None) -> float | int | str | None:
    if value is None:
        return None
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        if not np.isfinite(value):
            return None
        return round(float(value), digits) if digits is not None else float(value)
    return value


def score_rows(dataset: pd.DataFrame, model_path: Path, feature_columns: list[str]) -> pd.DataFrame:
    latest_date = dataset["date"].max()
    latest = dataset[dataset["date"].eq(latest_date)].copy()
    latest = latest.dropna(subset=feature_columns)
    booster = xgb.Booster()
    booster.load_model(str(model_path))
    dmatrix = xgb.DMatrix(latest[feature_columns].to_numpy(dtype=float), feature_names=feature_columns)
    latest["model_score"] = booster.predict(dmatrix)
    latest = latest.sort_values("model_score", ascending=False).reset_index(drop=True)
    latest["model_rank"] = np.arange(1, len(latest) + 1)
    latest["model_percentile"] = 100 * (1 - (latest["model_rank"] - 1) / max(1, len(latest) - 1))
    return latest


def sp500_comparison(row: pd.Series, reference_scores: pd.Series) -> dict:
    if reference_scores.empty:
        return {"sp500Rank": None, "sp500UniverseCount": 0, "sp500Percentile": None}
    rank = int((reference_scores > row["model_score"]).sum() + 1)
    universe_count = int(len(reference_scores))
    percentile = 100 * (1 - (rank - 1) / max(1, universe_count - 1))
    return {
        "sp500Rank": rank,
        "sp500UniverseCount": universe_count,
        "sp500Percentile": finite_or_none(percentile, 1),
    }


def row_payload(
    row: pd.Series,
    name_lookup: dict[str, str],
    sector_diagnostics: dict[str, dict] | None = None,
    reference_scores: pd.Series | None = None,
) -> dict:
    rank = int(row["model_rank"])
    universe_count = int(row["model_universe_count"])
    bucket = "Top Decile" if rank <= max(1, int(np.ceil(universe_count * 0.1))) else "Top Quintile" if rank <= max(1, int(np.ceil(universe_count * 0.2))) else "Ranked"
    diagnostic = (sector_diagnostics or {}).get(str(row["symbol"]), {})
    payload = {
        "symbol": row["symbol"],
        "name": name_lookup.get(row["symbol"], row["symbol"]),
        "sector": row["sector"],
        "modelRank": rank,
        "modelUniverseCount": universe_count,
        "modelScore": finite_or_none(row["model_score"], 6),
        "modelPercentile": finite_or_none(row["model_percentile"], 1),
        "modelBucket": bucket,
        "modelReasons": reason_tags(row),
        "riskFlags": risk_flags(row),
        "asOfDate": row["date"].date().isoformat(),
        "close": finite_or_none(row.get("close"), 2),
        "rsi14": finite_or_none(row.get("rsi_14"), 1),
        "return20": finite_or_none(row.get("ret_20d", 0) * 100, 2),
        "return60": finite_or_none(row.get("ret_60d", 0) * 100, 2),
        "return120": finite_or_none(row.get("ret_120d", 0) * 100, 2),
        "relativeReturn60VsSpy": finite_or_none(row.get("rel_ret_60d_vs_spy", 0) * 100, 2),
        "sectorReturn60": finite_or_none(row.get("sector_ret_60d", 0) * 100, 2),
        "volatility60d": finite_or_none(row.get("volatility_60d"), 4),
        "volatility60dVsSector": finite_or_none(row.get("volatility_60d_minus_sector_median"), 4),
        "distanceTo52wHigh": finite_or_none(row.get("distance_to_52w_high", 0) * 100, 2),
        "above50": bool(row.get("price_vs_sma_50", 0) > 0),
        "above200": bool(row.get("price_vs_sma_200", 0) > 0),
        "sectorSource": diagnostic.get("sectorSource", "sp500_constituent"),
        "sectorEtf": diagnostic.get("sectorEtf"),
        "sectorCorrelation": diagnostic.get("sectorCorrelation"),
        "sectorCorrelationObservations": diagnostic.get("sectorCorrelationObservations"),
    }
    if reference_scores is not None:
        payload.update(sp500_comparison(row, reference_scores))
    return payload


def main() -> None:
    args = parse_args()
    model_dir = ROOT / args.model_dir
    model_path = model_dir / f"{args.model_name}.json"
    metadata_path = model_dir / f"{args.model_name}_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    feature_columns = list(metadata["feature_columns"])

    focus_symbols = parse_focus_symbols(args.focus_symbols)
    constituents, constituent_source = load_constituents(args.max_symbols)
    reference_symbols = set(constituents["symbol"].astype(str))
    required_symbols = list(dict.fromkeys([*constituents["symbol"].tolist(), *focus_symbols, "SPY", *SECTOR_ETFS]))
    symbol_frames, failures = fetch_symbol_frames(required_symbols, args.years, args.max_workers)
    missing_context = [symbol for symbol in ["SPY", *SECTOR_ETFS] if symbol not in symbol_frames]
    if missing_context:
        raise SystemExit(f"Missing required model context histories: {missing_context}")

    constituents, sector_diagnostics = append_focus_constituents(constituents, focus_symbols, symbol_frames)
    dataset = assemble_live_features(symbol_frames, constituents, reference_symbols=reference_symbols)
    missing_features = [column for column in feature_columns if column not in dataset.columns]
    if missing_features:
        raise SystemExit(f"Live feature matrix is missing model features: {missing_features}")

    scored = score_rows(dataset, model_path, feature_columns)
    scored["model_universe_count"] = len(scored)
    name_lookup = constituents.set_index("symbol")["name"].to_dict()
    reference_scores = scored[scored["symbol"].isin(reference_symbols)]["model_score"]
    rankings = [row_payload(row, name_lookup, sector_diagnostics) for _, row in scored.iterrows()]
    focus_scored = scored[scored["symbol"].isin(focus_symbols)].copy()
    focus_rankings = [
        row_payload(row, name_lookup, sector_diagnostics, reference_scores=reference_scores)
        for _, row in focus_scored.iterrows()
    ]
    latest_date = scored["date"].max().date().isoformat() if not scored.empty else None
    scored_symbols = set(scored["symbol"].astype(str))
    unscored_symbols = sorted(set(constituents["symbol"].astype(str)) - scored_symbols - set(failures))
    focus_failures = {symbol: failures[symbol] for symbol in focus_symbols if symbol in failures}
    focus_unscored_symbols = sorted(set(focus_symbols) - set(focus_scored["symbol"].astype(str)) - set(focus_failures))

    payload = {
        "status": "ready",
        "generatedAt": pd.Timestamp.now("UTC").isoformat(),
        "asOfDate": latest_date,
        "model": {
            "name": args.model_name,
            "path": str(model_path.relative_to(ROOT)),
            "target": metadata.get("target_column"),
            "returnColumn": metadata.get("return_column"),
            "featureCount": len(feature_columns),
            "numBoostRound": metadata.get("num_boost_round"),
            "trainingEndDate": metadata.get("training_end_date"),
            "constituentSource": constituent_source,
        },
        "scoredCount": len(rankings),
        "requestedSymbolCount": len(constituents),
        "focusSymbols": focus_symbols,
        "focusRankings": focus_rankings,
        "focusFailureCount": len(focus_failures),
        "focusFailures": focus_failures,
        "focusUnscoredSymbols": focus_unscored_symbols,
        "referenceUniverse": "Current S&P 500",
        "referenceUniverseCount": int(len(reference_scores)),
        "sectorDiagnostics": {symbol: sector_diagnostics.get(symbol) for symbol in focus_symbols if symbol in sector_diagnostics},
        "methodologyNotes": [
            "Focus tickers are scored with the same production XGBoost rank model as the S&P 500 dashboard.",
            "S&P 500 rank compares each focus ticker's model score against the current S&P 500 reference scores.",
            "Non-S&P tickers are assigned a sector proxy using trailing daily-return correlation to SPDR sector ETFs.",
            "International or exchange-suffix tickers may be less comparable because price currency, trading calendar, and volume conventions can differ from U.S. common stocks.",
        ],
        "failedSymbolCount": len(failures),
        "unscoredSymbolCount": len(unscored_symbols),
        "unscoredSymbols": unscored_symbols,
        "failures": failures,
        "rankings": rankings,
    }
    output_path = ROOT / args.output
    write_json(payload, output_path)
    print(f"Wrote {len(rankings)} live model rankings to {output_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
