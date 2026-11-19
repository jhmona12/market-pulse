"""Readable, private comparison of quarantine, tuning and deployment evidence."""
from __future__ import annotations

import argparse
from html import escape
import json

import pandas as pd

from render_comparison import ROOT, table, pct
from tune_fixed_candidates import file_hash
from common import write_json


def render(folder):
    candidate_root = folder / "quarantine/price_only"
    deployment = json.loads((candidate_root / "deployment/results.json").read_text())
    assembly = json.loads((candidate_root / "deployment/dashboard-assembly-results.json").read_text())
    if deployment["status"] != "passed" or assembly["status"] != "passed":
        raise ValueError("Deployment replay has not passed")
    sections, summaries = [], []
    for horizon in (14, 252):
        fixed = json.loads((folder / f"quarantine-comparison/{horizon}d/results.json").read_text())
        if fixed["as_of"] != "2026-11-16":
            raise ValueError("This reviewed release decision is specific to the November 16 research snapshot")
        tuned = json.loads((candidate_root / f"tuning_{horizon}d/results.json").read_text())
        metadata = tuned["candidate_metadata"]
        explanation = json.loads((candidate_root / f"tuning_{horizon}d/xgboost_rank_sector{horizon}_v2_candidate_explainability.json").read_text())
        if metadata["model_sha256"] != explanation["modelSha256"] or metadata["model_sha256"] != deployment["artifact_hashes"][str(horizon)]:
            raise ValueError("Report explanation/replay refers to another model")
        if file_hash(ROOT / tuned["candidate_path"]) != metadata["model_sha256"]:
            raise ValueError("Candidate weights changed after replay")
        fixed_rows = [[r["stage"], pct(r["mean_stock_return"]), pct(r["median_basket_return"]), pct(r["mean_member_median"]),
                       pct(r["mean_sector_relative"])] for r in fixed["matched_models"]["rows"]]
        tuned_rows = [[r["stage"], pct(r["mean_stock_return"]), pct(r["median_stock_return"]), pct(r["mean_member_median"]),
                       pct(r["mean_excess"])] for r in tuned["test_summary"]]
        headers = ["Arm", "Mean stock basket", "Median stock basket", "Mean within-basket median", "Mean vs sector, net"]
        coverage = [[r["stage"], r["complete_dates"], r["total_dates"], r["unresolved_selected_positions"]] for r in fixed["coverage"]]
        test_cohorts = pd.read_csv(candidate_root / f"tuning_{horizon}d/test_cohorts.csv")
        bounded = []
        for name, group in test_cohorts.groupby("stage"):
            complete = group[["top_excess_lower", "top_excess_upper"]].notna().all(axis=1)
            bounded.append([name, f"{int(complete.sum())}/{len(group)}",
                pct(group.top_excess_lower.mean()) if complete.all() else "Incomplete",
                pct(group.top_excess_upper.mean()) if complete.all() else "Incomplete"])
        shap = [[r["feature"], f'{r["meanAbsShap"]:.5f}'] for r in explanation["topFeatures"][:10]]
        checks = fixed["checks"]
        summaries.append({"horizon": horizon, "fixed_comparison": fixed, "tuning": tuned,
                          "candidate_sha256": metadata["model_sha256"]})
        sections.append(f'''<section id="h{horizon}"><h2>{horizon}-Trading-Day Model</h2>
<p><strong>Fixed settings, four chronological folds.</strong> {fixed['matched_models']['common_complete_dates']} common complete baskets.
Quarantine removes {checks['removed_feature_rows']:,} feature rows; {checks['changed_common_feature_rows']:,} retained rows have recomputed features or context.
Unknown outcomes: {checks['baseline_unknown_outcomes']:,} before, {checks['quarantine_unknown_outcomes']:,} after.</p>
{table(headers, fixed_rows)}
<p>Average sector-relative effect: {fixed['paired_effect']['mean_difference']*100:+.2f} percentage points. {escape(fixed['paired_effect']['warning'])}</p>
<details><summary>Outcome coverage by comparison arm</summary>{table(['Arm', 'Complete baskets', 'All baskets', 'Unresolved selected positions'], coverage)}</details>
<h3>Bounded tuning and retraining</h3><p>Six recipes, two earlier validation windows. Selected: {escape(tuned['selected_recipe'])},
{tuned['rounds']} trees, depth {tuned['params']['max_depth']}. {metadata['training_rows']:,} matured training rows,
{metadata['training_symbols']} securities, {metadata['feature_count']} price/context features.
Signal dates {metadata['training_start_date']} to {metadata['training_end_date']}.</p>
<p>Later development test: {tuned['test_start']} to {tuned['test_end']};
{tuned['common_complete_test_cohorts']}/{tuned['test_cohorts']} common complete baskets. These are not untouched tests.</p>
{table(headers, tuned_rows)}
<details><summary>All later test baskets: contingent-rights bounds</summary>
<p>Verified cash and maximum contingent consideration provide bounds, not invented point labels. No mean is shown unless every basket in that arm has bounds. These ranges are not confidence intervals.</p>
{table(['Arm', 'Bounded / tested', 'Mean vs sector lower', 'Mean vs sector upper'], bounded)}</details>
<details><summary>Candidate-specific TreeSHAP</summary><p>Mean absolute contribution to the ranking score, not return, probability, direction or causation.
{explanation['sampleRows']:,} history rows; maximum reconstruction error {explanation['maxAdditivityError']:.2g}.</p>
{table(['Feature', 'Mean absolute SHAP'], shap)}</details></section>''')
    decisions = {"status": "hold_production_weights", "generated_at": pd.Timestamp.now("UTC").isoformat(),
        "research_as_of": "2026-11-16", "serving_replay_as_of": deployment["as_of"],
        "rationale": ["Tactical quarantine worsens fixed-setting mean and median returns and still trails simple recent baselines.",
            "Strategic average improves but median deteriorates, with only 24/48 common complete monthly baskets. Its tuned recipe also trails its inherited fixed refit across all 12 recent baskets when verified rights bounds are included.",
            "Previously inspected overlapping cohorts and unresolved former-security outcomes do not establish robust improvement."],
        "models": summaries, "deployment": deployment, "assembly": assembly,
        "production_hashes": {name: file_hash(ROOT / name) for name in (
            "models/rank/xgboost_rank_sector14_tuned.json", "models/long-horizon/xgboost_rank_sector252_15y_monthly_tuned_research.json")}}
    output = folder / "quarantine-comparison"
    write_json(decisions, output / "release-decision.json")
    checks = [[c["horizon"], c["scored_rows"], c["identical_all_ranks"], f'{c["max_direct_cache_score_error"]:.2g}'] for c in deployment["checks"]]
    html = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Targeted Quarantine: Model Release Review</title><style>
