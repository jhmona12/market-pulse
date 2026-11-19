"""Matched fixed-recipe sensitivity, before considering tuned candidates."""
from __future__ import annotations

import argparse
import gc
import json

import numpy as np
import pandas as pd
import xgboost as xgb

from experiment_data import ROOT
from common import write_json
from run_comparison import MODEL_METADATA, cohort_metrics, dmatrix, paired_interval
from tune_fixed_candidates import frozen_dataset, recipes, training_rows_as_known
from walk_forward_rank_model import fold_slices
from render_comparison import finite_json


def matched_summary(cohorts):
    names = list(cohorts.stage.unique())
    common = cohorts.pivot(index="date", columns="stage", values="excess_mean")[names].dropna().index
    rows = cohorts[cohorts.date.isin(common)]
    metrics = rows.groupby("stage").agg(mean_stock_return=("stock_mean", "mean"),
        median_basket_return=("stock_mean", "median"), mean_member_median=("stock_member_median", "mean"),
        mean_sector_relative=("excess_mean", "mean"), median_sector_relative=("excess_mean", "median"),
        cohorts=("date", "size"))
    return {"common_complete_dates": len(common), "rows": metrics.reset_index().to_dict("records")}


def run(folder, horizon):
    inherited = json.loads((ROOT / MODEL_METADATA[horizon]).read_text())
    baseline, base_values, meta, base_hash = frozen_dataset(folder / "reconciled/price_only", horizon)
    treated, values, treatment_meta, treatment_hash = frozen_dataset(folder / "quarantine/price_only", horizon)
    if meta["features"] != treatment_meta["features"] or meta["calendar"] != treatment_meta["calendar"]:
        raise ValueError("Unmatched feature/calendar contracts")
    if meta["source_hashes"] != treatment_meta["source_hashes"]:
        raise ValueError("Other source inputs changed between arms")
    if treatment_meta["quarantine_policy"]["flagged_observations"] != 54:
        raise ValueError("Predeclared 54-flag ledger changed")
    base_keys = pd.MultiIndex.from_frame(baseline[["date", "symbol"]])
    treated_keys = pd.MultiIndex.from_frame(treated[["date", "symbol"]])
    index = base_keys.get_indexer(treated_keys)
    if (index < 0).any():
        raise ValueError("Quarantine unexpectedly added eligible candidates")
    # Bounded batches avoid allocating another full ~650 MB feature matrix.
    changed = 0
    for start in range(0, len(treated), 25000):
        stop = start + 25000
        changed += int(np.any(values[start:stop] != base_values[index[start:stop]], axis=1).sum())
    newly_unknown = int((baseline.iloc[index].excess.notna().to_numpy() & treated.excess.isna().to_numpy()).sum())
    unaffected_labels = ~treated.price_quality_unknown_outcome.to_numpy(dtype=bool)
    np.testing.assert_allclose(baseline.iloc[index].outcome.to_numpy()[unaffected_labels],
        treated.outcome.to_numpy()[unaffected_labels], equal_nan=True, rtol=0, atol=0)
    failures = baseline[baseline.symbol.isin(["SIVB", "SBNY", "FRC"]) & baseline.outcome.lt(-.9)]
    failure_index = treated_keys.get_indexer(pd.MultiIndex.from_frame(failures[["date", "symbol"]]))
    if (failure_index < 0).any():
        raise ValueError("Quarantine erased verified failed-bank observations")
    np.testing.assert_allclose(treated.iloc[failure_index].outcome, failures.outcome, rtol=0, atol=0)
    checks = {"baseline_rows": len(baseline), "quarantine_rows": len(treated), "removed_feature_rows": len(baseline)-len(treated),
        "changed_common_feature_rows": changed, "new_unknown_outcomes_on_common_rows": newly_unknown,
        "baseline_unknown_outcomes": int(baseline.excess.isna().sum()), "quarantine_unknown_outcomes": int(treated.excess.isna().sum()),
        "verified_bank_loss_rows_retained": len(failures), "unaffected_labels_identical": True}
    if not changed or not newly_unknown:
        raise ValueError("Quarantine did not propagate features/context and outcome uncertainty")
    args = argparse.Namespace(target_column=inherited["target_column"], return_column=inherited["return_column"],
        embargo_days=None, folds=4, test_days=42 if horizon == 14 else 252,
        validation_days=42 if horizon == 14 else 252, min_train_days=756)
    folds = fold_slices(pd.DatetimeIndex(meta["calendar"]), args)
    _, params, rounds = recipes(inherited)[0]
    output = folder / "quarantine-comparison" / f"{horizon}d"
    output.mkdir(parents=True, exist_ok=True)
    observations, split_audit = [], []
    for arm, frame, matrix in (("corrected", baseline, base_values), ("quarantine", treated, values)):
        for fold in folds:
            cutoff = fold["validation_dates"][0]
            train_index = np.flatnonzero(frame.date.isin(fold["train_dates"]) & frame.excess.notna() & frame.label_available_at.lt(cutoff))
            test_index = np.flatnonzero(frame.date.isin(fold["test_dates"]))
            rows = frame.iloc[test_index]
            train_rows = training_rows_as_known(frame, train_index)
            if not train_rows.label_available_at.lt(cutoff).all():
                raise ValueError("Future outcome evidence entered training")
            print(f"{horizon}D {arm} fold {fold['fold']}: {len(train_index):,} train, {rows.date.nunique()} test cohorts", flush=True)
            booster = xgb.train(params, dmatrix(matrix[train_index], train_rows, meta["features"]), num_boost_round=rounds)
            score = booster.predict(xgb.DMatrix(matrix[test_index], feature_names=meta["features"], nthread=4))
            rows.assign(score=score).to_csv(output / f"{arm}_fold{fold['fold']}_predictions.csv", index=False)
            observations.extend(cohort_metrics(rows, score, arm, fold["fold"]))
            for label, scores in (("momentum", rows.ret_60d.to_numpy()), ("low_volatility", -rows.volatility_60d.to_numpy())):
                observations.extend(cohort_metrics(rows, scores, f"{arm}_{label}", fold["fold"]))
            split_audit.append({"arm": arm, "fold": fold["fold"], "training_rows": len(train_index),
                "latest_known_label": str(train_rows.label_available_at.max().date()), "validation_cutoff": str(cutoff.date()),
                **{k: v for k, v in fold.items() if not k.endswith("_dates")}})
            del booster
            gc.collect()
    cohorts = pd.DataFrame(observations)
    cohorts.to_csv(output / "cohorts.csv", index=False)
    model_rows = cohorts[cohorts.stage.isin(["corrected", "quarantine"])]
    per_fold = [{"fold": int(fold), **matched_summary(group)} for fold, group in model_rows.groupby("fold")]
    coverage = cohorts.groupby("stage").agg(total_dates=("date", "size"), complete_dates=("excess_mean", "count"),
        unresolved_selected_positions=("unresolved_top", "sum")).reset_index().to_dict("records")
    result = {"horizon": horizon, "as_of": meta["as_of"], "checks": checks,
        "source_hashes": {"corrected": base_hash, "quarantine": treatment_hash}, "quarantine_policy": treatment_meta["quarantine_policy"],
        "params": params, "rounds": rounds, "splits": split_audit, "coverage": coverage,
        "matched_models": matched_summary(model_rows), "matched_all_arms": matched_summary(cohorts), "per_fold": per_fold,
        "paired_effect": paired_interval(model_rows, horizon, stage="quarantine", control="corrected"),
        "promotion_status": "decision_pending_separate_tuning_and_deployment_checks",
        "limitations": ["Development data already inspected; not untouched confirmation.",
            "Complete-date comparison can still be biased by nonrandom missing outcomes; coverage is reported separately.",
            "Overlapping cohorts, not independent trades or CAGR. Historical data quality remains imperfect."]}
    write_json(finite_json(result), output / "results.json")
    print(json.dumps(finite_json({"checks": checks, "matched_models": result["matched_models"], "effect": result["paired_effect"]}), indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--horizon", type=int, choices=[14, 252], required=True)
    args = parser.parse_args()
    run(ROOT / args.experiment, args.horizon)
