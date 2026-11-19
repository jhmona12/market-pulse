"""Paired, bounded walk-forward experiment; never promotes production weights."""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import xgboost as xgb

from experiment_data import ROOT, load_pair
from common import write_json
from rank_validation import best_iteration_model
from walk_forward_rank_model import fold_slices

MODEL_METADATA = {
    14: "models/rank/xgboost_rank_sector14_tuned_metadata.json",
    252: "models/long-horizon/xgboost_rank_sector252_15y_monthly_tuned_research_metadata.json",
}


def cohort_metrics(rows, score, stage, fold):
    output = []
    for date, group in rows.assign(score=score).groupby("date", sort=True):
        group = group.sort_values(["score", "symbol"], ascending=[False, True], kind="stable")
        count = max(1, int(np.ceil(len(group) / 10)))
        top, bottom = group.head(count), group.tail(count)
        top_known, bottom_known = top.excess.notna().all(), bottom.excess.notna().all()
        output.append({"date": date, "stage": stage, "fold": fold, "universe": len(group), "top_count": count,
                       "stock_mean": top.outcome.mean() if top_known else np.nan, "stock_member_median": top.outcome.median() if top_known else np.nan,
                       "excess_mean": top.excess.mean() if top_known else np.nan, "excess_member_median": top.excess.median() if top_known else np.nan,
                       "spread": top.excess.mean() - bottom.excess.mean() if top_known and bottom_known else np.nan,
                       "hit_rate": top.excess.gt(0).mean() if top_known else np.nan,
                       "unresolved_top": int(top.excess.isna().sum()), "unresolved_universe": int(group.excess.isna().sum()),
                       "top_symbols": " ".join(top.symbol)})
        if "excess_lower" in top:
            for metric in ("outcome", "excess"):
                for bound in ("lower", "upper"):
                    column = f"{metric}_{bound}"
                    output[-1][f"top_{column}"] = float(top[column].mean()) if top[column].notna().all() else np.nan
    return output


def paired_interval(cohorts, horizon, stage="corrected_features", control="corrected_evaluation"):
    all_dates = cohorts.pivot(index="date", columns="stage", values="excess_mean")[[stage, control]]
    paired = all_dates.dropna()
    diff = (paired[stage] - paired[control]).to_numpy()
    if not len(diff):
        return {"status": "no_common_cohorts"}
    block = 14 if horizon == 14 else 12
    if len(diff) < 2 * block:
        return {"status": "insufficient_blocks", "mean_difference": float(diff.mean()),
                "approximate_95pct_block_interval": None, "paired_dates": len(diff), "block_cohorts": block,
                "warning": "Too few complete cohorts for two dependence-length blocks; no uncertainty interval is reported."}
    if len(paired) != len(all_dates):
        return {"status": "incomplete_calendar", "mean_difference": float(diff.mean()),
                "approximate_95pct_block_interval": None, "paired_dates": len(diff), "total_dates": len(all_dates),
                "block_cohorts": block,
                "warning": "Missing baskets break the regular calendar; refusing a block bootstrap on compressed complete dates."}
    rng = np.random.default_rng(42)
    resamples = []
    for _ in range(1000):
        starts = rng.integers(0, len(diff), size=int(np.ceil(len(diff) / block)))
        selected = ((starts[:, None] + np.arange(block)) % len(diff)).ravel()[:len(diff)]
        resamples.append(diff[selected].mean())
    low, high = np.quantile(resamples, [.025, .975])
    return {"mean_difference": float(diff.mean()), "approximate_95pct_block_interval": [float(low), float(high)],
            "paired_dates": len(diff), "block_cohorts": block,
            "warning": "Exploratory paired moving-block bootstrap, not independent confirmation; limited regimes and universe/selection bias remain."}


def dmatrix(values, rows, features):
    matrix = xgb.DMatrix(values, label=rows.grade.to_numpy(), feature_names=features, nthread=4)
    matrix.set_group(rows.groupby("date", sort=False).size().to_numpy())
    return matrix


