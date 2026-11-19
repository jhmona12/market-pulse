# Model Audit: November 18, 2026

## Scope And Decision

Reviewed the two deployed rank artifacts, dataset builders, shared features, temporal splits, tuning, model export, SHAP, live scoring, and recent-performance monitoring. Ran a full cached-dataset scan and a replay of saved research folds. This review did not fetch fresh market data, retrain/promote production weights, or publish the dashboard.

**Follow-up:** the subsequently authorized [controlled comparison](model-comparison-2026-11-18.md) did freeze fresh prices through November 17, implement versioned RSI/missing-volume corrections, rerun four folds per horizon, and fit separate research candidates. The original audit findings/replay below remain provenance; they are not the new study's results. Production weights remain unchanged, and the historical universe remains incomplete. See [the survivorship policy](survivorship-and-corporate-actions.md).

**Recommendation:** refresh the research data and evaluate new challengers, tactical first. Correct validation and feature/data defects before selecting replacements. Age alone does not prove that the deployed models have stopped working. Historical tuning results are not reliable enough to justify automatic promotion.

## What Is Actually Deployed

| Property | Tactical | Strategic |
| --- | --- | --- |
| Holding label | 14 trading days | 252 trading days |
| Intended raw-history window | About 10 years | About 15 years, filtered from a longer cache |
| Sampling | Daily | First available date each month |
| Saved training signal dates | 2017-05-04 to 2026-04-10 | 2012-05-23 to 2025-05-01 |
| Training observations | 1,082,323 | 70,734 |
| Training dates | 2,246 | 157 |
| Names | 502 | 500 |
| Export date | 2026-05-04 | 2026-05-22 |
| Features | 156 | 156 |
| Trees / maximum depth | 25 / 3 | 60 / 2 |
| Learning rate | 0.04 | 0.025 |

Both learn a cross-sectional relevance grade, not a probability or a price target. The economic outcome is stock return minus sector ETF return minus 15 bp, with entry at the next adjusted close and exit 14 or 252 sessions after entry. Each signal date's outcomes become grades 0-4. A constant 15 bp deduction changes reported net returns but not the within-date ordering of labels.

The same 156 feature definitions cover trend, momentum, relative strength, volatility, liquidity, beta, sector context and market breadth. Ticker, sector identity, date and target columns are not inputs. Neither model currently includes news, SEC fundamentals or point-in-time macro releases. The SEC and historical-membership scripts are audits, not integrated training features.

The strategic daily dataset ends on 2025-05-19; first-of-month sampling makes its actual fitted last signal date May 1. A 15-year raw-history window is not 15 complete labeled years: trailing warm-up and a year of forward outcomes shorten it. Daily refreshes use new inputs with fixed weights, not daily retraining. The live scorer's roughly three-year price fetch is enough for trailing features; it is not the training lookback.

## Findings And Changes

### P1: Early-Stopping Evaluation Used The Wrong Trees

`train_rank_model.py` and `walk_forward_rank_model.py` predicted with every saved tree, including patience rounds after `best_iteration`. Tuning and ablation used that same path; SHAP could explain the full rather than selected booster. Native XGBoost does not automatically select the best iteration. [XGBoost prediction documentation](https://xgboost.readthedocs.io/en/stable/prediction.html).

**Fixed:** `rank_validation.best_iteration_model()` trims once to the inclusive best iteration. Predictions, saved research boosters, and SHAP then use the same trees. Tests cover prediction equality, SHAP additivity and save/reload consistency. New evaluation reports identify `evaluation_version: 2` and the tree policy. The fixed-round deployed artifacts have no early-stop attribute and are unchanged.

### P1: Monitoring Called Pre-Export Dates Strict Out Of Sample

`analysis/model-monitoring/run_recent_decile_backtest.py` started its strict window immediately after the last training signal date. But the future outcomes of those training signals were already used to fit the model. For the tactical artifact, April 11 was not a valid post-availability boundary.

**Fixed:** start after the saved export date, conservatively May 5 for the May 4 artifact; reject earlier overrides or missing export metadata. The old `strict_post_training` key and filenames remain for compatibility, but display text says post-export monitoring. The monitor also rejects non-14D models/horizons instead of silently using its hard-coded 14D outcome columns. Historical monitoring reports need regeneration. This still does not remove survivorship bias or make overlapping outcomes independent.

### P2: Temporal Gaps Did Not Include The Entry Delay

Signal at `t`, entry at `t+1`, exit at `t+h+1`: an `h`-date exclusion can leave the last prior-block outcome ending on the next block's first signal date. The generic default was also 14 even when callers selected a 252D target. Saved strategic research explicitly used 252, not 14; monthly sampling may further widen some actual boundaries.

**Fixed:** infer the matching target/return horizon and require at least 15 excluded trading dates for tactical, 253 for strategic. Split before monthly sampling. Reject explicit undersized gaps and mismatched horizons. Tests assert that prior-block exits strictly precede the next block's first signal. Chronological splits and explicit gaps follow the principles described in [scikit-learn's time-series split documentation](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html).

