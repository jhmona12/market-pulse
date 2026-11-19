# Model Audit And Controlled Comparisons

These are private research tools, separate from daily refresh and GitHub Pages. No command here replaces the deployed artifacts.

## Commands

From the repository root, using a **completed** exchange session:

```bash
.venv-model/bin/python analysis/model-audit/prepare_experiment.py --as-of 2026-11-17 --output data/modeling/experiments/2026-11-18
.venv-model/bin/python analysis/model-audit/audit_survivorship.py --experiment data/modeling/experiments/2026-11-18
.venv-model/bin/python analysis/model-audit/run_comparison.py --experiment data/modeling/experiments/2026-11-18 --horizon 14
.venv-model/bin/python analysis/model-audit/run_comparison.py --experiment data/modeling/experiments/2026-11-18 --horizon 252
.venv-model/bin/python analysis/model-audit/render_comparison.py --experiment data/modeling/experiments/2026-11-18
```

Use a new output directory for a new snapshot date. Price responses, source hashes, cached feature matrices, model variants, predictions and reports live in ignored `data/modeling/experiments/`. A resumed fetch reuses its frozen files, not live dashboard caches. The original May research caches and deployed weights are preserved.

Resume verifies previously recorded price hashes and fails on modified/missing frozen files instead of silently fetching revised history. The HTML report and `comparison_summary.json` use matched complete baskets and distinguish basket medians from within-basket member medians. The [November results](../../docs/model-comparison-2026-11-18.md) provide the saved interpretation and deployment decision.

`audit_saved_models.py` is a separate, read-only replay of the May artifacts and caches. Its older comparisons isolate early stopping without adding fresh data. Do not compare those figures with the new experiment as though their dates and universes were identical.

## Experiment Contract

- 10-year raw window / daily samples for tactical; 15-year raw window / globally anchored monthly samples for strategic. Technical warm-up and future maturity shorten usable signal dates.
- Four chronological folds: tactical 42-session validation/test blocks with 15-session exclusions; strategic 252-session blocks with 253-session exclusions. Splits use full trading sessions even when rows are monthly.
- Fixed inherited hyperparameters and seed, four CPU threads, maximum 300 rounds, early-stopping patience 40/50. This is not a new hyperparameter search.
- Each v1 fit generates an all-trees diagnostic and a best-tree result. A matched v2 fit tests corrected RSI and missing-volume treatment using the same labels, common feature rows, dates and parameters.
- All arms use calendar-aligned raw histories, not row-compressed missing sessions. Legacy tree evaluation is shown only as a diagnostic; the audited gap is used throughout.
- RSI v1 retains legacy behavior for the frozen deployed models. V2 uses 100 for gain-only windows, 0 for loss-only windows, 50 for fully flat windows and missing during warm-up. V2 does not turn missing volume into zero. This retains the rolling-average RSI definition; it does not introduce Wilder smoothing as an additional unmeasured change.
- Rank before excluding unknown future outcomes. Incomplete selected baskets have no asserted portfolio return. Paired estimates use common complete dates; coverage remains visible.
- Stock mean, median member return, sector-relative return and top-minus-bottom spread are different metrics. Returns cover 14/252 trading days, not a compound strategy CAGR. A 15 bp cost applies to sector-relative returns.
- Approximate moving-block intervals preserve 14 daily or 12 monthly cohorts per block. Fewer than two full blocks, or missing baskets that would compress the regular calendar, returns an explicit unavailable result, not an interval. They are exploratory, not proof of significance across independent market regimes.
- Tactical frozen-incumbent comparisons are restricted to the latest fold after export. The May 2026 strategic artifact has no matured post-export one-year outcomes as of November 2026.
- This first comparison saved separate full-data v2 candidates with inherited 25/60 round counts. Their own training rows are never used as an out-of-sample performance claim. The subsequent tuning path below evaluates and exports exact fixed recipes separately. The live scorer is now metadata-version-aware; unchanged production models still use v1 and reject v2 feature caches.

## Fixed-Recipe Tuning And Deployment Replay

After the frozen matrices above exist:

```bash
.venv-model/bin/python analysis/model-audit/tune_fixed_candidates.py --experiment data/modeling/experiments/2026-11-18 --horizon 14
.venv-model/bin/python analysis/model-audit/tune_fixed_candidates.py --experiment data/modeling/experiments/2026-11-18 --horizon 252
.venv-model/bin/python analysis/model-audit/test_deployment.py --experiment data/modeling/experiments/2026-11-18
.venv-model/bin/python analysis/model-audit/test_deployment.py --experiment data/modeling/experiments/2026-11-18 --incumbents
node analysis/model-audit/test_dashboard_assembly.mjs data/modeling/experiments/2026-11-18
.venv-model/bin/python analysis/model-audit/render_deployment.py --experiment data/modeling/experiments/2026-11-18
```

