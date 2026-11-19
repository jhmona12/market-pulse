"""Freeze public historical data for audit; never execute upstream code."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

from experiment_data import ROOT
from common import write_json

REPOSITORY = "Johnbrick123/sp500-data"
ASSETS = {"prices.parquet", "membership_intervals.parquet", "members.parquet", "renames.csv",
          "no_trade_days.csv", "exit_gaps.csv", "recycled_tickers.csv", "no_data_tickers.txt",
          "full_check_drift.csv", "full_check_flagged.csv", "full_check_adjudicated.csv", "dry_verify.log"}


def fetch(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "MarketPulse-Research/1.0"}), timeout=120)


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = ROOT / args.output
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "archive-manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
    else:
        with fetch(f"https://api.github.com/repos/{REPOSITORY}/releases/tags/data") as stream:
            release = json.load(stream)
        assets = {a["name"]: a for a in release["assets"] if a["name"] in ASSETS}
        if set(assets) != ASSETS:
            raise ValueError(f"Missing audit assets: {ASSETS - set(assets)}")
        for name, record in assets.items():
            if not str(record.get("digest", "")).startswith("sha256:"):
                raise ValueError(f"Missing publisher hash: {name}")
        with fetch(f"https://api.github.com/repos/{REPOSITORY}/commits/main") as stream:
            commit = json.load(stream)["sha"]
        manifest = {"repository": REPOSITORY, "commit": commit, "release": release["html_url"],
                    "assets": assets, "policy": "Public community archive under independent audit, not verified institutional data."}
        write_json(manifest, manifest_path)
    for name, record in manifest["assets"].items():
        path = output / name
        expected = record["digest"].removeprefix("sha256:")
        if not path.exists():
            print(f"Downloading {name}: {record['size']:,} bytes", flush=True)
            temporary = path.with_suffix(path.suffix + ".partial")
            with fetch(record["browser_download_url"]) as response, temporary.open("wb") as target:
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    target.write(chunk)
            if sha(temporary) != expected:
                raise ValueError(f"Release asset changed during download: {name}; do not mix snapshots")
            temporary.replace(path)
        if sha(path) != expected:
            raise ValueError(f"Frozen asset hash changed: {name}")
    # Supplementary evidence is pinned to a commit, not a moving branch.
    for name in ("README.md", "series_end.csv", "series_start.csv", "series_splices.csv", "corporate_action_overrides.csv", "spinoffs.csv"):
        path = output / name
        if not path.exists():
            with fetch(f"https://raw.githubusercontent.com/{REPOSITORY}/{manifest['commit']}/{name}") as response:
                path.write_bytes(response.read())
        previous = manifest.setdefault("evidence_hashes", {}).get(name)
        digest = sha(path)
        if previous and previous != digest:
            raise ValueError(f"Frozen evidence changed: {name}")
        manifest["evidence_hashes"][name] = digest
    write_json(manifest, manifest_path)
    print(f"Frozen archive: {output}", flush=True)


if __name__ == "__main__":
    main()
