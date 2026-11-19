"""Render the paired model study as a local, self-contained HTML report."""
from __future__ import annotations

import argparse
from html import escape
import json
from math import isfinite
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]


def pct(value):
    return f"{100 * value:+.2f}%" if value is not None and isfinite(value) else "Unavailable"


def table(headers, rows):
    return '<div class="table-scroll"><table><thead><tr>' + ''.join(f'<th>{escape(str(h))}</th>' for h in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join(f'<td>{escape(str(cell))}</td>' for cell in row) + '</tr>' for row in rows) + '</tbody></table></div>'


def effect_conclusion(effect):
    if effect.get("status") == "no_common_cohorts":
        return "No common complete baskets: there is no comparable performance estimate."
    if effect.get("status") == "insufficient_blocks":
        return "Too few complete dependence-length blocks to estimate an uncertainty interval."
    delta = effect["mean_difference"]
    low, high = effect["approximate_95pct_block_interval"] or (float("nan"), float("nan"))
    if delta == 0:
        return "No measured performance difference on these matched baskets."
    if low <= 0 <= high:
        direction = "improved" if delta > 0 else "declined"
        return f"The point estimate {direction}, but the interval includes zero."
    return "The paired interval excludes zero; this remains exploratory evidence, not a promotion decision."


def finite_json(value):
    if isinstance(value, dict):
        return {k: finite_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite_json(v) for v in value]
    return None if isinstance(value, float) and not isfinite(value) else value


def interval_text(effect):
    bounds = effect.get("approximate_95pct_block_interval")
    return f"{bounds[0] * 100:+.2f} to {bounds[1] * 100:+.2f} pp" if bounds is not None else "unavailable (insufficient complete blocks)"


def comparison(folder, horizon):
    result_dir = folder / f"comparison_{horizon}d"
    result = json.loads((result_dir / "results.json").read_text())
    cohorts = pd.read_csv(result_dir / "cohorts.csv")
    main_stages = ["corrected_evaluation", "corrected_features"]
    paired = cohorts.pivot(index="date", columns="stage", values="excess_mean")[main_stages].dropna()
    label = "Tactical / 14 trading days" if horizon == 14 else "Strategic / 252 trading days"
    if paired.empty:
        return f'<section id="h{horizon}"><h2>{label}</h2><p>No common complete baskets; no performance comparison is available.</p></section>', {"horizon": horizon, "common_complete_cohorts": 0, "status": "no_common_cohorts"}
    matched = cohorts[cohorts.date.isin(paired.index) & cohorts.stage.isin(main_stages)]
    means = matched.groupby("stage")[["stock_mean", "stock_member_median", "excess_mean", "excess_member_median", "spread"]].mean()
    medians = matched.groupby("stage").excess_mean.median()
    stock_medians = matched.groupby("stage").stock_mean.median()
    names = {"corrected_evaluation": "Legacy features, corrected evaluation", "corrected_features": "Corrected features, corrected evaluation"}
    rows = [[names[s], pct(means.loc[s, "stock_mean"]), pct(stock_medians[s]), pct(means.loc[s, "stock_member_median"]), pct(means.loc[s, "excess_mean"]), pct(medians[s]), pct(means.loc[s, "spread"])] for s in main_stages]
    effect = result["feature_correction_effect"]
    delta = effect["mean_difference"]
    conclusion = effect_conclusion(effect)
    overview = f'<section id="h{horizon}"><h2>{label}</h2><p>{escape(result["test_start"])} to {escape(result["test_end"])}. {len(paired)} paired complete cohorts; {"daily" if horizon == 14 else "monthly"} observations.</p>'
    overview += f'<p class="verdict">Feature correction changed average top-decile sector-relative return by <strong>{delta * 100:+.2f} percentage points</strong>. Approximate block interval: {interval_text(effect)}.</p><p>{conclusion}</p>'
    overview += table(["Version", "Mean basket stock return", "Median basket stock return", "Mean of basket member medians", "Mean sector-relative return, net", "Median cohort sector-relative return", "Top-minus-bottom spread"], rows)
    overview += '<p class="note">Both rows use the same complete top-basket dates. Member medians describe the typical stock inside each selected basket; cohort medians describe the typical equal-weight basket across dates. Spread requires complete top and bottom baskets. These are holding-period returns, not annualized portfolio returns.</p>'
    counts = cohorts.groupby("stage").agg(cohorts=("date", "size"), incomplete_top=("unresolved_top", lambda s: int(s.gt(0).sum())), missing_top_members=("unresolved_top", "sum"))
    overview += '<h3>Coverage</h3>' + table(["Version", "Test cohorts", "Incomplete top baskets", "Unresolved selected outcomes"], [[names.get(s, "All-trees diagnostic"), int(r.cohorts), int(r.incomplete_top), int(r.missing_top_members)] for s, r in counts.iterrows()])
    incumbent = result["incumbent_comparison"]
    incumbent_means = []
    if incumbent["status"] == "no_fully_mature_post_export_signals":
        overview += '<h3>Frozen incumbent</h3><p>No fully matured post-export one-year outcomes exist yet for the May 2026 artifact. Historical walk-forward comparisons are not live confirmation of this model.</p>'
    else:
        recent = pd.read_csv(result_dir / "recent_incumbent_cohorts.csv")
        stages = ["frozen_incumbent", "corrected_evaluation", "corrected_features"]
        dates = recent.pivot(index="date", columns="stage", values="excess_mean")[stages].dropna().index
        recent = recent[recent.date.isin(dates) & recent.stage.isin(stages)]
        grouped = recent.groupby("stage")[["stock_mean", "stock_member_median", "excess_mean", "spread"]].mean()
        incumbent_means = grouped.reset_index().to_dict("records")
        overview += f'<h3>Fresh refit versus frozen incumbent</h3><p>{incumbent["start"]} to {incumbent["end"]}; {len(dates)} common post-export cohorts. The fold models were trained and validated before this test window. Full-data candidates are separate and are not evaluated on their own training rows.</p>'
        if len(dates):
            overview += table(["Model", "Mean stock return", "Mean member median", "Mean sector-relative, net", "Spread"], [[names.get(s, "Frozen May model"), *[pct(grouped.loc[s, c]) for c in grouped.columns]] for s in stages])
            recent_effect = incumbent["feature_candidate_vs_incumbent"]
            overview += f'<p>Refit minus incumbent: {recent_effect["mean_difference"] * 100:+.2f} pp, approximate block interval {interval_text(recent_effect)}. {effect_conclusion(recent_effect)}</p>'
        else:
            overview += '<p>No common complete baskets for the incumbent comparison.</p>'
    overview += '</section>'
    return overview, {"horizon": horizon, "common_complete_cohorts": len(paired), "feature_effect": effect,
        "means": means.reset_index().to_dict("records"), "median_cohort_excess": medians.to_dict(), "median_cohort_stock": stock_medians.to_dict(),
        "incumbent": incumbent, "incumbent_common_means": incumbent_means}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True)
    args = parser.parse_args()
    folder = ROOT / args.experiment
    sections, summaries = [], []
    for horizon in (14, 252):
        section, summary = comparison(folder, horizon)
        sections.append(section); summaries.append(summary)
    audit = json.loads((folder / "survivorship_audit.json").read_text())
    manifest = json.loads((folder / "manifest.json").read_text())
    as_of = escape(manifest["as_of"])
    archived = sum(bool(record.get("archived_segment")) for record in manifest["histories"].values())
    missing = len(audit["removed_tickers_without_experiment_prices"])
    body = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Market Pulse / Model comparison</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;background:#f7f9fa;color:#20292d;margin:0}}header,main{{max-width:1200px;margin:auto;padding:24px}}header{{border-bottom:1px solid #bac8cc}}h1{{font-size:28px;line-height:1.2}}h2{{font-size:23px}}h3{{font-size:18px}}nav{{display:flex;gap:24px;flex-wrap:wrap}}a{{color:#006c69}}section{{padding:28px 0;border-bottom:1px solid #cbd3d7}}.warning{{border-left:4px solid #ba7615;padding:12px 18px;background:#fff4dd}}.verdict{{font-size:18px}}.note{{color:#536269;font-size:14px}}.table-scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:12px;text-align:right;border-bottom:1px solid #d7dfe3;min-width:110px}}th{{background:#e8eff1;font-weight:600;vertical-align:bottom}}th:first-child,td:first-child{{text-align:left;min-width:180px}}tr:last-child td{{background:#e7f3ee}}@media(max-width:650px){{header,main{{padding:16px}}h1{{font-size:24px}}section{{padding:20px 0}}}}</style>
