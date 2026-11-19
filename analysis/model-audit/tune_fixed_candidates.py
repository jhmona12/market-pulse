"""Bounded fixed-recipe tuning and isolated deployment exports, never promotion."""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd
import xgboost as xgb

from experiment_data import ROOT
from run_comparison import MODEL_METADATA, cohort_metrics, dmatrix, paired_interval
from render_comparison import finite_json
from common import write_json
from walk_forward_rank_model import fold_slices
from schema import feature_matches_group


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def recipes(metadata):
    base = {**metadata["params"], "tree_method": "hist", "nthread": 4, "seed": 42}
    rounds = metadata["num_boost_round"]
    return [
        ("inherited_fixed", base, rounds),
        ("shorter_fit", base, max(10, rounds // 2)),
        ("longer_fit", base, rounds * 2),
        ("shallow_regularized", {**base, "max_depth": 2, "min_child_weight": 160, "lambda": 8}, rounds),
        ("slow_regularized", {**base, "eta": base["eta"] / 2, "min_child_weight": 160, "lambda": 8}, rounds * 2),
        ("depth_three", {**base, "max_depth": 3, "min_child_weight": 120, "lambda": 8}, rounds),
    ]


def frozen_dataset(folder, horizon):
    prefix = folder / f"matrix_{horizon}_v2"
    meta = json.loads(prefix.with_suffix(".json").read_text())
    frame = pd.read_csv(prefix.with_suffix(".csv"), parse_dates=["date"])
    if "label_available_at" in frame:
        frame["label_available_at"] = pd.to_datetime(frame["label_available_at"])
    values = np.load(prefix.with_suffix(".npy"), mmap_mode="r", allow_pickle=False)
    if meta["horizon"] != horizon or meta["feature_version"] != 2 or values.shape != (len(frame), len(meta["features"])):
        raise ValueError("Frozen matrix contract mismatch")
    if frame.duplicated(["date", "symbol"]).any():
        raise ValueError("Duplicate frozen keys")
    order = frame.sort_values(["date", "symbol"]).index.to_numpy()
    frame = frame.iloc[order].reset_index(drop=True)
    values = values[order]
    if not np.isfinite(values).all():
        raise ValueError("Nonfinite frozen features")
    percentile = frame.groupby("date").excess.rank(pct=True)
    frame["grade"] = np.select([percentile.ge(.9), percentile.ge(.8), percentile.le(.1), percentile.le(.2)], [4, 3, 0, 1], default=2).astype(float)
    frame.loc[frame.excess.isna(), "grade"] = np.nan
    hashes = {suffix: file_hash(prefix.with_suffix(suffix)) for suffix in (".csv", ".npy", ".json")}
    return frame, values, meta, hashes


def select_recipe(cohorts, names):
    # Selection sees validation baskets only, on identical complete dates.
    paired = cohorts.pivot(index="date", columns="stage", values="excess_mean")[names].dropna()
    if paired.empty:
        raise ValueError("No common complete validation baskets; refusing to choose a recipe")
    metrics = cohorts[cohorts.date.isin(paired.index)].groupby("stage").agg(
        mean_excess=("excess_mean", "mean"), median_excess=("excess_mean", "median"),
        mean_member_median=("stock_member_median", "mean"), spread=("spread", "mean"), dates=("date", "size"))
    selected = metrics.sort_values(["mean_excess", "median_excess"], ascending=False, kind="stable").index[0]
    return selected, metrics


def training_rows_as_known(frame, indices):
    """Re-rank only labels available at that fit's cutoff, not future evidence."""
    rows = frame.iloc[indices].copy()
    percentile = rows.groupby("date").excess.rank(pct=True)
    rows["grade"] = np.select([percentile.ge(.9), percentile.ge(.8), percentile.le(.1), percentile.le(.2)], [4, 3, 0, 1], default=2).astype(float)
    if rows.excess.isna().any():
        raise ValueError("Unknown outcomes cannot become training relevance grades")
    return rows


def export_candidate(booster, directory, name, inherited, frame, features, params, rounds, hashes, horizon):
    path = directory / f"{name}.json"
    booster.set_attr(feature_version="2")
    booster.save_model(path)
    metadata = {**inherited, "generated_at": pd.Timestamp.now("UTC").isoformat(),
        "model_path": str(path.relative_to(ROOT)), "dataset": "frozen experiment v2 matrix",
        "feature_version": 2, "feature_columns": features, "feature_count": len(features),
        "target_horizon_days": horizon, "training_rows": len(frame), "training_symbols": int(frame.symbol.nunique()),
        "training_start_date": str(frame.date.min().date()), "training_end_date": str(frame.date.max().date()),
        "training_date_count": int(frame.date.nunique()), "original_training_date_count": int(frame.date.nunique()),
        "train_sample_frequency": "daily" if horizon == 14 else "monthly", "params": params, "num_boost_round": rounds,
        "model_sha256": file_hash(path), "source_matrix_hashes": hashes, "status": "shadow_candidate_not_promoted",
        "notes": "Fixed recipe selected on earlier validation dates. Historical test already inspected; not independent confirmation.",
        "methodology_rationale": ["Inherited 10-year daily / 15-year monthly windows; fixed tree count identical between evaluation and full fit.",
                                  "Current/original snapshot union; survivorship and terminal-outcome gaps remain."]}
    write_json(metadata, path.with_name(f"{name}_metadata.json"))
    return path, metadata


def run(folder, horizon):
    started = time.monotonic()
    inherited = json.loads((ROOT / MODEL_METADATA[horizon]).read_text())
    frame, values, matrix_meta, hashes = frozen_dataset(folder, horizon)
    features = matrix_meta["features"]
    expected_features = inherited["feature_columns"]
    if matrix_meta.get("feature_policy") == "price_only":
        expected_features = [c for c in expected_features if not feature_matches_group(c, "liquidity")]
    if features != expected_features:
        raise ValueError("Frozen feature order differs from deployed artifact")
    args = argparse.Namespace(target_column=inherited["target_column"], return_column=inherited["return_column"],
        embargo_days=None, folds=4, test_days=42 if horizon == 14 else 252,
        validation_days=42 if horizon == 14 else 252, min_train_days=756)
    folds = fold_slices(pd.DatetimeIndex(matrix_meta["calendar"]), args)[-2:]
    directory = folder / f"tuning_{horizon}d"
    directory.mkdir(exist_ok=True)
    presets = recipes(inherited)
    selection = []
    for fold in folds:
        train_idx = np.flatnonzero(frame.date.isin(fold["train_dates"]) & frame.grade.notna())
        if "label_available_at" in frame:
            train_idx = train_idx[frame.iloc[train_idx].label_available_at.lt(fold["validation_dates"][0]).to_numpy()]
        val_idx = np.flatnonzero(frame.date.isin(fold["validation_dates"]))
        train = dmatrix(values[train_idx], training_rows_as_known(frame, train_idx), features)
        validation = xgb.DMatrix(values[val_idx], feature_names=features, nthread=4)
        for name, params, rounds in presets:
            print(f"{horizon}D tuning fold {fold['fold']}: {name}, {rounds} fixed trees, {len(train_idx):,} training rows", flush=True)
            booster = xgb.train(params, train, num_boost_round=rounds)
            booster.set_attr(feature_version="2")
            selection.extend(cohort_metrics(frame.iloc[val_idx], booster.predict(validation), name, fold["fold"]))
            if fold is folds[-1]:
                booster.save_model(directory / f"test_recipe_{name}.json")
            del booster
        del train, validation
        gc.collect()
    selection = pd.DataFrame(selection)
    selection.to_csv(directory / "validation_cohorts.csv", index=False)
    chosen, selection_metrics = select_recipe(selection, [p[0] for p in presets])
    selection_metrics.to_csv(directory / "selection_metrics.csv")
    name, params, rounds = next(p for p in presets if p[0] == chosen)
    print(f"{horizon}D selected {name}; now evaluating the fixed recipe on the later test block", flush=True)
    fold = folds[-1]
    test_idx = np.flatnonzero(frame.date.isin(fold["test_dates"]))
    test_rows = frame.iloc[test_idx]
    test = xgb.DMatrix(values[test_idx], feature_names=features, nthread=4)
    observations = []
    for stage, preset in (("selected_recipe", chosen), ("inherited_fixed", "inherited_fixed")):
        booster = xgb.Booster(); booster.load_model(directory / f"test_recipe_{preset}.json")
        prediction = booster.predict(test)
        test_rows.assign(score=prediction).to_csv(directory / f"{stage}_test_predictions.csv", index=False)
        observations.extend(cohort_metrics(test_rows, prediction, stage, fold["fold"]))
    for stage, score in (("momentum_60d", test_rows.ret_60d.to_numpy()), ("low_volatility", -test_rows.volatility_60d.to_numpy())):
        observations.extend(cohort_metrics(test_rows, score, stage, fold["fold"]))
    export_date = pd.Timestamp(inherited["generated_at"]).tz_localize(None).normalize()
    incumbent_available = test_rows.date.min() > export_date and (folder / f"matrix_{horizon}_v1.csv").exists()
    if incumbent_available:
        old_rows = pd.read_csv(folder / f"matrix_{horizon}_v1.csv", parse_dates=["date"])
        old_values = np.load(folder / f"matrix_{horizon}_v1.npy", mmap_mode="r")
        indexes = pd.MultiIndex.from_frame(old_rows[["date", "symbol"]]).get_indexer(pd.MultiIndex.from_frame(test_rows[["date", "symbol"]]))
        if (indexes < 0).any():
            raise ValueError("Incumbent comparison does not have matched v1 rows")
        incumbent = xgb.Booster(); incumbent.load_model(ROOT / inherited["model_path"])
        observations.extend(cohort_metrics(test_rows, incumbent.predict(xgb.DMatrix(old_values[indexes], feature_names=features)), "frozen_incumbent", fold["fold"]))
    cohorts = pd.DataFrame(observations)
    cohorts.to_csv(directory / "test_cohorts.csv", index=False)
    common = cohorts.pivot(index="date", columns="stage", values="excess_mean").dropna().index
    summary = cohorts[cohorts.date.isin(common)].groupby("stage").agg(
        mean_stock_return=("stock_mean", "mean"), median_stock_return=("stock_mean", "median"),
        mean_member_median=("stock_member_median", "mean"), mean_excess=("excess_mean", "mean"),
        median_excess=("excess_mean", "median"), spread=("spread", "mean"), dates=("date", "size"))
    effects = {control: paired_interval(cohorts, horizon, stage="selected_recipe", control=control)
               for control in cohorts.stage.unique() if control != "selected_recipe"}
    print(summary.to_string(), flush=True)
    print(f"{horizon}D fitting the selected {rounds}-tree recipe on all matured labels", flush=True)
    known_idx = np.flatnonzero(frame.grade.notna())
    full = xgb.train(params, dmatrix(values[known_idx], frame.iloc[known_idx], features), num_boost_round=rounds)
    candidate_name = f"xgboost_rank_sector{horizon}_v2_candidate"
    candidate_path, metadata = export_candidate(full, directory, candidate_name, inherited, frame.iloc[known_idx], features, params, rounds, hashes, horizon)
    for key in ("feature_policy", "universe_policy", "sector_policy", "source_hashes", "quality", "quarantine_policy"):
        if key in matrix_meta:
            metadata[key] = matrix_meta[key]
    if "universe_policy" in matrix_meta:
        metadata["methodology_rationale"][1] = matrix_meta["universe_policy"]
    write_json(metadata, candidate_path.with_name(candidate_name + "_metadata.json"))
    sample = np.unique(np.linspace(0, len(frame)-1, min(2048, len(frame)), dtype=int))
    probe = xgb.DMatrix(values[sample], feature_names=features, nthread=4)
    contributions = full.predict(probe, pred_contribs=True)
    shap_error = float(np.max(np.abs(contributions.sum(axis=1) - full.predict(probe))))
    if shap_error > 2e-6:
        raise ValueError("Candidate SHAP does not reconstruct predictions")
    importance = np.mean(np.abs(contributions[:, :-1]), axis=0)
    top = np.argsort(-importance)[:20]
    write_json({"generatedAt": metadata["generated_at"], "sourceModel": candidate_name, "modelSha256": metadata["model_sha256"],
        "featureVersion": 2, "method": "XGBoost exact TreeSHAP; evenly spaced full-history sample; not performance evidence",
        "sampleRows": len(sample), "maxAdditivityError": shap_error, "notes": ["Candidate-specific; not the incumbent explanation."],
        "topFeatures": [{"feature": features[i], "label": features[i], "meanAbsShap": float(importance[i]), "meanShap": float(contributions[:, i].mean())} for i in top]},
        directory / f"{candidate_name}_explainability.json")
    result = {"horizon": horizon, "as_of": matrix_meta["as_of"], "selected_recipe": chosen, "rounds": rounds,
        "params": params, "recipes": [{"name": n, "params": p, "rounds": r} for n,p,r in presets],
        "validation_summary": selection_metrics.reset_index().to_dict("records"), "test_summary": summary.reset_index().to_dict("records"),
        "test_start": str(test_rows.date.min().date()), "test_end": str(test_rows.date.max().date()), "common_complete_test_cohorts": len(common),
        "test_cohorts": int(test_rows.date.nunique()), "effects": effects, "incumbent_comparable": incumbent_available,
        "candidate_path": str(candidate_path.relative_to(ROOT)), "candidate_metadata": metadata, "source_matrix_hashes": hashes,
        "folds": [{k:v for k,v in f.items() if not k.endswith("_dates")} for f in folds],
        "elapsed_seconds": round(time.monotonic()-started, 1), "promotion_status": "not_promoted",
        "limitations": ["Test dates already inspected in prior work; development comparison, not untouched confirmation.",
                        "Historical membership, sector and terminal-return gaps remain. Deployment testing is separate from economic validation."]}
    write_json(finite_json(result), directory / "results.json")
    print(f"Saved {candidate_path}; elapsed {result['elapsed_seconds']} seconds", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--horizon", type=int, choices=[14, 252], required=True)
    args = parser.parse_args()
    run(ROOT / args.experiment, args.horizon)