Six predetermined recipes are fitted per horizon across two earlier validation windows, selecting only common complete validation baskets. The chosen fixed recipe is compared with an inherited fixed refit and simple momentum/low-volatility baselines on the last later test window. The frozen incumbent is included only on post-export signals. Previously inspected test dates remain development data. Full candidates use the exact evaluated recipe and have their own metadata, source hashes and SHAP. Outputs go to `tuning_14d/` and `tuning_252d/`; they do not overwrite the earlier comparison.

Deployment replay uses real frozen prices and actual production scoring/feature functions, with only external provider/constituent boundaries replaced. The first replay can fetch five missing market-strip ETFs into a separate local cache; subsequent runs reuse them. Actual cache-scoring CLI subprocesses test both horizons, all-rank/metric parity, stops, activation, private cached focus scoring and stale-cache rejection. `--incumbents` separately checks unchanged production weights. No paid API keys, news refresh or GitHub deployment are involved. The JS check exercises actual pure assembly functions and schemas without fetching market caps or using AI.

Read `deployment_readiness.html` and [the deployment decision](../../docs/model-deployment-check-2026-11-18.md). Passing the serving checks is not automatic permission to promote weights. Feature-cache serialization version 2 preserves tiny inputs and invalidates older lossy caches; the ordinary refresh rebuilds its tactical cache before the strategic phase.

## Interpretation

The study keeps the original/current snapshot union for a controlled comparison. It still has universe, historical-sector and missing-outcome bias. It is not a verified historical S&P 500 backtest and is not an automatic promotion gate. See [corporate-action and survivorship policy](../../docs/survivorship-and-corporate-actions.md) and [the original audit](../../docs/model-audit-2026-11-18.md).

Tests live in `tests/test_model_research.py`, `tests/test_rank_model_contracts.py` and `tests/test_candidate_deployment.py`; run them through `npm run verify` before considering a model promotion. The large frozen-price replay is an additional explicit acceptance test, not part of every scheduled refresh.

## Historical Membership Rebuild

This is a **different experiment** from the snapshot-union comparisons above. It adds historical member prices and reviewed terminal consideration, then tunes/retrains both horizons. Do not compare its headline returns with the older study as if only one variable changed: the universe, historical classifications, source adjustment methodology and feature policy differ.

```bash
.venv-model/bin/python -m pip install -r analysis/model-audit/requirements.txt
.venv-model/bin/python analysis/model-audit/fetch_historical_archive.py --output data/modeling/experiments/2026-11-18-pit/archive
.venv-model/bin/python analysis/model-audit/fetch_sector_history.py --output data/modeling/experiments/2026-11-18-pit/sectors --start 2010-10-01 --as-of 2026-11-16
.venv-model/bin/python analysis/model-audit/build_historical_matrix.py --experiment data/modeling/experiments/2026-11-18-pit --horizon 14
.venv-model/bin/python analysis/model-audit/build_historical_matrix.py --experiment data/modeling/experiments/2026-11-18-pit --horizon 252
.venv-model/bin/python analysis/model-audit/tune_fixed_candidates.py --experiment data/modeling/experiments/2026-11-18-pit/price_only --horizon 14
.venv-model/bin/python analysis/model-audit/tune_fixed_candidates.py --experiment data/modeling/experiments/2026-11-18-pit/price_only --horizon 252
.venv-model/bin/python analysis/model-audit/audit_historical_rebuild.py --experiment data/modeling/experiments/2026-11-18-pit --reference data/modeling/experiments/2026-11-18
```

The final audit additionally uses the original frozen reference cache for a cross-cache return check. That is a transformation check, not independent vendor validation. Run the original preparation command above if that reference directory does not exist.

Raw sources are hash-pinned and reused. Matrices are deliberately rebuilt after code or event-registry changes; tuning stores their hashes, and the audit rejects candidates made from earlier matrix versions. The two horizons have separate matrices, weights, metadata, predictions and SHAP. `price_only/` uses 142 features: the standard v2 technical features minus 14 volume/liquidity features that are unavailable for some former securities. No missing volume is invented. `--feature-policy full` builds a separate 156-feature dataset, but is not the default new candidate.

