# Controlled Model Comparison: November 18, 2026

**Follow-up:** this document preserves the earlier comparison and prepublication check. The subsequent [fixed-recipe tuning and deployment report](model-deployment-check-2026-11-18.md) records new six-recipe searches, retraining, compatible feature-versioned scoring, candidate SHAP and end-to-end replay. Its implementation resolves the earlier serving-compatibility blocker, but economic/membership limitations still prevent promotion.

## Decision

Keep the deployed models unchanged. The correctness fixes improve the reliability of the research, but this experiment does **not** establish a reliably better model. Fresh, full-data research candidates are saved separately and must not be deployed using the legacy feature engine.

This study freezes adjusted daily prices through **2026-11-17**. All results below are holding-period returns of equally weighted top-decile baskets, not portfolio CAGR, not a probability forecast and not an expected future return. Sector-relative returns subtract the matching sector ETF return and a fixed 15 bp cost. Stock returns in the tables are before that cost. Historical membership, sector and terminal-event gaps remain; the study is not survivorship-free.

## Changes Tested

1. All arms share the same frozen prices, calendar-aligned label dates, common feature rows, hyperparameters, seed and next-close-aware temporal exclusions. Test data is never supplied to early stopping.
2. A legacy all-trees diagnostic uses patience trees after the best validation iteration. Corrected evaluation retains only the selected best trees.
3. A matched corrected-feature fit changes gain-only RSI windows from 50 to 100, leaves warm-up missing and stops imputing missing volume as zero. This is feature version 2; live version 1 remains compatible with existing weights.
4. A tactical comparison evaluates the frozen May incumbent and newly fitted fold models on the same post-export signals. A separate full-data candidate is not evaluated on its own training rows.

Each horizon has four chronological folds. Tactical uses 42-session test blocks and daily signals; strategic uses 252-session test blocks and monthly signals. The study retains the 10-year tactical and 15-year strategic raw-history settings. It is a bounded refit/feature comparison, not a new hyperparameter or history-window search.

## Did Performance Improve?

### Correcting Evaluation

These comparisons use the same complete basket dates across all three arms:

| Mean top-decile sector-relative return, net | 14D | 252D |
| --- | ---: | ---: |
| All-trees diagnostic | +0.41% | +31.74% |
| Correct best-tree evaluation, legacy features | +0.16% | +30.55% |
| Correct best-tree evaluation, corrected features | +0.16% | +30.55% |

Correct evaluation reduced the measured result. That is not evidence that the deployed fixed-round weights became worse: those artifacts have not changed. It is evidence that the older research evaluation used a different booster than intended.

Corrected features produced **exactly the same saved test predictions in all eight fold comparisons**. The v1/v2 tactical matrices differ in 249,719 of 1,112,053 common rows, including cross-sectional ranks affected by RSI and missing-value changes. Nevertheless, the selected trees did not use RSI splits, and the feature changes did not alter selected splits/predictions. The effect estimate is zero in this experiment, not a general claim that RSI or missing-value handling never matters.

### Corrected Historical Results

| Metric | Tactical | Strategic |
| --- | ---: | ---: |
| Signal dates tested | 2026-02-27 to 2026-10-27 | 2021-12-01 to 2025-11-03 |
| Tested cohorts | 168 daily | 48 monthly |
| Complete paired top-decile baskets | 153 | 42 |
| Mean basket stock return | +1.32% | +45.38% |
| Median basket stock return | +0.68% | +38.37% |
| Mean of within-basket member medians | -0.09% | +20.03% |
| Mean basket sector-relative return, net | +0.16% | +30.55% |
| Median basket sector-relative return, net | +0.29% | +21.94% |
| Top-minus-bottom sector-relative spread | +0.22% | +31.84% |

The basket median describes a typical selection date's equal-weight return. The member median describes a typical stock within each basket, averaged over dates. The difference between means and member medians indicates important positive-tail concentration, especially strategically. Neither 153 overlapping daily outcomes nor 42 overlapping yearly outcomes represents that many independent market experiments. The large strategic results require particular caution because the surviving/current-snapshot universe can retain successful future winners while missing historical failures.

Fifteen tactical and six strategic baskets contain at least one unresolved selected outcome. They are marked incomplete, not averaged over the surviving stocks. The comparison uses common complete dates, which is transparent but still potentially biased by missing outcomes. Top-minus-bottom spread additionally requires a complete bottom basket.

### Recent Refit Versus Frozen Tactical Incumbent

On 42 common signal dates from **2026-08-28 through 2026-10-27**, all after the incumbent's May export:

| Metric | Frozen May model | Corrected fold refit |
| --- | ---: | ---: |
| Mean basket stock return | -3.64% | -2.93% |
| Mean sector-relative return, net | -2.94% | -2.70% |
| Top-minus-bottom spread | -3.10% | -3.22% |

The sector-relative improvement is **+0.24 percentage points**, with an approximate paired moving-block 95% interval of **-0.25 to +0.64 points**. Both absolute and sector-relative outcomes are negative; spread also deteriorated slightly. This is not compelling promotion evidence.

This comparison does not isolate recent data alone: the refit also uses the corrected pipeline, refreshed reference universe and validation-selected tree count (four trees in the latest tactical fold, versus 25 in the frozen artifact). Future tuning must not relabel these now-inspected dates an untouched holdout.

There are **no fully matured post-export one-year outcomes** for the May 2026 strategic model as of November 2026. Its historical walk-forward results cannot establish that a new strategic model beats the frozen model in subsequent live conditions. First such outcomes become available around May 2027, subject to the exact trading calendar and entry delay.