### P2: The Experimental Drawdown Label Was Misdefined

The old long-horizon calculation measured the worst loss from entry, not the worst peak-to-trough decline. Entry 100, peak 150, exit 120 had old "drawdown" 0%, although the actual drawdown is 20%.

**Fixed:** compute each complete entry-to-exit path relative to its running peak. Mark rebuilt datasets `label_version: 2`; training, tuning and export reject old/unversioned alternate drawdown labels. The deployed primary sector-return label is unchanged. Rebuild and reevaluate the experiment before drawing conclusions from the old comparison. The tactical legacy meta-label still uses entry-relative adverse excursion; it is not either deployed rank target.

### P2: Cached Price Gaps Could Change The Label Horizon

The builder dropped invalid closes and then shifted by row count. Missing sessions can make stock and benchmark labels cover different calendar windows. The current raw research cache has one internal SPY-calendar gap in FISV on 2025-11-12. Most files end 2026-05-21; BK and CTRA end 2026-05-01. This is the research cache, not evidence about the separate live-scoring cache.

**Fixed:** training loads reject invalid/nonpositive closes, duplicate/null dates, internal benchmark-calendar gaps and histories ending before the benchmark. Shorter IPO histories are allowed when complete within their available span. Do not forward-fill or fabricate missing trades. A new build may intentionally fail until these cache issues are repaired. Using SPY as the reference does not independently prove the vendor's calendar or prices are correct.

### P2: Dataset Metadata And Rank Score Semantics

Custom tactical builds wrote the default dataset's metadata file, while feature selection ignored dataset-specific metadata for non-default datasets. Rank outputs also copied raw rank scores into a misleading `predicted_probability` column.

**Fixed:** read/write the metadata corresponding to each dataset; reject identity/target features, duplicate keys, non-finite inputs and invalid grades before fitting. New rank predictions contain `predicted_rank_score` only; older probability-based classifiers are unchanged. SHAP no longer requires that misleading alias. Production export validates matching target/return horizons.

## Remaining Risks Before Retraining

- **Survivorship and sector-history bias:** both builders use current members and today's sector assignments throughout history. Historical losers/removals and past classifications are not reliably reconstructed. The free point-in-time audit is incomplete; a delisting flag known only afterward must never become a historical feature.
- **RSI feature definition:** rolling average loss of zero is replaced with missing and then filled with 50. An all-gains window should be 100. The raw-cache audit found 1,142 such windows across 2,381,669 rows, including ETFs. Undefined early RSI is also filled with 50, and missing volume with zero; the previous blanket "no imputation" documentation was inaccurate. Correct these intentionally in a versioned rebuild and switch matching training/live feature code together. Do not silently feed revised features into old weights.
- **Selection bias:** tuning, window choice and sampling choice reused historical development folds. The four-fold comparison is not independent confirmation of the two-fold selection. Correcting tree selection does not repair this research-selection bias.
- **Outcome dependence:** 157 monthly dates are not 157 independent one-year bets. Adjacent monthly labels share about eleven months. Daily tactical labels overlap too. Use block-aware uncertainty and non-overlapping cohort views, not stock-row counts as the statistical sample size.
- **Reproducibility:** raw research caches are shared and overwritten by later fetches; the strategic 15-year dataset was a filtered 20-year build. A new export should preserve the date filter, source dates, hashes, feature version and label version. A failed fetch must not silently reuse an older symbol history.
- **Economic interpretation:** sector-relative ranking can favor oversold, high-volatility names; it is not an absolute-return forecast or a pure momentum rule. Stops/activation levels are separate technical rules, not outputs learned by the rank objective. Liquidity metrics are adjusted-price-based proxies, and a flat cost does not model market impact, taxes or turnover.