body{{margin:0;color:#172a27;background:#f7f9fa;font:16px/1.55 system-ui,sans-serif}}main,header{{max-width:1150px;margin:auto;padding:24px}}
h1{{font-size:28px;line-height:1.2}}h2{{font-size:23px}}h3{{font-size:18px}}section{{padding:24px 0;border-top:1px solid #bbc9c7}}
nav{{display:flex;gap:20px;flex-wrap:wrap}}a{{color:#006f65}}.decision{{border-left:4px solid #b33738;background:#fff1ef;padding:16px}}
.table-scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:12px;border-bottom:1px solid #cdd9d6;text-align:right;min-width:110px}}
th{{background:#e5efeb}}td:first-child,th:first-child{{text-align:left;min-width:175px}}details{{padding:14px 0}}summary{{cursor:pointer;font-weight:600}}@media(max-width:650px){{main,header{{padding:16px}}h1{{font-size:24px}}}}
</style><header><h1>Targeted Quarantine: Model Release Review</h1><p>Frozen research through November 16, 2026. Neither candidate has replaced production.</p>
<nav><a href="#decision">Decision</a><a href="#h14">Tactical</a><a href="#h252">Strategic</a><a href="#deployment">Serving Checks</a></nav></header><main>
<section id="decision"><h2>Release Decision: Hold</h2><div class="decision">Both models were retuned and retrained. Serving checks pass; investment evidence does not justify replacing production weights.</div>
<ul>{''.join('<li>'+escape(reason)+'</li>' for reason in decisions['rationale'])}</ul>
<p>54 unresolved price flags, not 54 companies, were quarantined. Only suspect observations and dependent feature windows are excluded.
Uncertain future outcomes remain in selection accounting. True failed-bank losses remain. No alternate vendor return is assumed correct.</p>
<p>The price-review ledger is retrospective data-quality evidence, not proof that these exclusions were known at each historical signal date.</p>
<p><strong>Reading the tables:</strong> stock basket means are equal-weight returns before costs. Median basket is the median across signal dates;
within-basket median is the typical selected stock, averaged across dates. Sector-relative returns deduct 15 bp. These are overlapping holding-period baskets, not CAGR or expected returns.</p></section>
{''.join(sections)}<section id="deployment"><h2>Serving Checks</h2><p>Frozen real-price replay through {deployment['as_of']}, using production scoring functions and real cache-scoring subprocesses.</p>
{table(['Horizon', 'Names scored', 'All ranks match', 'Max score error'], checks)}
<p>JSON contracts, cross-tab metrics, positive stops, rebound-only activation, artifact/SHAP hashes, private cached focus scoring and stale-cache rejection passed.
No public refresh, paid AI call or GitHub deployment was performed. Insufficient-history names: {escape(', '.join(deployment['excluded_symbols']))}.</p>
</section></main></html>'''
    path = output / "latest_summary.html"
    path.write_text(html, encoding="utf-8")
    print(path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True)
    args = parser.parse_args()
    render(ROOT / args.experiment)
