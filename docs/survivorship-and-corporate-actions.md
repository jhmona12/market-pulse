# Historical Universe And Corporate-Action Policy

## Decision

Keep removed securities in the research record. Do not rebuild history from today's S&P 500 list. However, distinguish **eligibility for a new selection** from **tracking an existing holding and its outcome**:

- On signal date t, the primary S&P-only model should rank securities that were eligible members on t, using the sector classification known on t.
- After removal, retain all prior features, predictions, membership intervals and price/action evidence. Continue any already-open 14D or 252D outcome through its original horizon.
- Do not automatically open new S&P-only positions after removal. An extended former-member watchlist is a separate universe/strategy, evaluated separately.
- A list of every stock that ever joined the index is not sufficient: including future entrants years before admission also introduces hindsight into the universe.

This follows the distinction between index maintenance and security ownership. S&P's methodology describes deletions for eligibility changes and corporate events; removal itself does not imply a terminal shareholder loss. [S&P methodology overview](https://www.spglobal.com/spdji/en/research-insights/index-literacy/methodology-matters/).

## Identity And Membership

Use a persistent security-level ID, dated ticker aliases, exchange, share class, dated membership intervals and source evidence. CIK identifies an issuer, not necessarily one share class; a ticker alone is not a permanent identity. CRSP's permanent identifiers illustrate why ticker/name changes and reused tickers must not define continuity. [CRSP data guide](https://www.crsp.org/crsp_pdf/crsp-us-stock-indexes-databases-data-descriptions-guide-crspaccess/).

Intervals use `[effective_start, effective_end)`. Index announcements and effective dates are distinct. Store when the announcement was public, its effective session and the evidence URL. Unverified intervals, historical sectors or aliases remain review items; do not silently infer them from a present-day company profile.

For new daily tracking, freeze membership snapshots and retain bars for removed names independently of the current-universe download list. A fully verified 15-year reconstruction is a separate coverage task; retaining snapshots now does not repair missing old bankruptcies.

## Outcomes

| Event | Primary research treatment |
| --- | --- |
| Removed but still trades | Keep pre-removal signal observations and finish their original holding horizons using actual prices. No forced sale just because index membership ends. |
| Cash acquisition / going private | Use verified cash consideration per old share at the effective event. Hold proceeds as zero-interest cash until the original label exit; keep the same benchmark horizon. |
| Stock or mixed acquisition | Value contractual successor shares plus cash in consistent old-share units at a verified realizable event price. Primary policy liquidates the consideration then holds cash to the original exit. Following successor shares to exit is a separately labeled sensitivity test, not an automatic price-history splice. |
| Bankruptcy filing | A filing alone is not a zero price. Continue observable trading/OTC prices if reliable, preserving identity and source. |
| Confirmed old-equity cancellation with no recovery | Record total loss of the remaining old-share claim. Do not splice newly issued post-bankruptcy equity onto the old shares. |
| Trading halt, exchange transfer, no quote, unknown consideration | Mark the outcome unresolved/censored and retain the observation in coverage reports. Do not invent a last-price exit, a zero return or a total loss. |
| Spin-off / split / rights / distributions | Reconcile share units and all consideration. Never add a distribution a second time if the adjusted series already includes it. Multi-leg claims need a reviewed event record. |