## Evidence

Full scans of both saved datasets found:

| Check | Tactical | Strategic daily cache |
| --- | ---: | ---: |
| Rows scanned | 1,082,323 | 1,473,215 |
| Duplicate stock/date keys | 0 | 0 |
| Non-finite feature cells | 0 | 0 |
| Non-finite primary target cells | 0 | 0 |
| Stock minus sector minus 15 bp arithmetic mismatches | 0 | 0 |
| Forbidden identity/target feature columns | 0 | 0 |

These are consistency checks on saved datasets, not independent reconstruction of every original vendor price, dividend adjustment or constituent history.

Replaying the saved research folds on each fold's first available monthly test dates isolates the early-stopping issue:

| Probe | Tactical | Strategic |
| --- | ---: | ---: |
| Monthly date/fold probes | 16 | 52 |
| Mean top-decile membership retained after correction | 91.2% | 84.6% |
| Old mean of cohort basket excess returns | 2.59% | 29.72% |
| Best-tree mean of cohort basket excess returns | 2.52% | 28.31% |
| Old median of cohort basket excess returns | 3.07% | 23.26% |
| Best-tree median of cohort basket excess returns | 2.50% | 22.59% |

Returns here are sector-relative after the fixed cost, not raw stock returns or annualized portfolio returns. Median means median across cohort equal-weight basket returns, not median member-stock return. The JSON also stores within-basket member medians. Fold boundaries can add another probe within a calendar month. This replay does not fix the old splits, add fresh data or provide a new untouched test. Its purpose is to quantify whether tree selection matters.

## Retraining Plan

1. Freeze the current artifacts and export dates. Preserve hashes and the old reports.
2. Refresh and validate raw histories, constituent mapping and benchmark coverage. Correct/version RSI and intentionally define missing-volume behavior. Preserve separate tactical and strategic dataset windows.
3. Rebuild only fully matured labels. As of November 2026, tactical outcomes can extend into late October and strategic outcomes into roughly November 2025, depending on the latest completed exchange session. Derive exact cutoffs from the verified calendar, not calendar-day subtraction.
4. Rerun audited historical folds, then compare frozen incumbents on untouched, fully matured post-export dates. Any subsequent challenger selection makes those dates development data; reserve a later chronological holdout for the final decision. Never evaluate a newly trained artifact on dates it just trained on.
5. Start with 10-year daily tactical and 15-year monthly strategic settings. Compare top-decile mean and member median, median cohort basket return, sector-relative spread, drawdown, turnover/cost sensitivity and sector/volatility/liquidity exposures against simple momentum and low-volatility baselines. Do not select solely on NDCG or hit rate.
6. Keep tuning compact and separate for each horizon. Promote only after feature parity, saved-artifact replay and fresh holdout checks pass; refresh the matching SHAP artifacts. No automatic age-based replacement.

## Reproduce And Verify

```bash
.venv-model/bin/python analysis/model-audit/audit_saved_models.py
.venv-model/bin/python -m unittest discover -s tests -p 'test_rank_model_contracts.py' -v
npm run verify
```

The read-only audit requires the ignored local training caches and saved fold artifacts. It makes no API calls. Detailed output is stored under `data/diagnostics/model-audit-2026-11-18.json`; it is not a dashboard artifact.

Regression tests cover both next-close horizons, best-tree predictions/SHAP/serialization, fixed-round artifact preservation, purging and sampling, label metadata, raw calendar quality, feature leakage, dataset metadata isolation and the post-export monitoring boundary. The full project verification also runs syntax, existing source/AI/refresh tests and dashboard artifact contracts.

Unchanged deployed model SHA-256 values:

```text
tactical  a0046b8279fc65620805ad9f703157752ed73a0ea29502bbf15116b512e30605
strategic 3545669ef52a35abd59b760a942c43757c83b70d9b022c573c0cbbb84ea75496
```
