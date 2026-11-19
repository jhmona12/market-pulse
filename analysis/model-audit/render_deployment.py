"""Render tuning evidence and deployment checks without changing the dashboard."""
from __future__ import annotations

import argparse
from html import escape
import json

from render_comparison import ROOT, table, pct, interval_text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True)
    args = parser.parse_args()
    folder = ROOT / args.experiment
    sections = []
    for horizon in (14, 252):
        result = json.loads((folder / f"tuning_{horizon}d/results.json").read_text())
        params = result["params"]
        metric_rows = [[row["stage"], pct(row["mean_stock_return"]), pct(row["median_stock_return"]),
                       pct(row["mean_member_median"]), pct(row["mean_excess"]), pct(row["spread"])] for row in result["test_summary"]]
        effects = [[control, f"{effect['mean_difference']*100:+.2f} pp", interval_text(effect)] for control, effect in result["effects"].items()]
        title = "Tactical / 14 trading days" if horizon == 14 else "Strategic / 252 trading days"
        sections.append(f'''<section id="h{horizon}"><h2>{title}</h2>
<p>Selected on earlier validation dates: <strong>{escape(result['selected_recipe'])}</strong>. {result['rounds']} trees, depth {params['max_depth']}, learning rate {params['eta']}. Six recipes, two validation windows. Full-data candidate retrained with exactly that fixed recipe.</p>
<p>Later test: {result['test_start']} to {result['test_end']}. {result['common_complete_test_cohorts']} complete common baskets out of {result['test_cohorts']} tested. Test dates were previously inspected, so this is development evidence.</p>
{table(['Strategy','Mean stock basket','Median stock basket','Mean member median','Mean vs sector, net','Top-minus-bottom spread'], metric_rows)}
<p class="note">Stock returns are before costs; sector-relative returns subtract 15 bp. These are overlapping holding-period baskets, not portfolio CAGR or expected returns. Missing selected outcomes invalidate the entire basket.</p>
{table(['Selected recipe minus','Mean sector-relative difference','Exploratory block interval'], effects)}
<p class="note">The frozen incumbent appears only when the test signals occur after its original export. No such fully matured test exists yet for the strategic incumbent.</p></section>''')
    deployment = json.loads((folder / "deployment/results.json").read_text())
    incumbents = json.loads((folder / "deployment-incumbents/results.json").read_text())
    assembly = json.loads((folder / "deployment/dashboard-assembly-results.json").read_text())
    assert deployment["status"] == incumbents["status"] == assembly["status"] == "passed"
    checks = [["Candidate", c["horizon"], c["scored_rows"], f"{c['max_direct_cache_score_error']:.2g}", c["identical_all_ranks"]] for c in deployment["checks"]]
    checks += [["Unchanged incumbent", c["horizon"], c["scored_rows"], f"{c['max_direct_cache_score_error']:.2g}", c["identical_all_ranks"]] for c in incumbents["checks"]]
    sections.append(f'''<section id="deployment"><h2>Deployment replay</h2>
<p>Actual scoring functions and cache-scoring subprocess CLI, using frozen real prices through {deployment['as_of']}. {deployment['current_reference_count']} current members available; {deployment['checks'][0]['scored_rows']} feature-complete names scored. Insufficient-history names: {escape(', '.join(deployment['excluded_symbols']))}.</p>
{table(['Artifact','Horizon','Names scored','Max direct/cache score error','All ranks match'], checks)}
<p>Errors below 0.000001 reflect six-decimal output formatting. Candidate-specific metadata/SHAP hashes match. Stale caches are rejected. Model-scorebook and strategic JSON schemas passed; both contain {assembly['scorebookRows']} rows, and the top-decile monitor contains {assembly['monitorRows']} names.</p>
<h3>Serving defects found and fixed</h3><ul><li>Default pandas JSON precision rounded tiny liquidity features to zero. Before correction, direct/cached tactical scoring changed 358 ranks and one top-decile member. Full-precision serialization now preserves model inputs; old caches must be rebuilt.</li><li>Reference caches now retain stop calculations for all names, so a second model can promote a name without losing its stop. Calendar-based 60D returns are retained too.</li><li>YTD display returns now start at the previous year's final close, not the first January close.</li><li>Insufficient monthly samples no longer produce a spuriously zero-width bootstrap interval.</li></ul>
<p class="note">No GitHub deployment, live provider reliability test, news/LLM refresh or market-cap enrichment was performed. Public model weights and dashboard files remain unchanged.</p></section>''')
    body = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Model tuning and deployment readiness</title>
<style>body{{margin:0;background:#f7f9fa;color:#20292d;font:16px/1.5 system-ui,sans-serif}}header,main{{max-width:1200px;margin:auto;padding:24px}}h1{{font-size:28px;line-height:1.2}}h2{{font-size:23px}}h3{{font-size:18px}}section{{padding:24px 0;border-bottom:1px solid #bdcbd0}}a{{color:#006c69}}nav{{display:flex;gap:24px;flex-wrap:wrap}}.warning{{background:#fff4dd;border-left:4px solid #b87515;padding:12px 18px}}.note{{font-size:14px;color:#536269}}.table-scroll{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;font-size:14px}}th,td{{padding:12px;text-align:right;min-width:105px;border-bottom:1px solid #d2dde0}}th{{background:#e8eff1}}th:first-child,td:first-child{{text-align:left;min-width:160px}}@media(max-width:650px){{header,main{{padding:16px}}h1{{font-size:24px}}}}</style>
<header><h1>Model tuning and deployment readiness</h1><p>Operational replay passed. Model promotion remains on hold.</p><nav><a href="#h14">Tactical</a><a href="#h252">Strategic</a><a href="#deployment">Deployment</a><a href="#design">Research design</a></nav></header>
<main><p class="warning">Not a survivorship-free or independent confirmation study. The tactical candidate trails simple baselines in the recent window; the strategic candidate trails its inherited fixed recipe, with only six complete overlapping yearly baskets. No model has been promoted.</p>
{''.join(sections)}
<section id="design"><h2>Are the choices defensible?</h2><p>Yes as a constrained development experiment, not as proof of a trading edge. Around 500 names provide useful cross-sectional breadth, but correlated stocks and overlapping labels do not create hundreds of independent experiments per date.</p>
<ul><li>Retain separate 10-year/daily tactical and 15-year/monthly strategic windows; do not mix horizon labels or tune history length in this pass.</li><li>156 related technical features warrant shallow trees, regularization and a small six-recipe search. Their sufficiency is not proved by raw row count.</li><li>Temporal exclusions are 15/253 exchange sessions, including the next-close entry delay. Test outcomes do not choose the winning recipe.</li><li>Two validation windows limit search cost but cover few regimes. Annual outcomes remain heavily dependent even with monthly sampling.</li><li>Mean, median, simple baselines, incomplete outcomes and dependence-aware uncertainty are reported. No interval is asserted with fewer than two full-length blocks.</li><li>Historical membership, sectors and terminal returns remain the larger evidence gaps. More tuning is not a substitute for repairing those inputs.</li></ul></section></main></html>'''
    path = folder / "deployment_readiness.html"
    path.write_text(body, encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
