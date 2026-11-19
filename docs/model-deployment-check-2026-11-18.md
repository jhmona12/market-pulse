# Bounded Tuning And Deployment Readiness

## Decision

Both models were newly tuned and retrained as **private candidates**, using the frozen November 17, 2026 price snapshot. Neither candidate is promoted. Production model files and dashboard data are unchanged.

The scoring and dashboard-assembly replays pass, but the economic evidence does not justify replacing the incumbents. Keeping an incumbent is not an endorsement of its profitability. Serving-path corrections can be released separately from changing weights.

This follows the [controlled comparison](model-comparison-2026-11-18.md). That earlier study tested evaluation/feature corrections, not a new hyperparameter search. Its test dates had already been inspected: the following results are development evidence, not an untouched confirmation set.

## What Was Trained

| Property | Tactical | Strategic |
| --- | --- | --- |
| Label horizon | 14 trading days | 252 trading days |
| Intended raw history | 10 years | 15 years |
| Sampling | Daily | Monthly, anchored across the universe |
| Fully matured training rows | 1,111,948 | 72,692 |
| Distinct training dates | 2,246 | 157 |
| Distinct training symbols | 514 | 513 |
| Training signal dates | 2017-11-17 to 2026-10-27 | 2012-11-20 to 2025-11-03 |
| Features | 156, version 2 | 156, version 2 |
| Selected fixed recipe | `longer_fit` | `shorter_fit` |
| Trees / maximum depth | 50 / 3 | 30 / 2 |
| Learning rate | 0.04 | 0.025 |
| Minimum child weight / L2 penalty | 80 / 4 | 80 / 4 |
| Row / column sampling | 0.85 / 0.8 | 0.9 / 0.85 |

Training uses the original/current snapshot union, not just today's names. Today's reference has 503 securities; 500 have sufficient complete history. The raw-history windows lose their first year to feature warm-up and their final 14/252-session outcomes to label maturity. A one-year candidate cannot train on today's signal as though its future outcome were already known.

Both models use `rank:ndcg` with date-level groups and five relevance grades derived from stock total return minus sector-ETF total return minus 15 bp. Entry is the following session's close. These are ranking scores, not probabilities or predicted percentage returns. A sector-relative target does not itself enforce a sector-neutral portfolio. No news or fundamentals were added in this pass.

## Bounded Search

`analysis/model-audit/tune_fixed_candidates.py` tests six predetermined recipes per horizon: inherited fixed count, half count, double count, shallower/more regularized, slower/more regularized, and a depth-three alternative. Maximum tested count is 120 trees. Each uses seed 42, four CPU threads and the existing histogram tree method.

Selection maximizes mean top-decile sector-relative return after the fixed cost, with median return as tie-breaker, on common complete baskets across two earlier validation windows. It does not pick a winner from the later test returns. Temporal exclusions are 15/253 exchange sessions, accounting for next-close entry and the full label horizon. The full-data candidate uses the **exact same fixed recipe/count** evaluated on the later fold; it is not evaluated against outcomes included in its own final fit.

| Horizon | Earlier validation signal windows | Later test signal dates |
| --- | --- | --- |
| Tactical | 2026-04-08 to 2026-06-05; 2026-06-08 to 2026-08-06 | 2026-08-28 to 2026-10-27 |
| Strategic | 2021-11-08 to 2022-11-07; 2022-11-08 to 2023-11-08 | Monthly signals 2024-12-02 to 2025-11-03 |

Recorded tuning, full fitting and candidate SHAP took approximately 78 seconds tactical and 8 seconds strategic. Price preparation and deployment testing are separate costs. This is an efficient local search, not a globally optimal configuration or a proof that these windows/features are best.

## Later-Window Results

Each row summarizes equal-weight top-decile holding-period baskets on matched complete dates. Stock returns are before costs; sector-relative returns deduct 15 bp. Median basket return is the median across signal dates, not the median individual stock. These are overlapping baskets, **not portfolio CAGR**.