## Saved Research Candidates

| Property | Tactical candidate | Strategic candidate |
| --- | --- | --- |
| Feature version | 2 | 2 |
| Raw-history window | 10 years | 15 years |
| Sampling | Daily | Monthly |
| Matured training signal dates | 2017-11-17 to 2026-10-27 | 2012-11-20 to 2025-11-03 |
| Known-label training rows | 1,111,948 | 72,692 |
| Fixed full-fit tree count | 25, inherited | 60, inherited |
| Deployment status | Research only | Research only |

Files are separated under `data/modeling/experiments/2026-11-18/comparison_14d/` and `comparison_252d/`. Both include fold models, test predictions, cohort returns, `results.json`, and `candidate_v2_full.json` with source signatures and metadata. The full-fit tree counts are not a newly tuned optimum. The live scorer deliberately rejects these v2 artifacts until a matching feature rollout and promotion evaluation are completed.

The local visual report is `data/modeling/experiments/2026-11-18/comparison.html`; `comparison_summary.json` stores its matched metrics. Raw files and model artifacts remain private/ignored; the methodology and interpretation are versioned documentation.

## Survivorship Work And Remaining Gates

The 518-name original/current snapshot union preserves former original members, but it is not historical S&P membership. The audit discovered 311 distinct removed tickers over 15 years; 286 have no prices in this experiment. Six recent former-name provider histories shrank to one final row. Their older archived segments are retained separately, with no fabricated splice across missing months. An ended history is not itself proof of a completed acquisition or bankruptcy.

Implemented contracts preserve removed-name history, distinguish new-entry eligibility from existing outcomes, and require verified security identity, dates, consideration and share units for terminal accounting. A bankruptcy filing is not an automatic -100%; only verified worthless cancellation warrants zero recovery. Unknown outcomes remain visible and cannot influence who gets ranked into a basket through a future-data availability filter.

Not complete: the historical security master, point-in-time sectors, all former-member prices, daily forward archival, and verified terminal events applied to labels. The [corporate-action policy](survivorship-and-corporate-actions.md) specifies these boundaries and primary sources.

Before promotion: reconcile recent terminal/alias cases and coverage, extend reliable historical membership, evaluate simple momentum/low-volatility baselines and cost/exposure sensitivity, reserve a genuinely later holdout, and refresh SHAP for the actual candidate chosen. Do not run a broad hyperparameter search against the latest window merely to make its results look better.

## Reproduction And Tests

Commands and the full acceptance contract are in [the research README](../analysis/model-audit/README.md). `npm run verify` includes the research regression tests and Python syntax checks. Tests cover tree selection, temporal purging, feature versions, missing volume, immutable cache resume, moved membership sources, unknown-outcome basket handling and corporate-action accounting. Production weight hashes remain the ones recorded in [the original audit](model-audit-2026-11-18.md).

## Pre-Publication Sanity Check

The subsequent request to publish **only if the candidates are ready** resulted in a **hold**, not a model promotion. No production weights, refresh outputs or deployment settings were changed or pushed.

Checks rerun against the actual saved candidate files:

| Check | Tactical | Strategic |
| --- | --- | --- |
| Model hash matches research metadata | Pass | Pass |
| 156 unique, correctly ordered features; no declared identity/target columns | Pass | Pass |
| Training dates/count match known-label matrix rows | Pass | Pass |
| Finite predictions on 512 sampled rows | Pass | Pass |
| Save/reload predictions identical | Pass | Pass |
| Maximum SHAP-sum reconstruction error | 5.96e-8 | 8.94e-8 |
| Current production artifact scores a cached test cross-section | 507 rows, pass | 513 rows, pass |
| V2 candidate accepted by current V1 scorer | Correctly rejected | Correctly rejected |

These are offline cached-data probes, not a fresh dashboard run or an additional performance backtest. The full verification suite passed again: 92 JavaScript tests and 57 Python tests, plus syntax and dashboard artifact checks.

Publication blockers:

1. **Feature compatibility:** production scoring and its shared tactical/strategic reference cache still use version 1. Both candidates require version 2. Removing the version guard would silently mix feature definitions rather than fix compatibility.
2. **Artifact contract:** research metadata intentionally lacks the production keys `feature_columns`, `target_column`, `return_column`, `model_path`, `training_start_date` and `training_end_date`. Promotion requires a validated export and matching model-specific SHAP/performance artifacts, not copying or renaming research files.
3. **Exact training recipe:** the saved full-data candidates have inherited fixed counts of 25/60 trees. Evaluated tactical folds selected 66/22/89/4 trees; strategic folds selected 25/14/22/27. The earlier walk-forward numbers assess the early-stopping procedure, not an independently validated fixed-round deployment recipe. A matched fixed-round replay must train only on each fold's prior data; scoring full-fit candidates on outcomes they trained on is not acceptable evidence.
4. **Economic evidence:** the tactical improvement is uncertain, latest-fold spread worsened, and strategic live superiority is unmeasured. Historical member/sector/terminal-outcome gaps affect both the incumbents and candidates. Keeping the incumbent is not a statement that it has a proven profitable edge.

Recommended sequence: evaluate the exact fixed-round recipe against incumbents and simple baselines on properly timed folds; reconcile the recent unresolved securities and quantify historical coverage; implement feature-versioned shadow scoring with separate outputs and matched explanations; then decide on promotion using documented performance and operational gates. Shadow scoring means private comparison only, with no effect on dashboard recommendations. It has **not** been enabled by this sanity check.