The SEC warns that old common stock is often cancelled in bankruptcy while trading may continue before reorganization, and new stock can be distinct from the old claim. That is why a filing date is not itself a verified -100% outcome. [SEC bankruptcy guidance](https://www.sec.gov/files/investor/pubs/bankrupt.htm).

The terminal-return calculation, when the pre-event total-return path excludes the terminal event, is:

```text
full return = (1 + total return through last observed trade)
              * verified consideration per old share / last raw close
              - 1
```

The denominator is a **raw price in the same old-share units**, not a dividend-adjusted close. For a synthetic example, +20% through the last trade, last raw close $100 and verified recovery $110 implies `1.20 * 110/100 - 1 = +32%`. Verified zero recovery implies -100%. The research contract rejects double-counted consideration and identity mismatches. It also waits until both the original horizon and the event-value evidence are available before labeling a row mature.

Known terminal events belong in **labels and accounting**, not as future-known predictor flags. A later bankruptcy must never become an input to a prediction made before it was publicly known.

## Missing Outcomes And Performance

Rank all feature-eligible securities before checking whether their future returns are available. If a selected top-decile member has an unresolved outcome, mark that basket incomplete. Report its missing count and do not substitute the return of the surviving members. Paired model comparisons use only common, complete basket dates and disclose excluded dates. This still leaves missing-outcome bias; a complete-case comparison is not proof of unbiased performance.

Train only on known labels, with a dated exclusion ledger and a coverage gate. Missing labels cannot be interpreted as flat returns or negative examples. Scenario analysis on unresolved claims may be useful, but assumptions such as -50% or -100% are sensitivity cases, not recovered historical facts.

## Original Audit: November 18, 2026

This section describes the earlier snapshot-union experiment, not the subsequent historical rebuild below.

- The Wikipedia changes table moved to a separate historical-components page. The scraper now checks both pages and fails if neither returns changes. It also preserves blank added/removed cells so one-sided spin-offs/removals do not shift columns.
- Discovery found 314 removal events and 311 unique removed tickers in the approximately 15-year study window. This table is a discovery source, not verified security-level membership.
- The controlled experiment uses 518 original/current-snapshot names plus 12 benchmarks. It is **not a point-in-time universe**. 286 historical removed tickers have no price history in this experiment; this is a coverage gap, not a finding that all 286 are unavailable from every free source.
- Six former-name Yahoo responses contain only a final price row. The earlier cached segments are preserved separately for the original security; they are not stitched to that lone quote. Their incomplete outcomes remain visible.
- EQR, PSKY and SATS differ between the two current-member snapshots without matching removal entries in the discovery table. These require alias/action/reference reconciliation, not guessed membership dates.

The review queue is generated at `data/modeling/experiments/2026-11-18/former_member_review_queue.csv`. The summary explicitly blocks a survivorship-free/promotion claim until stable identities, intervals, historical sectors, prices and terminal outcomes are verified. **No historical corporate-action returns have been invented or automatically applied to these datasets.**

## Historical Rebuild: Implemented

The follow-up now actually loads historical membership, former-member prices, dated sector observations and reviewed terminal events into separate training matrices. The earlier work only implemented contracts and discovery. It did **not** complete this integration; describing it as a finished survivorship correction would have been wrong.

- Frozen [public historical archive](https://github.com/Johnbrick123/sp500-data): price histories exist for all 700 securities intersecting the ten-year raw window and all 804 intersecting the fifteen-year window. This is community reconstruction, not licensed institutional data. Cache files and upstream hashes remain private/ignored; no redistribution rights are assumed from public availability.
- Frozen [IVV holdings archive](https://github.com/riazarbi/sp500-scraper): 187 monthly observations provide dated sectors. Apply observations one session later; never backfill current sectors. A January-June 2017 archive gap uses the last observed classification, with a 366-day expiry and explicit age counts. This is a documented classification approximation, not permission to carry stale prices.
- ISIN links and reviewed old/new security identities disambiguate aliases. Dated eligibility precedes universe breadth, percentiles and sector medians. Unknown/conflicting identities are not silently joined.
- `config/research-terminal-events.json` supplies reviewed cash/share consideration with primary-source URLs and evidence dates. `historical_universe.py` applies the accounting policy to labels. Stock consideration uses the first available successor session on/after the event (maximum four calendar days), with split units reconciled; then proceeds remain cash. Unknown nontradeable rights are bounds only, never invented point labels.
- Halt/carry rows are not executable prices. Actual post-removal trading remains available for original holding outcomes. The new datasets retain failed-bank losses rather than removing failed issuers from history.
- Six fixed recipes per horizon are tuned on earlier purged validation windows, then evaluated on later development dates and refitted on all known mature labels. Labels becoming known late cannot affect earlier training grades. These are separate research candidates, not automatic production replacements.

See [historical rebuild results](historical-model-rebuild-2026-11-18.md), [reproduction commands](../analysis/model-audit/README.md) and the generated `historical_rebuild_audit.json`. The remaining review queue is explicit: missing/uncertain terminal claims, sparse classifications, upstream price discrepancies and corporate-identity verification. Acquisition terms are manually reviewed, not automatically inferred by an LLM. Continuous former-holding archival in daily refresh remains separate work. Neither the old nor new models should be described as demonstrably survivorship-free.

The [follow-up reconciliation](historical-data-reconciliation-2026-11-18.md) corrects omissions in the publisher's no-trade list by combining it with exit-gap intervals and `config/research-no-trade-periods.json`. `config/research-price-reviews.json` records primary-sourced decisions and a guarded Cintas dividend correction; frozen downloads remain immutable. Corrected `reconciled/` matrices are separate from initial fitted candidates. Missing outcomes are not random: do not delete them before ranking, renormalize baskets onto known survivors, or remove valid history because an issuer later fails. Price quarantines must rebuild affected features and shared context, not only remove a flagged date.
