# Historical Data Rebuild And Model Retuning

> **Follow-up reconciliation:** counts and model results below describe the initial candidates. A later audit found incomplete halt masking and an upstream Cintas dividend error, added 21 terminal records, and rebuilt separate corrected matrices. Unknown labels are now 428 / 912, not 601 / 1,165; 54 of 64 vendor discrepancies still need resolution. Candidate performance below has **not** been rerun on corrected matrices. See [causes, repairs and exclusion policy](historical-data-reconciliation-2026-11-18.md).

## Decision

Actual historical data has now been integrated and both models retuned/retrained in isolated research directories. This is more than the earlier contracts-only change. **Neither new candidate is promoted.** The tactical test remains weak; strategic evidence is positive but thin, sensitive to missing claims, and does not establish improvement over the inherited recipe. The reconstruction substantially reduces current-constituent bias but does not justify a claim of complete or unbiased historical data.

Prices are frozen through **2026-11-16**, the archive's latest SPY session. This is one session earlier than the preceding snapshot-union experiment and is not a live dashboard refresh. Production model hashes are unchanged.

## Data Added

| Measure | Tactical | Strategic |
| --- | ---: | ---: |
| Raw history / signal frequency | 10 years / daily | 15 years / monthly |
| Historical securities with price records | 700 | 804 |
| Securities with complete features | 658 | 747 |
| Securities with known training labels | 658 | 739 |
| Known training rows | 1,120,989 | 76,572 |
| Distinct training dates | 2,246 | 157 |
| Training signal start | 2017-11-16 | 2012-11-19 |
| Training signal end | 2026-10-26 | 2025-11-03 |
| Former securities retained in feature matrix | 160 | 268 |
| Former-security feature rows | 158,178 | 17,302 |
| Average date-level complete-feature coverage | 99.08% | 98.33% |
| Minimum date-level complete-feature coverage | 94.06% | 92.28% |
| Labels recovered with verified terminal consideration | 361 | 264 |
| Remaining unknown point labels | 601 / 1,121,590 (0.054%) | 1,165 / 77,737 (1.50%) |
| Securities affected by unknown labels | 65 | 106 |
| Unknown point labels with contractual bounds | 42 | 31 |

Counts distinguish raw coverage, feature eligibility and label maturity. Recent additions, short histories, sparse classifications and newly created sector ETF histories do not automatically produce eligible training rows. Unknown outcomes are excluded from fitting but retained when constructing portfolios; their selected baskets cannot be given a fabricated complete point return.