### Tactical

All 42 test baskets were complete.

| Strategy | Mean stock return | Median basket return | Mean sector-relative return |
| --- | ---: | ---: | ---: |
| Frozen production incumbent | -3.64% | -5.11% | -2.94% |
| Refit inherited fixed recipe, 25 trees | -3.16% | -4.47% | -2.72% |
| Validation-selected candidate, 50 trees | -3.50% | -5.44% | -2.46% |
| Simple 60-session momentum | -0.50% | +0.60% | -0.16% |
| Simple low volatility | +1.52% | +3.21% | +0.33% |

The selected recipe improves sector-relative return by 0.26 percentage points versus the fixed refit, but worsens raw mean and median returns. Its exploratory paired 95% block interval versus that refit is -0.58 to +1.08 points. It improves by 0.49 points versus the frozen incumbent but still trails both simple baselines. Forty-two overlapping daily cohorts contain only about three holding-period-length blocks; these figures cannot establish robust superiority across market regimes.

### Strategic

Only **6 of 12** monthly baskets have complete outcomes across all comparison arms. Missing selected outcomes invalidate a basket rather than removing the missing stock and reporting the survivors' average.

| Strategy | Mean stock return | Median basket return | Mean sector-relative return |
| --- | ---: | ---: | ---: |
| Refit inherited fixed recipe, 60 trees | +96.65% | +82.05% | +77.34% |
| Validation-selected candidate, 30 trees | +93.83% | +80.21% | +75.12% |
| Simple 60-session momentum | +25.14% | +28.81% | +10.46% |
| Simple low volatility | +4.41% | +5.63% | -8.65% |

The selected recipe is worse than the inherited fixed refit by 2.22 percentage points sector-relative. Its average within-basket median stock return is +40.07%, substantially below its +93.83% basket mean. Outliers, overlapping outcomes and incomplete historical coverage make these large figures unsuitable as expected-return estimates. Restricting to complete baskets does not remove selection bias from missing baskets.

No uncertainty interval is reported: six complete monthly cohorts are fewer than even one 12-month dependence block. The corrected bootstrap refuses to report an interval below two full blocks; that minimum guard is not a claim that two blocks would provide strong inference. The strategic incumbent has no matured post-export one-year test yet; this is not a valid live challenger-versus-incumbent comparison.

## Are The Choices Defensible?

Yes for constrained research; **not as a blanket validation of deployment**.

- Approximately 500 names provide useful within-date ranking breadth, not 500 independent market histories. Stocks share shocks, and adjacent holding-period labels overlap. Raw row counts materially overstate independent evidence.
- Daily tactical and monthly strategic sampling preserve the separate use cases. Monthly sampling reduces duplicate yearly outcomes; it does not make them independent. We did not retune history length based on these results.
- The 156 features include many transformations of related price/volume signals. Shallow trees, shrinkage, regularization and a six-recipe budget constrain capacity. The feature count itself is not justified by a rows-per-feature rule, and a controlled feature-family ablation is still worthwhile.
- Validation is chronological, with label-maturity exclusions. Model selection and the later fixed-recipe test are separated, but previous inspection means genuinely new future observations are still needed for confirmation.
- Mean/median returns, simple baselines, missing-outcome coverage and exposure concentration matter more than a single hit-rate statistic. A fixed 15 bp cost is a simplifying assumption, not a realistic turnover/tax/capacity simulation.
- The larger unresolved threat is historical constituent/sector/terminal-outcome bias. The study identified 311 removed tickers, 286 without prices in this experiment. Technical corrections and a small tuning search do not solve that gap.

