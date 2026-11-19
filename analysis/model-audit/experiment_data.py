"""Versioned experiment matrices built with the shared technical feature engine."""
from __future__ import annotations

import gc
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/modeling"))
from build_training_dataset import enrich_price_features, build_market_context, sector_name_for_etf
from common import SECTOR_ETFS, write_json
from model_features import add_cross_sectional_model_features, model_feature_columns
from score_live_rank_model import build_base_feature_dataset, build_spy_context


def cache_signature(folder: Path, horizon: int, version: int) -> str:
    digest = hashlib.sha256((folder / "manifest.json").read_bytes())
    for name in ("scripts/modeling/build_training_dataset.py", "scripts/modeling/model_features.py",
                 "scripts/modeling/score_live_rank_model.py", "analysis/model-audit/experiment_data.py"):
        digest.update((ROOT / name).read_bytes())
    digest.update(f"{horizon}:{version}".encode())
    return digest.hexdigest()


def build_matrix(folder: Path, horizon: int, version: int) -> None:
    prefix = folder / f"matrix_{horizon}_v{version}"
    metadata_path = prefix.with_suffix(".json")
    signature = cache_signature(folder, horizon, version)
    if metadata_path.exists() and json.loads(metadata_path.read_text())["signature"] == signature:
        return
    manifest = json.loads((folder / "manifest.json").read_text())
    reference = pd.read_csv(folder / "reference.csv")
    as_of = pd.Timestamp(manifest["as_of"])
    start = as_of - pd.DateOffset(years=10 if horizon == 14 else 15)
    prices = folder / "raw/prices"
    spy_raw = pd.read_csv(prices / "SPY.csv", parse_dates=["date"])
    calendar = pd.DatetimeIndex(spy_raw.loc[spy_raw.date.ge(start) & spy_raw.date.le(as_of), "date"])
    frames, quality = {}, []
    for symbol in ["SPY", *SECTOR_ETFS, *reference.symbol]:
        path = prices / f"{symbol}.csv"
        evidence = manifest["histories"].get(symbol, {})
        if not path.exists() or evidence.get("status") == "unresolved":
            quality.append({"symbol": symbol, "status": "missing_history"})
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != evidence["sha256"]:
            raise ValueError(f"Frozen cache hash changed: {symbol}")
        archived = evidence.get("archived_segment")
        if archived:
            path = folder / archived["path"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != archived["sha256"]:
                raise ValueError(f"Archived segment hash changed: {symbol}")
        raw = pd.read_csv(path, parse_dates=["date"])
        if raw.date.duplicated().any() or raw.date.isna().any() or not np.isfinite(raw.close).all() or raw.close.le(0).any():
            raise ValueError(f"Invalid cached prices: {symbol}")
        raw = raw[raw.date.ge(start) & raw.date.le(as_of)]
        if raw.empty:
            quality.append({"symbol": symbol, "status": "no_history_in_window"})
            continue
        sessions = calendar[calendar >= raw.date.min()]
        missing = sessions[sessions <= raw.date.max()].difference(pd.DatetimeIndex(raw.date))
        quality.append({"symbol": symbol, "status": "ended_history_unresolved" if raw.date.max() < as_of else "active_history",
                        "last_price": str(raw.date.max().date()), "archived_original_segment": bool(archived), "observed_provider_end": evidence.get("end"),
                        "missing_sessions": len(missing), "missing_volume": int(raw.volume.isna().sum())})
        # Keep removed names' earlier observations. Missing and post-delisting
        # sessions remain NaN, so shifts cannot silently shorten the horizon.
        aligned = raw.set_index("date").reindex(sessions).rename_axis("date").reset_index()
        aligned["symbol"] = symbol
        enriched = enrich_price_features(aligned, feature_version=version)
        enriched["outcome"] = enriched.close.shift(-(horizon + 1)) / enriched.close.shift(-1) - 1
        frames[symbol] = enriched
    if any(s not in frames for s in ["SPY", *SECTOR_ETFS]):
        raise ValueError("Missing market/sector benchmark")
    breadth, sector = build_market_context(frames, reference)
    base = build_base_feature_dataset(frames, reference, breadth, sector, build_spy_context(frames["SPY"]))
    sector_outcomes = pd.concat([frames[s][["date", "outcome"]].assign(sector=sector_name_for_etf(s)) for s in SECTOR_ETFS])
    base = base.merge(sector_outcomes.rename(columns={"outcome": "sector_outcome"}), on=["date", "sector"], how="left", validate="many_to_one")
    base["excess"] = base["outcome"] - base["sector_outcome"] - .0015
    eligible_calendar = calendar[252:-(horizon + 1)]
    if horizon == 252:
        # Global, predeclared monthly anchors; purging still uses full sessions.
        anchors = pd.Series(eligible_calendar).groupby(eligible_calendar.to_period("M")).first()
        base = base[base.date.isin(anchors)].copy()
    else:
        base = base[base.date.isin(eligible_calendar)].copy()
    del frames, breadth, sector
    gc.collect()
    dataset = add_cross_sectional_model_features(base, round_trip_cost=None)
    del base
    features = model_feature_columns(dataset)
    matrix = dataset[features].to_numpy(dtype=np.float32)
    complete = np.isfinite(matrix).all(axis=1)
    known = np.isfinite(dataset.excess.to_numpy())
    quality_summary = {"feature_complete_rows": int(complete.sum()), "unresolved_outcome_rows": int((complete & ~known).sum()), "histories": quality}
    selected = complete
    rows = dataset.loc[selected, ["date", "symbol", "sector", "outcome", "excess", "ret_60d", "volatility_60d"]].reset_index(drop=True)
    if rows.duplicated(["date", "symbol"]).any():
        raise ValueError("Duplicate experiment keys")
    np.save(prefix.with_suffix(".npy"), matrix[selected], allow_pickle=False)
    rows.to_csv(prefix.with_suffix(".csv"), index=False)
    write_json({"signature": signature, "feature_version": version, "features": features, "rows": len(rows), "horizon": horizon,
                "calendar": eligible_calendar.strftime("%Y-%m-%d").tolist(), "quality": quality_summary,
                "raw_window_start": str(start.date()), "as_of": manifest["as_of"]}, metadata_path)
    print(f"Built {horizon}D v{version}: {len(rows):,} feature-complete rows; {quality_summary['unresolved_outcome_rows']} unresolved outcomes retained before ranking", flush=True)


def load_pair(folder: Path, horizon: int):
    matrices, rows, metadata = {}, {}, {}
    for version in (1, 2):
        build_matrix(folder, horizon, version)
        prefix = folder / f"matrix_{horizon}_v{version}"
        matrices[version] = np.load(prefix.with_suffix(".npy"), mmap_mode="r", allow_pickle=False)
        rows[version] = pd.read_csv(prefix.with_suffix(".csv"), parse_dates=["date"])
        metadata[version] = json.loads(prefix.with_suffix(".json").read_text())
    keys = ["date", "symbol"]
    common = rows[1][keys].merge(rows[2][keys], on=keys, validate="one_to_one").sort_values(keys)
    indexes = {v: pd.MultiIndex.from_frame(rows[v][keys]).get_indexer(pd.MultiIndex.from_frame(common)) for v in (1, 2)}
    frame = rows[1].iloc[indexes[1]].reset_index(drop=True)
    if not np.allclose(frame.excess, rows[2].iloc[indexes[2]].excess, equal_nan=True):
        raise ValueError("Feature variants changed economic outcomes")
    percentile = frame.groupby("date").excess.rank(pct=True)
    frame["grade"] = np.select([percentile.ge(.9), percentile.ge(.8), percentile.le(.1), percentile.le(.2)], [4, 3, 0, 1], default=2)
    frame["grade"] = frame["grade"].where(frame.excess.notna())
    return frame, {v: matrices[v][indexes[v]] for v in (1, 2)}, metadata
