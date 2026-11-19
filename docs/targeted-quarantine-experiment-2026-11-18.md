# Targeted Price-Quality Quarantine

**Subsequent deployment decision:** after this study recommended holding, the
user explicitly requested public replacement instead of a shadow rollout. The
quarantine pair is now the selected production release. Economic findings and
the original gate below are preserved, not rewritten as evidence of improvement.
See [the promotion record](model-promotion-2026-11-18.md).

## Predeclared Contract

This is a data-quality sensitivity experiment, not a rule to remove losing
stocks. Freeze the 54 unresolved observations in the reconciled price-review
ledger. Do not quarantine explained corporate actions, resolved halts, or an
entire issuer. Keep both source data and existing production weights intact.

Compare corrected historical-membership matrices with a second rebuild that:

- Masks the disputed close and preceding session's OHLCV values.
- Excludes affected trailing 252-session feature windows before constructing
  breadth and cross-sectional ranks. No market-wide dates are dropped.
- Marks forward outcomes spanning a questionable observation as unknown,
  separately from predictor eligibility. Future label availability must never
  decide whether a stock could be selected at the signal date.
- Preserves missing-outcome counts, corporate-action bounds and real failures.
  Unknown outcomes are not zero returns or verified trading halts.

Both arms use identical time folds, feature lists, costs, seeds and inherited
fixed hyperparameters for the primary comparison. Bounded six-recipe tuning
uses earlier validation folds only; the later test cannot choose exclusions.
Report top-decile mean and median returns, sector-relative returns, missing
outcomes, matched-date differences and coverage. These are overlapping,
previously inspected historical development samples, not untouched tests.

The review ledger was assembled retrospectively. It is a sensitivity study of
today's data-quality knowledge, not proof that these exclusions could have been
known in real time. In particular, a disputed daily return also questions the
preceding close. Do not market this as a historically implementable trading rule
or a fully point-in-time data-vintage backtest.

## Release Gate

Passing implementation tests is necessary but not evidence of investment
value. Check temporal leakage, artifact metadata/hashes, exact feature order,
freshness, direct-versus-cache scores, stop/activation fields, and rollback.
Do not promote a model merely because deleting uncertain observations makes
its reported average rise. A failed or inconclusive economic gate means hold
production weights and retain the rebuilt model as a research candidate.

## Executed Results

Both arms were rebuilt/fitted separately. Sources end November 16, 2026;
production-path replay uses the separately frozen November 17 serving cache.
Neither date is relabeled as a live November 18 market snapshot.

| Data check | Tactical, 14D | Strategic, 252D |
| --- | ---: | ---: |
| Corrected baseline feature rows | 1,121,332 | 77,724 |
| Quarantined feature rows removed | 524 (0.047%) | 180 (0.232%) |
| Retained rows with changed features/context | 135,412 | 27,304 |
| Unknown outcomes, baseline | 428 | 912 |
| Unknown outcomes, quarantine | 458 | 1,061 |
| Newly unknown labels on shared rows | 44 | 165 |
| Verified failed-bank loss rows retained | 17 | 37 |

The total unknown count is a net number: some previously unknown rows also lose
predictor eligibility. The raw quarantine flag touches 45/208 surviving forward
windows; some were already unknown. Neither figure means additional bankruptcies.
Unchanged, unaffected labels match exactly. Historical membership, feature order,
calendar and source registry hashes match across arms.

### Fixed Settings, Four Chronological Folds

These are equal-weight top-decile **holding-period baskets**, not CAGR. Mean and
median stock baskets are gross of costs; sector-relative returns subtract 15 bp.
The median basket is across signal dates, not the median constituent return.

| Horizon / arm | Mean stock basket | Median stock basket | Mean sector-relative |
| --- | ---: | ---: | ---: |
| 14D corrected baseline | +0.63% | +1.01% | -0.45% |
| 14D quarantine | -0.25% | -0.06% | -1.16% |
| 252D corrected baseline | +22.92% | +20.88% | +4.90% |
| 252D quarantine | +24.76% | +14.50% | +5.49% |

Tactical uses all 168 matched complete test dates. Quarantine's mean
sector-relative difference is -0.71 percentage points; its exploratory block
interval is approximately -1.49 to +0.03 points. This does not show improvement.

Strategic has only **24 of 48** common complete monthly baskets. Mean improves
0.60 points, but the median deteriorates. The mean within-basket median stock
return is +12.18% baseline versus +11.97% quarantine, another indication that a
higher average is not broad improvement. Missing baskets are nonrandom. The
bootstrap now refuses to compress gaps into an artificial regular calendar, so
no annual confidence interval is asserted.

### Bounded Tuning And Full Refit