def run(folder, horizon):
    metadata = json.loads((ROOT / MODEL_METADATA[horizon]).read_text())
    frame, values, dataset_meta = load_pair(folder, horizon)
    features = dataset_meta[1]["features"]
    if features != metadata["feature_columns"] or features != dataset_meta[2]["features"]:
        raise ValueError("Feature names/order changed")
    args = argparse.Namespace(target_column=metadata["target_column"], return_column=metadata["return_column"],
        embargo_days=None, folds=4, test_days=42 if horizon == 14 else 252,
        validation_days=42 if horizon == 14 else 252, min_train_days=756)
    calendar = pd.DatetimeIndex(dataset_meta[1]["calendar"])
    folds = fold_slices(calendar, args)
    params = {**metadata["params"], "nthread": 4, "tree_method": "hist"}
    result_dir = folder / f"comparison_{horizon}d"
    result_dir.mkdir(exist_ok=True)
    all_cohorts, fitted = [], []
    for fold in folds:
        train_idx = np.flatnonzero(frame.date.isin(fold["train_dates"]) & frame.grade.notna())
        val_idx = np.flatnonzero(frame.date.isin(fold["validation_dates"]) & frame.grade.notna())
        test_idx = np.flatnonzero(frame.date.isin(fold["test_dates"]))
        for version in (1, 2):
            print(f"{horizon}D fold {fold['fold']}/4 v{version}: train {len(train_idx):,}, validate {len(val_idx):,}, test {len(test_idx):,}", flush=True)
            train = dmatrix(values[version][train_idx], frame.iloc[train_idx], features)
            validation = dmatrix(values[version][val_idx], frame.iloc[val_idx], features)
            booster = xgb.train(params, train, num_boost_round=300, evals=[(validation, "validation")], early_stopping_rounds=40 if horizon == 14 else 50, verbose_eval=False)
            test = xgb.DMatrix(values[version][test_idx], feature_names=features, nthread=4)
            if version == 1:
                all_cohorts.extend(cohort_metrics(frame.iloc[test_idx], booster.predict(test), "all_trees_reference", fold["fold"]))
            selected = best_iteration_model(booster)
            selected.set_attr(feature_version=str(version))
            stage = "corrected_evaluation" if version == 1 else "corrected_features"
            prediction = selected.predict(test)
            all_cohorts.extend(cohort_metrics(frame.iloc[test_idx], prediction, stage, fold["fold"]))
            selected.save_model(result_dir / f"fold{fold['fold']}_v{version}.json")
            frame.iloc[test_idx].assign(score=prediction).to_csv(result_dir / f"fold{fold['fold']}_v{version}_predictions.csv", index=False)
            fitted.append({"fold": fold["fold"], "version": version, "rounds": selected.num_boosted_rounds(),
                "split": {key: value for key, value in fold.items() if not key.endswith("_dates")}})
            del train, validation, booster, selected, test
            gc.collect()
    cohorts = pd.DataFrame(all_cohorts)
    cohorts.to_csv(result_dir / "cohorts.csv", index=False)
    metrics = ["stock_mean", "stock_member_median", "excess_mean", "excess_member_median", "spread", "hit_rate"]
    summary = cohorts.groupby("stage")[metrics].mean().reset_index()
    summary["median_cohort_excess"] = summary.stage.map(cohorts.groupby("stage").excess_mean.median())
    summary["cohorts"] = summary.stage.map(cohorts.groupby("stage").size())
    summary["complete_top_cohorts"] = summary.stage.map(cohorts.groupby("stage").excess_mean.count())
    final_test = folds[-1]["test_dates"]
    export_day = pd.Timestamp(metadata["generated_at"]).tz_localize(None).normalize()
    recent_idx = np.flatnonzero(frame.date.isin(final_test) & frame.date.gt(export_day))
    incumbent_comparison = {"status": "no_fully_mature_post_export_signals", "exported_at": metadata["generated_at"]}
    if len(recent_idx):
        incumbent = xgb.Booster(); incumbent.load_model(ROOT / metadata["model_path"])
        incumbent_rows = cohort_metrics(frame.iloc[recent_idx], incumbent.predict(xgb.DMatrix(values[1][recent_idx], feature_names=features)), "frozen_incumbent", folds[-1]["fold"])
        recent = pd.concat([pd.DataFrame(incumbent_rows), cohorts[cohorts.date.isin(frame.iloc[recent_idx].date)]])
        recent.to_csv(result_dir / "recent_incumbent_cohorts.csv", index=False)
        incumbent_comparison = {"status": "post_export_latest_fold_comparison", "start": str(frame.iloc[recent_idx].date.min().date()), "end": str(frame.iloc[recent_idx].date.max().date()),
            "summary": recent.groupby("stage")[metrics].mean().reset_index().to_dict("records"),
            "feature_candidate_vs_incumbent": paired_interval(recent, horizon, control="frozen_incumbent")}
    # Freeze a full-data research candidate, distinct from validation-fold models.
    print(f"{horizon}D: fitting separate full-data candidate; no performance is claimed on its own training rows", flush=True)
    known_idx = np.flatnonzero(frame.grade.notna())
    candidate = xgb.train(params, dmatrix(values[2][known_idx], frame.iloc[known_idx], features), num_boost_round=metadata["num_boost_round"])
    candidate.set_attr(feature_version="2")
    candidate_path = result_dir / "candidate_v2_full.json"
    candidate.save_model(candidate_path)
    write_json({"generated_at": pd.Timestamp.now("UTC").isoformat(), "feature_version": 2, "horizon": horizon, "features": features,
        "training_start": str(frame.iloc[known_idx].date.min().date()), "training_end": str(frame.iloc[known_idx].date.max().date()), "training_rows": len(known_idx),
        "num_boost_round": metadata["num_boost_round"], "params": params, "model_sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
        "status": "research_only_not_promoted", "source_signatures": {v: dataset_meta[v]["signature"] for v in (1, 2)}}, result_dir / "candidate_v2_full_metadata.json")
    payload = {"horizon": horizon, "as_of": dataset_meta[1]["as_of"], "summary": summary.to_dict("records"),
        "feature_correction_effect": paired_interval(cohorts, horizon), "incumbent_comparison": incumbent_comparison, "folds": fitted,
        "common_rows": len(frame), "version_rows": {v: dataset_meta[v]["rows"] for v in (1, 2)}, "quality": dataset_meta[2]["quality"],
        "test_start": str(cohorts.date.min().date()), "test_end": str(cohorts.date.max().date()),
        "notes": ["All arms share calendar-aligned prices, label dates, common rows, fixed hyperparameters and purged folds. All-trees reference is a diagnostic, not a candidate.",
                  "Universe is original/current-member union, not point-in-time membership. Unresolved terminal outcomes are counted, never fabricated; results remain survivorship-biased.",
                  "Long-horizon evaluation uses monthly anchors. Tactical evaluation uses daily cohorts. Cohorts overlap; these are research comparisons, not final promotion evidence."]}
    write_json(payload, result_dir / "results.json")
    print(summary.to_string(index=False), flush=True)
    print(json.dumps(payload["feature_correction_effect"], indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--horizon", type=int, choices=[14, 252], required=True)
    args = parser.parse_args()
    run(ROOT / args.experiment, args.horizon)