These principles follow the grouped-ranking structure in [XGBoost's learning-to-rank documentation](https://xgboost.readthedocs.io/en/stable/tutorials/learning_to_rank.html), its [capacity/regularization controls](https://xgboost.readthedocs.io/en/stable/parameter.html), and the separation of model selection from later evaluation described in [scikit-learn's cross-validation guidance](https://scikit-learn.org/stable/modules/cross_validation.html). Specific budgets and windows here are project choices, not prescriptions from those sources.

## Deployment Evidence

`analysis/model-audit/test_deployment.py` replays hash-verified frozen real price responses through the actual production `main` path. Only external constituent/provider boundaries and diagnostic output paths are replaced. The strategic and tactical cache consumers run as real CLI subprocesses. The same replay runs separately for unchanged production artifacts and corrected-feature candidates.

- 503/503 histories have November 17 closes; 500 feature-complete names score in both models. FDXF, HONA and VYLR lack sufficient feature history and are disclosed as unscored.
- All 500 ranks match direct versus cached scoring for both horizons and both artifact families. Maximum score differences are below 0.000001 from six-decimal output formatting.
- Closing prices, displayed trailing returns, beta and YTD match across direct/cached paths. Required stop prices are positive; activation appears only on rebound-watch setups.
- Freshness rejects an intentionally outdated cache. In-universe private Ticker Lab scoring uses the exact matching artifact without an additional price request.
- Real JS assembly functions produce a 500-row tactical scorebook, 500-row strategic book and 50-name monitor. JSON schemas, cross-tab metric agreement and candidate-specific SHAP/model hashes pass.
- Full repository verification passes: 92 JavaScript tests, 69 Python tests, syntax and dashboard artifact checks.
- No public artifacts, production weights, workflow settings or live deployment were changed. This is not proof of GitHub scheduler/network reliability, market-cap/news enrichment or paid AI behavior. No visual UI redesign was made.

### Defects Found And Corrected

1. **Reference-cache precision:** pandas' default ten-decimal JSON formatting rounded tiny liquidity inputs to zero. Before correction, the tactical candidate replay changed 358 of 500 ranks and one top-decile member between direct and cached inference. Full-precision float serialization fixes this; format version 2 rejects old lossy caches.
2. **Stop coverage:** a second model can promote names not previously surfaced by the tactical model. The reference cache now retains dashboard/stop metrics for all reference names, including calendar-based 60D return.
3. **YTD denominator:** displayed YTD now uses the previous year's final available close, not the first January close. It stays null when no prior-year baseline exists. This does not alter trained model features or weights.
4. **False bootstrap precision:** short strategic samples no longer resample a whole sample as one block and imply a zero-width interval.
5. **Artifact compatibility:** metadata, feature order, tree count, label horizon, feature version and model hashes are checked. Cached scores must belong to the exact model; feature caches can be shared across horizons only with matching feature definitions. Production defaults remain version 1; version 2 is opt-in via validated candidate metadata.

Candidate-specific exact TreeSHAP summaries are saved for both new artifacts, using 2,048 evenly spaced history rows and an additivity check. Tactical importance is led by volatility ranks; strategic importance is led by liquidity (`amihud_20d_pct_rank`) and volatility. Mean absolute SHAP is importance, not effect direction or causal explanation. These summaries are not substituted for incumbent explanations.

## Files And Next Gate

Reproducible commands are in [the research README](../analysis/model-audit/README.md). Ignored outputs live under `data/modeling/experiments/2026-11-18/`:

- `tuning_14d/` and `tuning_252d/`: validation/test cohorts, exact evaluated recipes, full-fit candidates, metadata, source hashes and matching SHAP.
- `deployment/` and `deployment-incumbents/`: isolated full/cache replays, diagnostics and machine-readable acceptance results.
- `deployment_readiness.html`: side-by-side performance and deployment report.

Release the independently tested serving corrections with a fresh full tactical cache rebuild before the strategic phase. Do not simply relabel an old cache as version 2. Keep candidate weights private, reconcile terminal histories and historical membership, and predefine a genuinely later evaluation interval before any replacement decision. A scheduled shadow comparison has not been enabled by this work.
