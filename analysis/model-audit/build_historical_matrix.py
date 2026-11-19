"""Build PIT-eligible research matrices from independently frozen archives."""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from experiment_data import ROOT
from common import SECTOR_ETFS, write_json
from build_training_dataset import enrich_price_features, build_market_context, sector_name_for_etf
from score_live_rank_model import build_base_feature_dataset, build_spy_context
from model_features import add_cross_sectional_model_features, model_feature_columns, RAW_PRICE_FEATURE_COLUMNS, SECTOR_CONTEXT_FEATURE_COLUMNS
from schema import feature_matches_group
from historical_universe import member_mask, security_candidates, resolve_sector_identity, dated_sectors, normalize_bars, forward_outcomes, outcome_bounds, apply_reviewed_price_corrections
from fetch_historical_archive import sha
from outcome_diagnostics import no_trade_calendar
from price_quarantine import unresolved_flags, quarantine_bars, quarantine_exposure


def load_archive(folder):
    archive = folder / "archive"
    manifest = json.loads((archive / "archive-manifest.json").read_text())
    for name, record in manifest["assets"].items():
        if sha(archive / name) != record["digest"].removeprefix("sha256:"):
            raise ValueError(f"Frozen archive changed: {name}")
    return manifest


def sector_observations(folder, intervals, horizon):
    manifest = json.loads((folder / "sectors/manifest.json").read_text())
    renames = list(pd.read_csv(folder / "archive/renames.csv").itertuples(index=False, name=None))
    records, unmatched, snapshots, isin_labels = [], [], [], {}
    aliases = {"Technology": "Information Technology", "Communication": "Communication Services", "Consumer Cyclical": "Consumer Discretionary",
               "Telecommunications": "Telecommunication Services"}
    for path, evidence in manifest["files"].items():
        local = folder / "sectors" / Path(path).name
        if sha(local) != evidence["sha256"]:
            raise ValueError(f"Sector history changed: {path}")
        date = pd.Timestamp(local.stem)
        active = tuple(intervals.loc[intervals.start.le(date) & (intervals.end.isna() | intervals.end.gt(date)), "ticker"].unique().tolist())
        data = pd.read_csv(local, keep_default_na=False)
        data = data[data.asset_class.eq("Equity")]
        snapshots.append((path, date, active, data))
        # ISIN links are identity evidence only. Sector observations remain
        # dated and can never be backfilled from a later holdings snapshot.
        for row in data.itertuples():
            labels = security_candidates(row.symbol, active, renames)
            if len(labels) == 1 and row.ISIN not in ("", "-"):
                isin_labels.setdefault(row.ISIN, set()).add(labels[0])
    identities = json.loads((ROOT / "config/research-security-identities.json").read_text())
    evidence = pd.read_csv(folder / "sectors" / identities["evidence_file"], keep_default_na=False)
    for identity in identities["identities"]:
        if not ((evidence.ISIN == identity["isin"]) & (evidence.name == identity["holding_name"])).any():
            raise ValueError(f"Identity evidence mismatch: {identity}")
        isin_labels[identity["isin"]] = {identity["security_id"]}
    for path, date, active, data in snapshots:
        for row in data.itertuples():
            labels = resolve_sector_identity(row.symbol, row.ISIN, active, renames, isin_labels)
            if row.sector in ("", "-"):
                labels = []
            if len(labels) == 1:
                records.append({"symbol": labels[0], "date": date, "sector": aliases.get(row.sector, row.sector),
                                "isin": row.ISIN, "source_symbol": row.symbol, "source_file": path})
            else:
                unmatched.append({"date": date, "symbol": row.symbol, "name": row.name, "candidates": "|".join(labels)})
    result = pd.DataFrame(records)
    # Multiple share lines with conflicting sectors are not resolved by row order.
    conflicts = result.groupby(["symbol", "date"]).sector.nunique()
    if conflicts.gt(1).any():
        raise ValueError("Conflicting historical sector matches")
    result = result.drop_duplicates(["symbol", "date"])
    result.to_csv(folder / f"sector-observations-{horizon}d.csv", index=False)
    pd.DataFrame(unmatched).to_csv(folder / f"sector-unmatched-{horizon}d.csv", index=False)
    return result


