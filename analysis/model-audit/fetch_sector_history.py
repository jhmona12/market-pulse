"""Freeze monthly archived IVV classifications, not today's sectors backcast."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pandas as pd

from experiment_data import ROOT
from fetch_historical_archive import fetch, sha
from common import write_json

REPO = "riazarbi/sp500-scraper"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--start", default="2010-10-01")
    parser.add_argument("--as-of", default="2026-11-16")
    args = parser.parse_args()
    output = ROOT / args.output
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if (manifest["start"], manifest["as_of"]) != (args.start, args.as_of):
            raise ValueError("Use a separate directory for another date window")
    else:
        with fetch(f"https://api.github.com/repos/{REPO}/commits/main") as response:
            commit = json.load(response)["sha"]
        with fetch(f"https://api.github.com/repos/{REPO}/git/trees/{commit}?recursive=1") as response:
            tree = json.load(response)
        if tree.get("truncated"):
            raise ValueError("Truncated sector archive listing")
        paths = [r["path"] for r in tree["tree"] if r["path"].startswith("ishares/sp500/csv/") and r["path"].endswith(".csv")]
        monthly = {}
        for path in sorted(paths):
            date = pd.Timestamp(Path(path).stem)
            if pd.Timestamp(args.start) <= date <= pd.Timestamp(args.as_of):
                monthly[str(date.to_period("M"))] = path
        manifest = {"repository": REPO, "commit": commit, "start": args.start, "as_of": args.as_of,
                    "paths": list(monthly.values()), "files": {}, "source": "Archived iShares IVV historical holdings; classifications are observed as-of snapshots, not exact reclassification notices."}
        write_json(manifest, manifest_path)
    def download(path):
        target = output / Path(path).name
        if not target.exists():
            with fetch(f"https://raw.githubusercontent.com/{REPO}/{manifest['commit']}/{path}") as response:
                content = response.read()
            temp = target.with_suffix(".partial")
            temp.write_bytes(content)
            temp.replace(target)
        digest = sha(target)
        expected = manifest["files"].get(path)
        if expected and expected["sha256"] != digest:
            raise ValueError(f"Sector snapshot changed: {path}")
        frame = pd.read_csv(target)
        if not {"symbol", "sector", "ISIN", "date"}.issubset(frame) or len(frame) < 450:
            raise ValueError(f"Invalid sector snapshot: {path}")
        dates = pd.to_datetime(frame.date).dropna().unique()
        if len(dates) != 1 or pd.Timestamp(dates[0]) != pd.Timestamp(target.stem):
            raise ValueError(f"Sector file mislabeled as another date: {path}")
        return path, {"sha256": digest, "rows": len(frame)}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for index, (path, record) in enumerate(pool.map(download, manifest["paths"]), 1):
            manifest["files"][path] = record
            if index % 20 == 0:
                write_json(manifest, manifest_path)
                print(f"Sector snapshots: {index}/{len(manifest['paths'])}", flush=True)
    write_json(manifest, manifest_path)
    print(f"Frozen {len(manifest['files'])} monthly snapshots", flush=True)


if __name__ == "__main__":
    main()