Sources are frozen with hashes: [historical price/membership archive](https://github.com/Johnbrick123/sp500-data), [archived IVV sector observations](https://github.com/riazarbi/sp500-scraper), and 31 reviewed event records in `config/research-terminal-events.json`. This is community-sourced reconstruction with issuer/SEC/exchange evidence for individual actions, not a licensed institutional security master. Do not redistribute downloaded data merely because the repository is public.

The 187 monthly sector snapshots are applied one session after observation. January-June 2017 is missing from the source archive. Classifications carry forward from the last observation, expire after 366 calendar days, and have age diagnostics; future sector values are never backfilled. That is an acknowledged historical classification approximation, not permission to carry stale market prices. Old/new Chubb, Johnson Controls, Allergan and other identities use reviewed ISIN mappings rather than ticker-only joins.

Both candidates use **142 features**, removing 14 volume/liquidity features from the standard 156 because some former securities have closes but no reliable volume. Missing volume is not filled with zero. The remaining inputs use the same version-2 Python technical engine. Eligibility filtering happens before breadth, cross-sectional percentiles and sector medians. Neither ticker IDs nor eventual acquisition/bankruptcy flags are predictors.

## Terminal Accounting

Removed names continue to contribute their observed post-removal outcomes for previously eligible signals. Cash acquisitions use verified consideration, followed by zero-interest cash through the original horizon. Stock/mixed deals value the contractual share ratio at the first realizable successor session, reconcile split-adjusted share units and then liquidate to cash. That policy is explicit; it does not splice two companies' price histories together. Evidence availability and original label maturity both restrict training.

Observed failures now appear in the data. Examples, not model recommendations:

- SIVB signal 2023-03-07: observed next-close 14D stock return about **-99.85%**.
- SBNY signal 2023-03-07: about **-99.87%** over the same horizon.
- FRC signal 2023-04-13: about **-97.58%** over 14D.
- Old Peabody security, monthly signal 2014-08-01: about **-93.31%** over 252D.

These returns come from observed archived trading paths, not an automatic rule that bankruptcy means -100%. Halt/carry rows are not treated as executable prices.

Walgreens illustrates an irreducible point-valuation problem: its acquisition delivered $11.45 cash plus a non-transferable right to up to $3.00. A missing market quote for that right is not evidence of zero value. [OCC completion notice](https://infomemo.theocc.com/infomemos?number=57160). WBA, HOLX and ABMD therefore have cash-to-contractual-maximum sensitivity bounds, not invented point labels. Bounds are not quoted liquidation values, confidence intervals or training targets.

## Tuning And Results

Six predetermined fixed recipes per horizon were evaluated on two earlier validation windows. Temporal exclusions are 15 sessions for next-close 14D labels and 253 sessions for next-close 252D labels. Selection uses common complete validation baskets only: 84 tactical dates but just seven strategic dates. Each full refit uses the same selected tree count evaluated on the later development block. No test returns select the recipe.

| Selected candidate | Tactical | Strategic |
| --- | --- | --- |
| Recipe | slow regularized | longer fit |
| Trees / depth | 50 / 3 | 120 / 2 |
| Learning rate | 0.02 | 0.025 |
| Minimum child weight / L2 | 160 / 8 | 80 / 4 |
| Final tuning + full-fit elapsed | 74.9 seconds | 11.6 seconds |

All returns below are equal-weight **top-decile holding-period returns**, not annualized strategy returns. Sector-relative figures subtract each selected stock's sector ETF return and 15 bp. The inherited comparison is refitted on the **same rebuilt dataset**, not the frozen production model. Earlier study headline figures are not directly comparable: universe, sector history, feature policy, data adjustments and dates changed.

### Tactical: 42 Recent Daily Cohorts

Signals: 2026-08-27 through 2026-10-26. All 42 top baskets are complete across the four methods.

| Method | Mean stock return | Median basket stock return | Mean sector-relative return |
| --- | ---: | ---: | ---: |
| Newly tuned | -3.91% | -5.66% | -2.84% |
| Inherited recipe refit | -3.65% | -5.46% | -2.47% |
| 60D momentum | -0.99% | -0.76% | -0.93% |
| Low volatility | +1.42% | +3.19% | +0.35% |

The tuned model is 0.37 percentage points worse than the inherited refit. An exploratory 14-cohort block interval is approximately -1.28 to +0.40 points, so this is not strong evidence of a difference. It is nevertheless **not evidence supporting promotion**. Forty-two overlapping daily labels are not 42 independent trials.

### Strategic: 12 Monthly Cohorts

Signals: 2024-12-02 through 2025-11-03, each followed for 252 trading days. Only four dates have complete point-valued top baskets across all four methods. On those four dates the tuned model's mean stock return is +54.38%, median basket return +40.05%, and mean sector-relative return +33.24%, versus +31.78% sector-relative for the inherited recipe. **Those attractive four-date figures are not the headline conclusion.** They select a subset of overlapping outcomes.

Including contractual contingent-rights bounds lets the tuned and inherited recipes each be assessed across all 12 test dates:

| Method | Test dates with complete bounds | Mean sector-relative return range |
| --- | ---: | ---: |
| Newly tuned | 12 / 12 | +26.76% to +27.01% |
| Inherited recipe refit | 12 / 12 | +27.48% to +27.73% |
| 60D momentum | 12 / 12 | +13.56% to +13.65% |
| Low volatility | 11 / 12 | -6.49% to -6.40% |

The final row uses fewer dates and is not a matched 12-date comparison. The selected and inherited recipes contain identical uncertain WBA exposures in these baskets, so their shared uncertainty cancels in the paired difference: tuning is about **0.71 percentage points worse** across all 12 dates. One year of monthly, overlapping annual outcomes is far too little independent evidence to claim a stable advantage or expected annual return.

Exact TreeSHAP artifacts are saved beside each candidate and reconstruct predictions within tolerance. Tactical importance is led by cross-sectional 60D volatility, distance to the 52-week high and relative volatility. Strategic importance is led by sector-relative and cross-sectional volatility, distance from the 52-week low and beta. These remain risk/reversion-sensitive rankers, not pure trend-following models.

## Verification And Remaining Work

- Full repository verification: 92 JavaScript tests, 83 Python tests, syntax/compile checks and existing dashboard contracts passed. New fixtures cover dated eligibility, re-entry, ticker reuse, ISIN conflicts, sector availability/expiry, halted prices, cash/mixed consideration, split units, weekend valuation, contingent-rights bounds and feature/label separation.
- Frozen matrix audit checks every retained row against membership intervals, duplicate identities, finite features, label maturity, source hashes and candidate artifact contracts. Artifact replays and SHAP checks pass. This is **not a full daily-refresh deployment replay of the new 142-feature artifacts**.
- Across 11 current securities/benchmarks and 4,031 overlapping sessions each, the largest cross-cache daily return discrepancy was 0.0771 percentage points. This checks transformations/revisions; both caches contain Yahoo-derived records and are not independent vendors.
- The upstream audit still lists **64 price-return discrepancies** within the fifteen-year window. They are retained in a review ledger, not assumed corrected or used as a reason to erase inconvenient losses.
- The 601 tactical / 1,165 strategic unknown point labels, 2017 sector archive gap, unmatched classifications and historical identity review remain explicit gates. Free-source integration is materially improved, not finished institutional-grade reconciliation.

Next work should reconcile the per-security review queue and archive discrepancies, expand chronological validation, and compare the same recipes on matched data/feature sets. A licensed corporate-action/price history source may reduce manual reconstruction work; no paid data was purchased or used here. Do not promote merely because retraining completed.

## Saved Outputs

Reproduce using [the audit commands](../analysis/model-audit/README.md#historical-membership-rebuild). Generated files are ignored and kept outside GitHub Pages:

- `data/modeling/experiments/2026-11-18-pit/historical_rebuild_audit.json`
- `price_only/coverage_*d_by_date.csv` and `coverage_*d_by_security.csv`
- `price_only/unresolved_*d_review.csv`
- `sector-observations-*d.csv` and `sector-unmatched-*d.csv`
- `archive-price-discrepancy-review.csv`
- `price_only/tuning_14d/` and `price_only/tuning_252d/`: weights, metadata, exact fixed-recipe results, test predictions, complete-basket/bounded returns and SHAP.

The original production model hashes remain `a0046b8279fc65620805ad9f703157752ed73a0ea29502bbf15116b512e30605` (14D) and `3545669ef52a35abd59b760a942c43757c83b70d9b022c573c0cbbb84ea75496` (252D). No dashboard deployment, commit or push was performed as part of this research request.