Review `historical_rebuild_audit.json`, `price_only/unresolved_*d_review.csv`, per-date/per-security coverage, the dated sector matching ledgers and `archive-price-discrepancy-review.csv`. Bounds in test cohorts are sensitivity ranges for uncertain claims, not point returns, confidence intervals or training targets. All outputs remain outside GitHub Pages. The frozen archive ends November 16, one session earlier than the original comparison cache, and is not presented as a live dashboard refresh. Read [the results and remaining gates](../../docs/historical-model-rebuild-2026-11-18.md) before promotion.

## Outcome And Price Reconciliation

The follow-up fixes source-verified issues without overwriting initial matrices/candidates. Run from the repository root with the frozen archive and reference cache already present:

```bash
.venv-model/bin/python analysis/model-audit/build_historical_matrix.py --experiment data/modeling/experiments/2026-11-18-pit --horizon 14 --output-root data/modeling/experiments/2026-11-18-pit/reconciled
.venv-model/bin/python analysis/model-audit/build_historical_matrix.py --experiment data/modeling/experiments/2026-11-18-pit --horizon 252 --output-root data/modeling/experiments/2026-11-18-pit/reconciled
.venv-model/bin/python analysis/model-audit/diagnose_historical_gaps.py --experiment data/modeling/experiments/2026-11-18-pit --matrix-root data/modeling/experiments/2026-11-18-pit/reconciled --reference data/modeling/experiments/2026-11-18 --output data/modeling/experiments/2026-11-18-pit/diagnosis/after
.venv-model/bin/python -m unittest discover -s tests -p 'test_historical*.py'
npm run verify
```

The diagnostic rejects corrected matrices built against outdated event/halt/price-review registries. It verifies matrix integrity and historical eligibility, separates unknown causes, reconciles publisher flags, measures direct trailing-feature/forward-label exposure and reports potential shared-context effects. Exposure CSVs are a sensitivity proposal, **not an applied deletion filter**. It never substitutes a comparator return just because it differs, guesses an unpriced right, or removes unknown outcomes before portfolio selection.

Primary evidence is in `config/research-terminal-events.json`, `config/research-no-trade-periods.json` and `config/research-price-reviews.json`. Matrix hashes include these registries; downloaded files remain unchanged. The subsequent quarantine study below refits these corrected matrices and a separately quarantined arm. Older results must not be labeled corrected-data results. Read [the detailed reconciliation](../../docs/historical-data-reconciliation-2026-11-18.md).

## Targeted Quarantine Experiment

After reconciliation, run these commands for each horizon (`14` and `252`):

```bash
.venv-model/bin/python analysis/model-audit/build_historical_matrix.py --experiment data/modeling/experiments/2026-11-18-pit --horizon 14 --output-root data/modeling/experiments/2026-11-18-pit/quarantine --quarantine-ledger data/modeling/experiments/2026-11-18-pit/diagnosis/after/price-disagreements.csv
.venv-model/bin/python analysis/model-audit/run_quarantine_experiment.py --experiment data/modeling/experiments/2026-11-18-pit --horizon 14
.venv-model/bin/python analysis/model-audit/tune_fixed_candidates.py --experiment data/modeling/experiments/2026-11-18-pit/reconciled/price_only --horizon 14
.venv-model/bin/python analysis/model-audit/tune_fixed_candidates.py --experiment data/modeling/experiments/2026-11-18-pit/quarantine/price_only --horizon 14
```

The frozen review ledger must have the predeclared 54 unresolved flags. The
baseline and treatment must match source hashes, features and calendars.
Quarantine masks disputed bars and excludes affected predictor windows **before**
breadth/rank construction. Future label uncertainty is separate: it cannot remove
an earlier portfolio candidate. The comparison asserts unchanged unaffected
labels and retains verified failed-bank losses. No entire issuer is deleted.

After both horizons finish:

```bash
.venv-model/bin/python analysis/model-audit/test_deployment.py --experiment data/modeling/experiments/2026-11-18 --candidate-root data/modeling/experiments/2026-11-18-pit/quarantine/price_only --output-dir data/modeling/experiments/2026-11-18-pit/quarantine/price_only/deployment
node analysis/model-audit/test_dashboard_assembly.mjs data/modeling/experiments/2026-11-18-pit/quarantine/price_only
.venv-model/bin/python analysis/model-audit/render_quarantine_report.py --experiment data/modeling/experiments/2026-11-18-pit
npm run verify
```

`quarantine-comparison/latest_summary.html` is the private side-by-side report;
`release-decision.json` records the reviewed November 18 decision and evidence.
`quarantine/price_only/tuning_*d/` contains newly fitted weights, metadata and exact
TreeSHAP, but is **not** a production artifact directory. The HTML renderer records
this study's hold decision; it is not an automatic model-approval system.
See [methodology and results](../../docs/targeted-quarantine-experiment-2026-11-18.md).
