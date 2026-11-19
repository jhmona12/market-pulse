"""Promote a validated pair of artifacts without refitting or changing public data."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import shutil

from artifact_contract import artifact_hash, validate_model_artifact
from common import ROOT, write_json

PRODUCTION = {
    14: "models/rank/xgboost_rank_sector14_tuned.json",
    252: "models/long-horizon/xgboost_rank_sector252_15y_monthly_tuned_research.json",
}


def family(path):
    return [path, path.with_name(path.stem + "_metadata.json"),
            path.with_name(path.stem + "_explainability.json")]


def validate_candidate(path, horizon):
    _, meta_path, shap_path = family(path)
    meta = json.loads(meta_path.read_text())
    explanation = json.loads(shap_path.read_text())
    if validate_model_artifact(path, meta) != 2 or meta["target_horizon_days"] != horizon:
        raise ValueError("Wrong candidate feature version or horizon")
    if explanation.get("modelSha256") != meta["model_sha256"] or explanation.get("featureVersion") != 2:
        raise ValueError("SHAP explanation does not match candidate weights")
    if any(row["feature"] not in meta["feature_columns"] for row in explanation["topFeatures"]):
        raise ValueError("SHAP feature not present in model")
    return meta, explanation


def dashboard_evidence(meta, explanation, result, release_id):
    return {
        "generatedAt": meta["generated_at"],
        "sourceGeneratedAt": meta["generated_at"],
        "modelMetadata": {
            "targetColumn": meta["target_column"], "returnColumn": meta["return_column"],
            "featureCount": meta["feature_count"], "featureVersion": meta["feature_version"],
            "modelSha256": meta["model_sha256"], "releaseId": release_id,
            "trainingRows": meta["training_rows"], "trainingSymbols": meta["training_symbols"],
            "trainingStartDate": meta["training_start_date"], "trainingEndDate": meta["training_end_date"],
            "numBoostRound": meta["num_boost_round"], "trainSampleFrequency": meta["train_sample_frequency"],
            "methodologyRationale": meta["methodology_rationale"],
        },
        "promotedMethodology": {"trainingWindow": "15y / monthly", "rationale": meta["methodology_rationale"]},
        "labelSummaries": [{"label": "Sector-neutral 252D return",
            "description": "Ranks one-year total return versus the sector ETF, net of 15 bp estimated round-trip cost.",
            "targetColumn": meta["target_column"], "returnColumn": meta["return_column"]}],
        "labelComparison": [], "baselineComparison": {"monthly": [], "quarterly": []},
        "shapTopFeatures": explanation["topFeatures"][:12],
        "evaluation": {
            "status": "development_only", "releaseId": release_id,
            "modelSha256": meta["model_sha256"],
            "message": "The replacement has improved data-quality controls, not proven better returns. Previously inspected development tests are not an independent performance estimate. Old-model backtest medians are intentionally not carried forward.",
            "testStart": result["test_start"], "testEnd": result["test_end"],
            "completeCommonCohorts": result["common_complete_test_cohorts"], "totalCohorts": result["test_cohorts"],
            "reportUrl": "https://github.com/jhmona12/market-pulse/blob/main/docs/targeted-quarantine-experiment-2026-11-18.md",
        },
        "methodology": [
            "Separate 252-trading-day rank model; scores are not return forecasts or probabilities.",
            "Approximately 15 years of raw history with monthly signal dates; only matured labels enter training.",
            "Historical membership and reviewed corporate actions are included, but unresolved outcomes and retrospective price reviews remain limitations.",
            "No independent post-release performance is available at promotion. SHAP explains model output, not causal return drivers.",
        ],
    }


def promote(candidate_root, release_id, root=ROOT):
    release = root / "models/releases" / release_id
    backup = root / "data/modeling/rollbacks" / release_id
    if release.exists() or backup.exists():
        raise ValueError("Release/rollback already exists; refusing to overwrite")
    prepared = []
    promoted_at = datetime.now(timezone.utc).isoformat()
    # Validate BOTH horizons and all companions before touching any deployed file.
    for horizon, destination in PRODUCTION.items():
        source = candidate_root / f"tuning_{horizon}d/xgboost_rank_sector{horizon}_v2_candidate.json"
        meta, explanation = validate_candidate(source, horizon)
        result = json.loads((source.parent / "results.json").read_text())
        if result["candidate_metadata"]["model_sha256"] != meta["model_sha256"]:
            raise ValueError("Evaluation provenance does not match candidate")
        target = root / destination
        old_hashes = {str(p.relative_to(root)): artifact_hash(p) for p in family(target)}
        meta.update(status="production", model_path=destination, promoted_at=promoted_at, release_id=release_id,
                    prior_model_sha256=artifact_hash(target))
        explanation.update(sourceModel=target.stem)
        explanation["notes"] = ["Explains the deployed replacement weights; not evidence of investment performance."]
        if horizon == 252:
            meta["dashboard_evidence_path"] = f"models/releases/{release_id}/strategic-evidence.json"
        prepared.append((horizon, source, target, meta, explanation, result, old_hashes))
    backup.mkdir(parents=True)
    for _, _, target, *_ in prepared:
        for path in family(target):
            copy = backup / path.relative_to(root)
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, copy)
    manifest = {"releaseId": release_id, "promotedAt": promoted_at,
                "authorization": "User requested public replacement instead of shadow rollout.",
                "performanceStatus": "Improvement not established; economic gate inconclusive/negative. Explicit user-directed promotion.",
                "models": []}
    try:
        release.mkdir(parents=True)
        for horizon, source, target, meta, explanation, result, old_hashes in prepared:
            shutil.copy2(source, target)
            write_json(meta, family(target)[1])
            write_json(explanation, family(target)[2])
            validate_candidate(target, horizon)
            write_json(result, release / f"evaluation-{horizon}d.json")
            if horizon == 252:
                write_json(dashboard_evidence(meta, explanation, result, release_id), release / "strategic-evidence.json")
            manifest["models"].append({"horizon": horizon, "path": str(target.relative_to(root)),
                "modelSha256": artifact_hash(target), "previousFiles": old_hashes,
                "featureVersion": 2, "featureCount": meta["feature_count"]})
        write_json(manifest, release / "manifest.json")
        write_json(manifest, backup / "manifest.json")
    except Exception:
        for _, _, target, *_ in prepared:
            for path in family(target):
                shutil.copy2(backup / path.relative_to(root), path)
        raise
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-root", required=True)
    parser.add_argument("--release-id", required=True)
    args = parser.parse_args()
    if Path(args.release_id).name != args.release_id or args.release_id in (".", ".."):
        parser.error("release-id must be a single directory name")
    print(json.dumps(promote(ROOT / args.candidate_root, args.release_id), indent=2))