<header><h1>Market Pulse / Model comparison</h1><p>Frozen price snapshot through {as_of}. Research candidates only; deployed models unchanged.</p><nav><a href="#h14">Tactical</a><a href="#h252">Strategic</a><a href="#universe">Universe &amp; corporate actions</a></nav></header><main>
<p class="warning"><strong>Not a survivorship-free backtest.</strong> The study retains original/current snapshot names and archived former-name segments, but {missing} historical removed tickers have no prices in this experiment. Terminal events and historical sectors are not fully reconciled. No candidate is automatically promoted.</p>
{''.join(sections)}
<section id="universe"><h2>Removed companies remain part of the record</h2><p>Discovery identified {audit['historical_removal_events']} removal events and {audit['unique_removed_tickers']} unique removed tickers in the study window. {archived} earlier archived histories are retained separately where provider histories became too short. Unknown outcomes are not zero returns.</p>
<ul><li>Index removal: stop new S&amp;P-only entries, but finish existing holding-period outcomes.</li><li>Cash deal: verified consideration, then cash through the original label exit.</li><li>Stock/mixed deal: verified exchange value in matching share units; primary research policy liquidates at the event.</li><li>Bankruptcy: no automatic -100% at filing. Confirm cancellation/recovery; new equity gets a new security identity.</li><li>Unknown outcome: preserve and flag it. Do not remove it before ranking a historical basket.</li></ul>
<p class="note">These event-accounting contracts are implemented and tested, but no unverified corporate-action return has been applied to this study. A verified security master and event reconciliation are still required.</p></section></main></html>'''
    (folder / "comparison.html").write_text(body, encoding="utf-8")
    (folder / "comparison_summary.json").write_text(json.dumps(finite_json(summaries), indent=2, allow_nan=False) + "\n")
    print(folder / "comparison.html")


if __name__ == "__main__":
    main()
