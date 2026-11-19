"""Offline production-path replay using real frozen prices, with isolated outputs."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import numpy as np
import pandas as pd

from experiment_data import ROOT
from prepare_experiment import cache_history
from tune_fixed_candidates import file_hash
from common import write_json
import score_live_rank_model as live


def assert_rankings(payload, as_of):
    rows = payload["rankings"]
    assert payload["asOfDate"] == as_of and payload["status"] == "ready"
    assert len({r["symbol"] for r in rows}) == len(rows) >= 450
    assert [r["modelRank"] for r in rows] == list(range(1, len(rows) + 1))
    assert all(np.isfinite(r["modelScore"]) and 0 <= r["modelPercentile"] <= 100 for r in rows)
    for row in rows:
        assert row["asOfDate"] == as_of
        assert row["close"] > 0
        for key in ("beta60d", "return7", "return14", "return30", "return60", "return90", "ytdReturn"):
            assert row.get(key) is not None and np.isfinite(row[key]), (row["symbol"], key)
        needs_stop = row["modelRank"] <= int(np.ceil(len(rows)/10)) or row["setupType"] in {
            "momentum_confirmed", "model_rebound_watch", "model_ranked_not_momentum_confirmed"}
        if needs_stop:
            assert row.get("stopSellPrice", 0) > 0, row["symbol"]
        if row["setupType"] == "model_rebound_watch":
            assert row.get("reboundActivationPrice", 0) > row["close"]
        else:
            assert row.get("reboundActivationPrice") is None
    return len(rows)


def run(folder, incumbents=False, candidate_root=None, output_dir=None):
    feature_version = 1 if incumbents else 2
    output = output_dir or folder / ("deployment-incumbents" if incumbents else "deployment")
    output.mkdir(parents=True, exist_ok=True)
    supplemental = folder / "deployment/prices"
    supplemental.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((folder / "manifest.json").read_text())
    as_of = manifest["as_of"]
    reference = pd.read_csv(folder / "current_members.csv")
    start = pd.Timestamp(as_of, tz="UTC") - pd.DateOffset(years=3)
    end = pd.Timestamp(as_of, tz="UTC") + pd.Timedelta(days=1)
    # Only the five additional market-strip ETFs are absent from the study cache.
    supplement_records = {}
    for symbol in live.MARKET_STRIP_SYMBOLS:
        if not (folder / "raw/prices" / f"{symbol}.csv").exists():
            _, record = cache_history(symbol, supplemental, start, end, as_of)
            if record["status"] == "unresolved" or record.get("end") != as_of:
                raise ValueError(f"Missing supplemental history: {symbol}: {record}")
            supplement_records[symbol] = record
    write_json(supplement_records, output / "supplemental_manifest.json")
    def frozen_provider(symbol, requested_start, requested_end, **kwargs):
        path = folder / "raw/prices" / f"{symbol}.csv"
        evidence = manifest["histories"].get(symbol)
        if not path.exists():
            path = supplemental / f"{symbol}.csv"
            evidence = supplement_records[symbol]
        if file_hash(path) != evidence["sha256"]:
            raise ValueError(f"Frozen raw history changed: {symbol}")
        frame = pd.read_csv(path, parse_dates=["date"])
        return frame[frame.date.ge(start.tz_localize(None)) & frame.date.le(pd.Timestamp(as_of))].copy()
    original_diagnostic = live.record_price_readiness
    def isolated_diagnostic(*args, **kwargs):
        kwargs["directory"] = output / "diagnostics"
        return original_diagnostic(*args, **kwargs)
    candidate_paths = {h: (candidate_root or folder) / f"tuning_{h}d/xgboost_rank_sector{h}_v2_candidate.json" for h in (14, 252)}
    if incumbents:
        candidate_paths = {14: ROOT / "models/rank/xgboost_rank_sector14_tuned.json",
                           252: ROOT / "models/long-horizon/xgboost_rank_sector252_15y_monthly_tuned_research.json"}
    cache_path = output / f"reference-cache-v{feature_version}.json"
    env = {"EXPECTED_MARKET_DATA_DATE": as_of, "MODEL_DATA_READINESS_ATTEMPTS": "1", "MODEL_DATA_READINESS_DELAY_SECONDS": "0"}
    captured = {}
    actual_score_rows = live.score_rows
    def capture(*args, **kwargs):
        result = actual_score_rows(*args, **kwargs)
        captured["dataset"] = args[0]
        return result
    tactical_path = output / "tactical-scores.json"
    argv = ["score_live_rank_model.py", "--model-dir", str(candidate_paths[14].parent.relative_to(ROOT)),
        "--model-name", candidate_paths[14].stem, "--reference-cache", str(cache_path.relative_to(ROOT)),
        "--output", str(tactical_path.relative_to(ROOT)), "--no-reference-cache", "--max-workers", "2"]
    with patch.dict(os.environ, env), patch.object(sys, "argv", argv), \
         patch.object(live, "fetch_sp500_constituents", return_value=reference), \
         patch.object(live, "fetch_yahoo_history", side_effect=frozen_provider), \
         patch.object(live, "record_price_readiness", side_effect=isolated_diagnostic), \
         patch.object(live, "score_rows", side_effect=capture):
        live.main()
    tactical = json.loads(tactical_path.read_text())
    counts = {14: assert_rankings(tactical, as_of)}
    checks = []
    # Exercise the real subprocess CLI for both cache consumers, with no provider mocks.
    for horizon, path in candidate_paths.items():
        target = output / ("tactical-cached.json" if horizon == 14 else "strategic-scores.json")
        command = [sys.executable, str(ROOT / "scripts/modeling/score_live_rank_model.py"),
            "--model-dir", str(path.parent.relative_to(ROOT)), "--model-name", path.stem,
            "--reference-cache", str(cache_path.relative_to(ROOT)), "--score-reference-cache", "--output", str(target.relative_to(ROOT))]
        subprocess.run(command, cwd=ROOT, env={**os.environ, **env}, check=True)
        payload = json.loads(target.read_text())
        counts[horizon] = assert_rankings(payload, as_of)
        metadata = json.loads(path.with_name(path.stem + "_metadata.json").read_text())
        direct = actual_score_rows(captured["dataset"], path, metadata["feature_columns"], scoring_date=pd.Timestamp(as_of), feature_version=feature_version)
        direct_scores = direct.set_index("symbol").model_score
        cached_scores = pd.Series({r["symbol"]: r["modelScore"] for r in payload["rankings"]})
        max_error = float((direct_scores - cached_scores).abs().max())
        direct_top = direct.head(int(np.ceil(len(direct)/10))).symbol.tolist()
        cached_top = [r["symbol"] for r in payload["rankings"][:len(direct_top)]]
        all_ranks_match = direct.symbol.tolist() == [r["symbol"] for r in payload["rankings"]]
        original_rows = {r["symbol"]: r for r in tactical["rankings"]}
        for row in payload["rankings"]:
            for key in ("close", "beta60d", "return7", "return14", "return30", "return60", "return90", "ytdReturn"):
                assert row[key] == original_rows[row["symbol"]][key], (horizon, row["symbol"], key)
        checks.append({"horizon": horizon, "scored_rows": counts[horizon], "max_direct_cache_score_error": max_error,
                       "identical_top_decile": direct_top == cached_top, "identical_all_ranks": all_ranks_match,
                       "identical_displayed_returns_and_beta": True})
        if max_error > 1e-6 or not all_ranks_match:
            raise AssertionError(f"Cache feature precision/parity failure: {checks[-1]}")
    cache = json.loads(cache_path.read_text())
    metadata = json.loads(candidate_paths[14].with_name(candidate_paths[14].stem + "_metadata.json").read_text())
    args = argparse.Namespace(model_name=candidate_paths[14].stem, reference_cache=str(cache_path), years=3, max_workers=1)
    with patch.dict(os.environ, env), patch.object(live, "fetch_yahoo_history", side_effect=AssertionError("Unexpected network call")):
        focus = live.score_focus_with_reference_cache(args, cache, candidate_paths[14], metadata, metadata["feature_columns"], [tactical["rankings"][0]["symbol"]])
        assert len(focus["focusRankings"]) == 1
    with patch.dict(os.environ, {"EXPECTED_MARKET_DATA_DATE": (pd.Timestamp(as_of) + pd.Timedelta(days=1)).date().isoformat()}):
        assert live.load_reference_cache(cache_path) is None
    write_json({"status": "passed", "as_of": as_of, "mode": "frozen_real_price_replay_not_public_refresh", "checks": checks,
        "feature_version": feature_version, "artifacts": "incumbents" if incumbents else "tuned_candidates",
        "artifact_hashes": {str(h): file_hash(path) for h, path in candidate_paths.items()},
        "in_universe_focus_cache": "passed", "stale_cache_rejected": True, "deployment_promoted": False,
        "current_reference_count": len(reference), "excluded_symbols": tactical["unscoredSymbols"],
        "boundaries": ["Frozen provider responses replayed through actual main/full feature path; subprocess cache scoring uses the real CLI.",
                       "Not a GitHub-hosted runner/network reliability test; no public artifacts or AI/news refresh performed."]}, output / "results.json")
    print(json.dumps(checks, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--incumbents", action="store_true", help="Replay the unchanged deployed weights as a rollback/regression check")
    parser.add_argument("--candidate-root", help="Matrix/tuning root when separate from frozen serving prices")
    parser.add_argument("--output-dir", help="Isolated replay output directory")
    args = parser.parse_args()
    if args.candidate_root and args.incumbents:
        parser.error("--candidate-root and --incumbents are mutually exclusive")
    run(ROOT / args.experiment, args.incumbents, ROOT / args.candidate_root if args.candidate_root else None,
        ROOT / args.output_dir if args.output_dir else None)
