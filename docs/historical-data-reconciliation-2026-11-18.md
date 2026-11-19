# Historical Outcome And Price Reconciliation

## Decision

**Do not delete unknown outcomes wholesale.** In the initial historical rebuild, every unknown-label row belonged to a former constituent. Missingness is related to acquisitions, halts and lost vendor coverage, not random sampling. Excluding unknown labels from fitting is unavoidable without reliable targets, but remains a selection limitation. Deleting their research records or excluding them before portfolio ranking would hide that limitation rather than cure it.

This follow-up repairs verified facts and diagnoses the rest. It does **not** promote or retrain weights, refresh the dashboard, or claim that the entire archive is reconciled. Original candidate results remain preserved, but are not performance estimates for the corrected matrices.

**Subsequent work:** the [targeted quarantine experiment](targeted-quarantine-experiment-2026-11-18.md)
has now rebuilt the quarantine arm, retuned/refitted both arms and horizons, and
completed serving checks. Its results and hold decision supersede the proposed
next experiment below; the reconciliation counts in this document remain the
pre-quarantine baseline, not the treatment sample.

## Unknown Outcomes

The old ledger grouped most failures as `requires_event_price_or_entry_review`. That was too vague to distinguish a missing merger payment from an impossible next-close entry or a vendor history gap. The new row-level ledger records signal, entry, exit, last observed trade, sector, reason and whether contractual bounds exist.

Remaining counts after repairs:

| Reason | 14D daily signals | 252D monthly signals |
| --- | ---: | ---: |
| Series ends; terminal/continuation record needs review | 293 | 849 |
| Unpriced contingent rights | 42 | 31 |
| Next-close entry after a verified termination | 37 | 2 |
| Next-close entry after an unreviewed series end | 20 | 3 |
| Entry during a halt | 9 | 0 |
| Exit during a halt | 27 | 0 |
| Missing exit quote inside the history span | 0 | 27 |
| **Total** | **428** | **912** |