def build(folder, horizon, feature_policy, output_root=None, quarantine_ledger=None):
    manifest = load_archive(folder)
    as_of = pd.read_parquet(folder / "archive/prices.parquet", columns=["date"], filters=[("ticker", "==", "SPY")]).date.max()
    start = as_of - pd.DateOffset(years=10 if horizon == 14 else 15)
    raw = pd.read_parquet(folder / "archive/prices.parquet", filters=[("date", ">=", start), ("date", "<=", as_of)])
    all_intervals = pd.read_parquet(folder / "archive/membership_intervals.parquet")
    intervals = all_intervals
    intervals = intervals[(intervals.end.isna() | intervals.end.gt(start)) & intervals.start.le(as_of)].copy()
    symbols = sorted(intervals.ticker.unique())
    if not 450 <= len(symbols) <= 2000:
        raise ValueError(f"Unexpected historical-universe size: {len(symbols)}")
    observations = sector_observations(folder, all_intervals, horizon)
    sector_groups = {s: g for s, g in observations.groupby("symbol")}
    symbol_raw = {s: g for s, g in raw.groupby("ticker") if s in set(symbols) | {"SPY", *SECTOR_ETFS, "IYZ"}}
    review_path = ROOT / "config/research-price-reviews.json"
    reviews = json.loads(review_path.read_text())["reviews"]
    for symbol in {r["security_id"] for r in reviews if r["action"] == "correct_dividend"}:
        if symbol in symbol_raw:
            symbol_raw[symbol] = apply_reviewed_price_corrections(symbol_raw[symbol], [r for r in reviews if r["security_id"] == symbol])
    calendar = pd.DatetimeIndex(symbol_raw["SPY"].date.sort_values())
    flags = unresolved_flags(pd.read_csv(quarantine_ledger)) if quarantine_ledger else {}
    if set(flags) & {"SPY", *SECTOR_ETFS, "IYZ"}:
        raise ValueError("Benchmark quarantine needs a separate market-context policy")
    if calendar[-1] != as_of:
        raise ValueError("Benchmark is not at the frozen archive as-of date")
    no_trades = pd.read_csv(folder / "archive/no_trade_days.csv", parse_dates=["date"])
    halt_path = ROOT / "config/research-no-trade-periods.json"
    no_trade_by_symbol = no_trade_calendar(calendar, no_trades, pd.read_csv(folder / "archive/exit_gaps.csv"),
        json.loads(halt_path.read_text())["periods"])
    event_path = ROOT / "config/research-terminal-events.json"
    events = {e["security_id"]: e for e in json.loads(event_path.read_text())["events"]}
    frames, sector_history, masks, audit, past_masks = {}, {}, {}, [], {}
    expected_symbols = ["SPY", *SECTOR_ETFS, "IYZ", *symbols]
    for index, symbol in enumerate(expected_symbols):
        prices = symbol_raw.get(symbol)
        if prices is None:
            raise ValueError(f"Historical security missing all prices: {symbol}")
        bars = normalize_bars(prices, no_trade_by_symbol.get(symbol, [])).set_index("date").reindex(calendar).rename_axis("date").reset_index()
        past, future = quarantine_exposure(calendar, flags.get(symbol, []), horizon)
        past_masks[symbol] = past
        bars = quarantine_bars(bars, flags.get(symbol, []), calendar)
        bars["symbol"] = symbol
        frame = enrich_price_features(bars, feature_version=2)
        event = events.get(symbol)
        outcome, available, resolved = forward_outcomes(prices, calendar, horizon, security_id=symbol, as_of=as_of,
            event_record=event, successor_raw=symbol_raw.get((event or {}).get("successor")), no_trade_dates=no_trade_by_symbol.get(symbol, []))
        frame["outcome"], frame["label_available_at"], frame["terminal_resolved"] = outcome.to_numpy(), available.to_numpy(), resolved
        low, high = outcome_bounds(prices, calendar, horizon, outcome=outcome, event_record=event,
            security_id=symbol, as_of=as_of, successor_raw=symbol_raw.get((event or {}).get("successor")), no_trade_dates=no_trade_by_symbol.get(symbol, []))
        frame["outcome_lower"], frame["outcome_upper"] = low.to_numpy(), high.to_numpy()
        frame.loc[future, ["outcome", "outcome_lower", "outcome_upper"]] = np.nan
        frame.loc[future, "label_available_at"] = pd.NaT
        frame.loc[future, "terminal_resolved"] = False
        frame["price_quality_unknown_outcome"] = future
        keep = ["date", "symbol", "close", "outcome", "outcome_lower", "outcome_upper", "label_available_at", "terminal_resolved", "price_quality_unknown_outcome", "forward_return_14d",
                "forward_return_14d_next_close", "max_drawdown_14d_next_close", *RAW_PRICE_FEATURE_COLUMNS]
        frames[symbol] = frame[[c for c in dict.fromkeys(keep) if c in frame]].copy()
        if symbol in symbols:
            mask = member_mask(calendar, intervals[intervals.ticker.eq(symbol)])
            masks[symbol] = mask
            sector_history[symbol] = dated_sectors(calendar, sector_groups.get(symbol, pd.DataFrame()), max_age_days=366)
            audit.append({"symbol": symbol, "membership_days": int(mask.sum()), "tradable_member_days": int((mask & frame.close.notna()).sum()),
                "missing_member_volume": int((mask & bars.volume.isna()).sum()), "unknown_sector_days": int((mask & sector_history[symbol].sector.isna()).sum()),
                "sector_older_than_62_days": int((mask & sector_history[symbol].sector.notna() & (calendar - sector_history[symbol].sector_observed_at).dt.days.gt(62)).sum()),
                "terminal_labels_resolved": int((mask & resolved).sum()), "last_observed_price": str(prices.date.max().date())})
        if index % 100 == 0:
            print(f"{horizon}D features: {index}/{len(expected_symbols)}", flush=True)
    del raw
    gc.collect()
    reference = pd.DataFrame({"symbol": symbols, "sector": "dated_observations"})
    # Membership filtering precedes breadth, percentile ranks and sector medians.
    context_frames = {s: f.loc[masks[s] & ~past_masks[s]] if s in masks else f for s, f in frames.items() if s != "IYZ"}
    breadth, sectors = build_market_context(context_frames, reference)
    extra = frames["IYZ"][["date", *[c.removeprefix("sector_") for c in SECTOR_CONTEXT_FEATURE_COLUMNS]]].copy()
    extra = extra.rename(columns={c.removeprefix("sector_"): c for c in SECTOR_CONTEXT_FEATURE_COLUMNS})
    extra["sector"] = "Telecommunication Services"
    sectors = pd.concat([sectors, extra], ignore_index=True)
    spy = build_spy_context(frames["SPY"])
    sector_outcomes = pd.concat([frames[s][["date", "outcome"]].assign(sector=sector_name_for_etf(s) if s != "IYZ" else "Telecommunication Services") for s in [*SECTOR_ETFS, "IYZ"]])
    eligible_calendar = calendar[252:-(horizon + 1)]
    active_counts = np.sum(np.stack(list(masks.values())), axis=0)
    if ((active_counts[252:] < 450) | (active_counts[252:] > 550)).any():
        raise ValueError("Daily historical membership count is implausible")
    signal_dates = eligible_calendar
    if horizon == 252:
        signal_dates = pd.DatetimeIndex(pd.Series(eligible_calendar).groupby(eligible_calendar.to_period("M")).first())
    pieces = []
    for symbol in symbols:
        base = build_base_feature_dataset({symbol: frames[symbol]}, reference[reference.symbol.eq(symbol)], breadth, sectors, spy,
                                          sector_history={symbol: sector_history[symbol]})
        base = base.loc[masks[symbol] & ~past_masks[symbol] & base.date.isin(signal_dates)].copy()
        pieces.append(base)
    base = pd.concat(pieces, ignore_index=True)
    del frames, pieces, context_frames, symbol_raw
    gc.collect()
    base = base.merge(sector_outcomes.rename(columns={"outcome": "sector_outcome"}), on=["date", "sector"], how="left", validate="many_to_one")
    base["excess"] = base.outcome - base.sector_outcome - .0015
    base["excess_lower"] = base.outcome_lower - base.sector_outcome - .0015
    base["excess_upper"] = base.outcome_upper - base.sector_outcome - .0015
    dataset = add_cross_sectional_model_features(base, round_trip_cost=None)
    del base
    gc.collect()
    all_features = model_feature_columns(dataset)
    features = [c for c in all_features if feature_policy != "price_only" or not feature_matches_group(c, "liquidity")]
    matrix = dataset[features].to_numpy(dtype=np.float32)
    complete = np.isfinite(matrix).all(axis=1)
    full_complete = np.isfinite(dataset[all_features].to_numpy(dtype=np.float32)).all(axis=1)
    selected = dataset.loc[complete, ["date", "symbol", "sector", "outcome", "excess", "outcome_lower", "outcome_upper", "excess_lower", "excess_upper", "ret_60d", "volatility_60d", "label_available_at", "terminal_resolved", "price_quality_unknown_outcome"]].reset_index(drop=True)
    if selected.duplicated(["symbol", "date"]).any():
        raise ValueError("Duplicate historical observations")
    coverage = dataset.assign(feature_complete=complete, full_feature_complete=full_complete, known_label=np.isfinite(dataset.excess)).groupby("date").agg(
        eligible_rows=("symbol", "size"), feature_complete=("feature_complete", "sum"), full_feature_complete=("full_feature_complete", "sum"), known_labels=("known_label", "sum"))
    output = (output_root or folder) / feature_policy
    output.mkdir(parents=True, exist_ok=True)
    prefix = output / f"matrix_{horizon}_v2"
    np.save(prefix.with_suffix(".npy"), matrix[complete], allow_pickle=False)
    selected.to_csv(prefix.with_suffix(".csv"), index=False)
    pd.DataFrame(audit).to_csv(output / f"coverage_{horizon}d_by_security.csv", index=False)
    coverage.to_csv(output / f"coverage_{horizon}d_by_date.csv")
    unresolved = dataset.loc[complete & dataset.excess.isna(), ["symbol", "date", "sector", "outcome", "sector_outcome"]]
    unresolved.to_csv(output / f"unresolved_{horizon}d.csv", index=False)
    quality = {"historical_securities": len(symbols), "feature_complete_symbols": int(selected.symbol.nunique()),
        "eligible_signal_rows": len(dataset), "feature_complete_rows": len(selected), "full_feature_complete_rows": int(full_complete.sum()),
        "unresolved_outcome_rows": int(selected.excess.isna().sum()), "resolved_terminal_rows": int(selected.terminal_resolved.sum()),
        "price_quality_unknown_outcome_rows": int(selected.price_quality_unknown_outcome.sum()),
        "bounded_not_point_labels": int((selected.excess.isna() & selected.excess_lower.notna() & selected.excess_upper.notna()).sum()),
        "unknown_sector_signal_rows": int(dataset.sector.isna().sum()), "minimum_date_coverage": float((coverage.feature_complete/coverage.eligible_rows).min()),
        "mean_date_coverage": float((coverage.feature_complete/coverage.eligible_rows).mean())}
    write_json({"feature_version": 2, "features": features, "rows": len(selected), "horizon": horizon, "calendar": eligible_calendar.strftime("%Y-%m-%d").tolist(),
        "quarantine_policy": {"enabled": bool(quarantine_ledger), "ledger_sha256": sha(quarantine_ledger) if quarantine_ledger else None,
            "flagged_observations": sum(map(len, flags.values())), "feature_lookback_sessions": 252,
            "future_outcomes": "Unknown if holding window spans disputed return; not an ex-ante exclusion"},
        "quality": quality, "raw_window_start": str(start.date()), "as_of": str(as_of.date()), "feature_policy": feature_policy,
        "universe_policy": "Historical interval eligibility before breadth/ranks; continue observed post-removal outcomes; unresolved claims retained.",
        "sector_policy": "Last observed archived IVV sector, lagged one session, expiry 366 calendar days; classifications (not prices) carried through 2017 archive gap, ages audited; no future backfill. Legacy telecom benchmark IYZ.",
        "source_hashes": {"archive": sha(folder / "archive/archive-manifest.json"), "sector": sha(folder / "sectors/manifest.json"), "events": sha(event_path), "identities": sha(ROOT / "config/research-security-identities.json"), "no_trade_periods": sha(halt_path), "price_reviews": sha(review_path)},
        "promotion_status": "research_only_pending_coverage_and_terminal_reconciliation"}, prefix.with_suffix(".json"))
    print(json.dumps(quality, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--horizon", type=int, choices=[14, 252], required=True)
    parser.add_argument("--feature-policy", choices=["full", "price_only"], default="price_only")
    parser.add_argument("--output-root", help="Separate rebuild directory, preserving earlier research artifacts")
    parser.add_argument("--quarantine-ledger", help="Frozen reviewed price-disagreement CSV; unresolved rows only")
    args = parser.parse_args()
    if args.quarantine_ledger and not args.output_root:
        parser.error("Quarantine requires a separate --output-root")
    build(ROOT / args.experiment, args.horizon, args.feature_policy, ROOT / args.output_root if args.output_root else None,
          ROOT / args.quarantine_ledger if args.quarantine_ledger else None)
