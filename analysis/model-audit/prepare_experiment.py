"""Freeze a separate, resumable research cache without changing live artifacts."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import shutil

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/modeling"))
from common import PRICES_DIR, REFERENCE_DIR, SECTOR_ETFS, fetch_sp500_constituents, fetch_yahoo_history, write_json
from build_sp500_point_in_time_universe import fetch_changes


def cache_history(symbol, folder, start, end, as_of, expected=None):
    path = folder / f"{symbol}.csv"
    if path.exists():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected and expected.get("sha256"):
            if digest != expected["sha256"]:
                raise ValueError(f"Frozen cache hash changed: {symbol}; use a new experiment directory")
            return symbol, dict(expected)
        frame = pd.read_csv(path, parse_dates=["date"])
        return symbol, {"status": "cached", "rows": len(frame), "end": str(frame.date.max().date()), "sha256": digest}
    if expected and expected.get("sha256"):
        raise ValueError(f"Frozen cache file missing: {symbol}; do not silently refetch revised history")
    error = None
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            frame = fetch_yahoo_history(symbol, start, end, host=host)
            frame["date"] = pd.to_datetime(frame["date"])
            frame = frame[frame.date.le(pd.Timestamp(as_of))].copy()
            if frame.empty:
                raise ValueError("No prices through requested as-of date")
            temporary = path.with_suffix(".tmp")
            frame.to_csv(temporary, index=False)
            temporary.replace(path)
            return symbol, {"status": "fetched", "rows": len(frame), "start": str(frame.date.min().date()), "end": str(frame.date.max().date()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            time.sleep(1)
    return symbol, {"status": "unresolved", "error": error}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", required=True, help="Latest completed exchange session, YYYY-MM-DD")
    parser.add_argument("--output", required=True)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    folder = ROOT / args.output
    prices = folder / "raw/prices"
    prices.mkdir(parents=True, exist_ok=True)
    start = datetime.fromisoformat(args.as_of).replace(tzinfo=timezone.utc) - timedelta(days=16 * 366)
    end = datetime.fromisoformat(args.as_of).replace(tzinfo=timezone.utc) + timedelta(days=1)
    reference_path = folder / "reference.csv"
    if reference_path.exists():
        reference = pd.read_csv(reference_path)
    else:
        current = fetch_sp500_constituents()
        original = pd.read_csv(REFERENCE_DIR / "sp500_constituents.csv")
        current.to_csv(folder / "current_members.csv", index=False)
        original.to_csv(folder / "original_members.csv", index=False)
        reference = pd.concat([original, current]).drop_duplicates("symbol", keep="first")
        reference["in_original_snapshot"] = reference.symbol.isin(original.symbol)
        reference["in_current_snapshot"] = reference.symbol.isin(current.symbol)
        reference.to_csv(reference_path, index=False)
    changes_path = folder / "membership_candidates.csv"
    if not changes_path.exists() or changes_path.stat().st_size < 10:
        changes, source_url, html = fetch_changes()
        changes.to_csv(changes_path, index=False)
        write_json({"source": source_url, "fetched_at": datetime.now(timezone.utc).isoformat(), "html_sha256": hashlib.sha256(html.encode()).hexdigest(), "status": "discovery_only_not_verified_security_membership"}, folder / "membership_source.json")
    symbols = list(dict.fromkeys(["SPY", *SECTOR_ETFS, *reference.symbol]))
    manifest = {"as_of": args.as_of, "requested_start": start.isoformat(), "reference_sha256": hashlib.sha256(reference_path.read_bytes()).hexdigest(), "histories": {},
        "universe_policy": "Frozen original/current member union for controlled experiments; NOT point-in-time or survivorship-free. Missing outcomes remain unresolved."}
    manifest_path = folder / "manifest.json"
    previous = {}
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text())
        if previous["as_of"] != args.as_of or previous["reference_sha256"] != manifest["reference_sha256"]:
            raise ValueError("Cache identity differs; use a new experiment directory")
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 4))) as pool:
        futures = [pool.submit(cache_history, s, prices, start, end, args.as_of, previous.get("histories", {}).get(s)) for s in symbols]
        for i, future in enumerate(as_completed(futures), 1):
            symbol, result = future.result()
            manifest["histories"][symbol] = result
            if i % 25 == 0 or i == len(symbols):
                write_json(manifest, manifest_path)
                print(f"Cached {i}/{len(symbols)} histories; unresolved {sum(v['status']=='unresolved' for v in manifest['histories'].values())}", flush=True)
    archived = folder / "raw/archived_prices"
    archived.mkdir(exist_ok=True)
    for row in reference.to_dict("records"):
        symbol = row["symbol"]
        record = manifest["histories"][symbol]
        source = PRICES_DIR / f"{symbol}.csv"
        if row["in_original_snapshot"] and not row["in_current_snapshot"] and record.get("rows", 0) < 252 and source.exists():
            target = archived / source.name
            if not target.exists():
                if record.get("archived_segment"):
                    raise ValueError(f"Frozen archived segment missing: {symbol}")
                shutil.copyfile(source, target)
            if record.get("archived_segment") and hashlib.sha256(target.read_bytes()).hexdigest() != record["archived_segment"]["sha256"]:
                raise ValueError(f"Frozen archived segment hash changed: {symbol}")
            old = pd.read_csv(target)
            if len(old) > record.get("rows", 0):
                record["archived_segment"] = {"path": str(target.relative_to(folder)), "rows": len(old), "end": old.date.max(),
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "policy": "Original snapshot security only; no stitching to later quotes or recycled tickers"}
    write_json(manifest, manifest_path)
    required = ["SPY", *SECTOR_ETFS]
    if any(manifest["histories"][s].get("end") != args.as_of for s in required):
        raise SystemExit("Incomplete benchmark histories; manifest preserved, experiment must not train")
    print(f"Frozen cache: {folder}", flush=True)


if __name__ == "__main__":
    main()