The first category is **unfinished reconciliation, not a claim that the outcomes are unknowable**. Remaining acquisitions need verified final terms, successor prices/share units, or shareholder-election treatment. Original offer headlines are insufficient: Life Technologies ultimately paid $76.1311786 rather than the initially proposed $76. [Issuer completion release](https://www.thermofisher.com/br/en/home/about-us/news-gallery/press-releases/2014/thermo-fisher-scientific-completes-acquisition-of-life-technologies-corporation.html).

Four histories have a separate coverage problem: old BBBY, DNB, FTR and MNK. Their cached series end in March 2018; BBBY, DNB and FTR also have substantial gaps before that final date. They account for 36 unknown annual observations, including all 27 missing interior exit quotes. Substituting a later bankruptcy recovery, a current recycled ticker, or an acquisition payment years later would not measure these signals' original one-year returns. They need correct-identity quotes for the actual holding windows.

## Concrete Bugs

### Incomplete Halt Masking

The archive's `no_trade_days.csv` omitted repeated-price bars inside suspension intervals. Its separate `exit_gaps.csv` supplied last actual trade and first reopening trade. Previously, only the explicit list and `carried` source flags were masked.

The loader now combines both with reviewed no-trade periods for BIIB, SIVB, SBNY and FRC. Nasdaq confirms SVB's halt began before the March 10, 2023 regular session; Biogen confirms its June 9, 2023 halt. Reopening observations remain usable. [Nasdaq notice](https://ir.nasdaq.com/news-releases/news-release-details/nasdaq-halts-svb-financial-group), [Biogen notice](https://investors.biogen.com/news-releases/news-release-details/biogen-stock-trading-halted-today-fda-advisory-committee-review).

Returns explicitly use `fill_method=None`, avoiding pandas-version-dependent forward filling. Missing closes must not become zero-return days. Actual crashes remain: approximately -99.85% SIVB and -99.87% SBNY 14D outcomes for March 7 signals, plus observed FRC losses.

Invalid inputs also invalidate affected feature windows: 258 tactical rows (253 BIIB, three SIVB, one SBNY, one FRC) and 13 monthly rows (12 BIIB, one FRC) leave the feature-complete matrix. This is input-quality filtering, not removing issuers because their future returns were bad. Coverage changes remain explicit.

### Missing Special Dividend

The publisher's Cintas override treated its second $0.85 payment in November 2014 as a duplicate. Cintas confirms **$0.85 regular plus $0.85 special**, totaling $1.70. [Cintas announcement](https://www.cintas.com/about/newsroom/details/news/2014/10/22/cintas-corporation-announces-regular-annual-dividend-and-special-dividend/).

The correction converts this to the archive's split-adjusted units, restores the ex-date total return and rebases preceding adjusted prices. Immutable downloads and later price levels remain untouched. The November 5 return changes from about **-0.224% to +0.966%**. Guards reject changed units, unexpected source values and duplicate application. Normalized OHLC uses the same adjustment factor as close.

## The 64 Price Flags

These were **64 vendor-return disagreements**, not 64 proven wrong prices. The publisher's adjudication called 60 undecided, favored its series once, and had no entry for three.

| Disposition | Flagged dates |
| --- | ---: |
| Spin-off/reverse-split holder-return formula reconciles | 4 |
| Confirmed halt masked | 2 |
| Next session compares against a halted prior close | 1 |
| Dividend inclusion confirmed from issuer evidence | 2 |
| Cintas dividend corrected | 1 |
| Unresolved vendor differences | 52 |
| Conflicting issuer-hosted dividend-date evidence | 2 |
| **Total** | **64** |

The four action-formula cases are DD (2019-06-03), DOV (2014-03-03), and HWM (2016-11-01 and 2020-04-01). A shareholder receives retained shares **and distributed shares**; comparing only the retained share price can manufacture discrepancies. Reproducing the archive's formula explains its calculation but does not independently verify every quote.

Pfizer and Gap's flagged returns include verified dividends that the comparator omits. [Pfizer history](https://investors.pfizer.com/Investors/stock-dividend/dividend-split-history/default.aspx), [Gap issuer filing](https://s204.q4cdn.com/320226404/files/doc_financials/2021/q3/ca222f63-5f53-4d7f-8724-8bf5579489b1.pdf).

Among remaining disagreements, 14 adjacent-date pairs almost cancel over two sessions (absolute compounded-return difference below one basis point). This is consistent with a different intermediate close or timing allocation, **not proof of which daily close is correct**. Volatility and an entry/exit on that date can still be wrong.

Norfolk Southern is an evidence conflict: its archived April 28, 2015 press release says a May 1 record date, while its issuer-hosted Mergent table says May 8, with May 6 ex-date. Both associated dates stay flagged rather than choosing a convenient return. [Announcement listing](https://norfolksouthern.investorroom.com/investor-news?l=100&o=100), [dividend table](https://norfolksouthern.investorroom.com/dividend-history).

## Repairs And Coverage

Added 21 primary-sourced completion records: AGN, ALTR-201512, BCR-201712, CA-201811, CSRA-201804, CXO, DRE, ETFC, EVHC-201810, FLIR, HAR-201703, HSP-201509, INFO-202203, LIFE-201402, MON-201806, PCP-201601, PETM-201503, PLL-201508, TIF, WFM-201708 and XL-201809. There are now 52 event records, including three contingent-rights cases. Every record has a source and evidence-availability date.

| Measure | 14D | 252D |
| --- | ---: | ---: |
| Initial unknown labels | 601 | 1,165 |
| Previously unknown labels repaired | 182 | 252 |
| Previously asserted labels correctly made unknown | 12 | 0 |
| Feature-complete rows after repair | 1,121,332 | 77,724 |
| Remaining unknown labels | 428 (0.038%) | 912 (1.17%) |
| Known labels for a subsequent fit | 1,120,904 | 76,812 |

Counts also change when invalid predictor rows leave the matrix; remaining unknowns are not simply initial unknowns minus repaired outcomes. Former constituents account for 426/428 tactical and all 912 annual unknowns. Two additional tactical unknowns concern BIIB's halted entry/exit, not delisting.

## Should Rows Be Dropped?

1. **Verified invalid predictor inputs:** mask bad bars and rebuild features, context and ranks. Implemented for reviewed halts. Do not remove the whole issuer or its valid loss observations.
2. **Unresolved prices:** conservative quarantine is a sensible sensitivity experiment, not a demonstrated unbiased improvement. The 54 remaining dates directly touch 569 tactical rows (0.051%) and 388 annual rows (0.50%), counting trailing-feature and forward-label windows. These are potential exposures, not proven errors in every row.
3. **Shared context:** direct counts are not the full footprint. Dates with direct feature exposure contain 134,938 tactical / 26,994 annual rows across all stocks. Breadth and ranks can propagate corrections. Rebuild from masked bars; do not simply delete rows from an existing matrix or remove all stocks on those dates.
4. **Unknown labels:** keep them missing in fitting, retain them in the ledger and rank/select before inspecting future outcome availability. Selected unknown holdings retain their weights. Do not renormalize onto known survivors or assert complete returns unless every selected outcome has a defensible point value or bound.
5. **Unexecuted entries:** report execution coverage separately. Cash/non-fill portfolio accounting is an additional explicit strategy assumption; this pass does not assign zero stock returns or substitute another security.

Remaining tactical unknowns have a median prior 60D return of +6.32%, versus +2.72% among known-label observations. These descriptive differences and former-member concentration do not estimate the bias's size or direction, but contradict the assumption that deletion is obviously harmless. A small missing percentage is not proof of missing-at-random data.

Next: finish recoverable event/continuation records, obtain correct-identity quotes for incomplete histories, then run fixed-recipe quarantine sensitivity on matched chronological folds. Do not select exclusions by improved realized performance. New out-of-sample evidence remains necessary before promotion.

## Reproduction And Tests

See [commands](../analysis/model-audit/README.md#outcome-and-price-reconciliation). Generated outputs remain ignored under `data/modeling/experiments/2026-11-18-pit/`:

- `reconciled/price_only/matrix_{14,252}_v2.{csv,json,npy}`: corrected matrices, separate from initial candidates.
- `diagnosis/after/summary.json`: counts, source/matrix hashes, missingness and quarantine exposure.
- `diagnosis/after/unknown-*d.csv`: unresolved observations with causes.
- `diagnosis/after/price-disagreements.csv`: all 64 flags, formulas and source checks.
- `diagnosis/after/quarantine-exposure-*d.csv`: direct affected-window candidates, not an applied deletion list.
- `diagnosis/after/changed-rows-*d.csv`: repaired labels and removed invalid feature rows.

Verification: 92 JavaScript tests, 91 Python tests, syntax/compile checks and dashboard contracts pass. The matrix audit checks unique identities, historical membership, finite features, label maturity and current source hashes. Fixtures cover halt boundaries/reopening, no forward fill, preserved distress losses, impossible entries versus rights, feature/label exposure, spin-off formulas and dividend correction/share units. Production model hashes remain unchanged. Tests do not turn unresolved source evidence into ground truth.