Each arm/horizon tested six predefined recipes on two earlier validation windows.
No exclusions were chosen by their investment returns. The 14D search has 84
common complete validation dates; the annual search has only **seven**, a serious
selection limitation. The final selected recipe is refitted on all matured known
labels, with source hashes, feature-version metadata and exact TreeSHAP stored.

| Quarantine candidate | Tactical | Strategic |
| --- | --- | --- |
| Feature count / version | 142 / v2 | 142 / v2 |
| Training rows | 1,120,350 | 76,483 |
| Training securities with known labels | 658 | 739 |
| Training signal dates | 2017-11-16 to 2026-10-26 | 2012-11-19 to 2025-11-03 |
| Sampling / intended raw history | Daily / 10 years | Monthly / 15 years |
| Selected recipe | shallow_regularized | longer_fit |
| Trees / depth / learning rate | 25 / 2 / 0.04 | 120 / 2 / 0.025 |
| Search + final fit + SHAP elapsed | 116.6 seconds | 19.5 seconds |

The corrected-data control arm was also independently tuned and refitted. The
timings above exclude feature rebuilds, fixed-setting comparisons and serving QA.
All experiments use the same sector-relative label, next-close entry, 15 bp cost
and horizon-aware temporal exclusions as the prior corrected-data research.

Latest tactical test, 42 dates (August 27 through October 26):

| Quarantine strategy | Mean stock basket | Median stock basket | Mean sector-relative |
| --- | ---: | ---: | ---: |
| Selected recipe | -3.60% | -4.19% | -2.76% |
| Inherited recipe, refitted | -3.78% | -4.97% | -2.70% |
| Simple 60-session momentum | -0.99% | -0.76% | -0.93% |
| Simple low volatility | +1.42% | +3.19% | +0.35% |

The annual complete-point comparison has only four common baskets across all
arms. Do not treat its large averages as expected returns. Across **all 12** recent
monthly baskets, verified contingent-rights bounds give the tuned candidate
mean sector-relative return of +26.88% to +27.13%, versus +27.87% to +28.12% for
the inherited recipe refitted on identical data. Thus the apparent advantage in
the four complete baskets does not carry through the full bounded comparison.
These bounds concern cash/maximum contractual rights, not confidence intervals.

## Serving Checks And Decision

- Full verification: 92 JavaScript tests and 96 Python tests passed, including
  five new quarantine/calendar fixtures, plus syntax and JSON artifact checks.
- Real production scoring plus cache CLI replay: 500 feature-complete names for
  each candidate. All direct/cached ranks match; maximum score error is below
  0.0000005 from six-decimal output formatting.
- 503 current histories have the replay date; FDXF, HONA and VYLR lack sufficient
  feature history and remain explicitly unscored, not assigned guessed inputs.
- Stops, rebound-only activation, beta and trailing returns agree across paths.
  Private cached focus scoring, stale-cache rejection, exact model/SHAP hashes,
  500-row dashboard books and 50-row monitor assembly all pass.
- 17 tactical and 37 strategic SIVB/SBNY/FRC outcomes worse than -90% remain;
  quarantine has not cosmetically removed these genuine losses.

**Decision: retain both new models as private research candidates; do not replace
public recommendation weights.** The tactical result is unfavorable, annual
evidence is incomplete and unstable, and tuning does not fix those limitations.
This is not an endorsement of the older production models' investment merit.
Source/corporate-action coverage is improved, not complete or bias-free.

No public refresh, GitHub deployment, paid data purchase or AI call was performed.
The HTML report was generated and its data contracts checked. A visual browser
preview was blocked by the browser's local-file URL policy; no workaround was
attempted, and this is not claimed as a visual UI test.
Production model hashes remain:

- Tactical: `a0046b8279fc65620805ad9f703157752ed73a0ea29502bbf15116b512e30605`
- Strategic: `3545669ef52a35abd59b760a942c43757c83b70d9b022c573c0cbbb84ea75496`

Next release work should separate data-integrity approval from claimed investment
improvement: resolve the remaining selected former-security outcomes, freeze the
candidate recipes, and collect genuinely later shadow results against simple
baselines. Do not keep retuning this already-inspected window until it looks good.
Automatic daily shadow scoring has not been enabled by this experiment.

## Reproduction

See `analysis/model-audit/README.md` for commands. Results are generated under
`data/modeling/experiments/2026-11-18-pit/`, never in the public dashboard data.

- `quarantine-comparison/latest_summary.html`: readable side-by-side results,
  coverage, tuning, candidate SHAP and serving checks.
- `quarantine-comparison/release-decision.json`: evidence and production hashes.
- `quarantine-comparison/{14,252}d/`: matched fixed-setting cohorts and predictions.
- `{reconciled,quarantine}/price_only/tuning_{14,252}d/`: distinct retrained weights,
  metadata, validation/test predictions and SHAP for both arms/horizons.
