# Refresh Operations

This document describes the scheduled refresh path, its failure boundaries, and the first checks to make when a run fails.

## Refresh Stages

1. `scripts/check-refresh-window.mjs` maps the scheduled slot to a Pacific morning/evening target and skips a target already recorded as published.
2. `scripts/update-macro-calendar.mjs` refreshes the next six months of official macro dates.
3. `scripts/refresh/run-model-scoring.mjs` runs the tactical scorer, then the long-horizon scorer against the new tactical reference cache. It retries only the failed phase.
4. `scripts/update-data.mjs` ingests sources, official macro releases, earnings, market movers, Reddit sentiment, and AI synthesis, then assembles the four dashboard JSON artifacts.
5. `scripts/verify.mjs` checks syntax, fixtures, schemas, freshness, ranks, stop/activation contracts, and deployment contracts.
6. GitHub Pages is packaged, deployed, and probed. A scheduled target is written to `data/refresh-ledger.json` only after live status identifies the successful run and SHA-256 hashes of all four dashboard JSON artifacts match the verified local outputs.

The live probe uses bounded requests and a different query parameter on each attempt to avoid repeatedly reading the same cached response. A matching status file alone is not sufficient: missing or older Today, Tactical, Strategic, or Lab data keeps the target retryable.

Runtime cache saving is optional and only runs after successful model scoring. A cache-service outage must not prevent otherwise verified data from publishing.

## Failure Recovery

`scripts/refresh/recover-dashboard.mjs` checks the remote branch before restoring the seven tracked data/status/ledger artifacts from `HEAD`. Recovery is skipped if the remote advanced or the new dashboard was already confirmed live. In particular, a later Git commit failure must not roll back a successfully published dashboard.

When recovery is allowed, the workflow writes and attempts to commit a failure status without marking the target complete. If neither normal deployment succeeded, it also attempts to publish the restored data with that status through a distinct Pages artifact. This preserves the prior dataset rather than publishing partial output; retained data is not a successful fresh run.

If deployment succeeded but the live probe failed, the workflow records the unconfirmed publication and does not redeploy older data over the possibly successful deployment. Inspect both Actions and the live site in that case. Failure-status publication is itself best-effort when GitHub Pages or network access is unavailable.

## Missed-Refresh Monitor

The monitor checks the latest due morning target and the latest due evening target independently. Before a window's cutoff it checks the previous Pacific calendar day; after that cutoff it checks today. Thus a monitor delayed until after midnight still detects the preceding evening's missing refresh. Date calculation uses Pacific calendar dates, including DST and year boundaries.

Default cutoffs are 7:30 AM and 6:30 PM Pacific. Scheduled monitor slots are later, at 9:30 AM and 8:30 PM, to allow for runner delays. Missing targets produce a failing Actions run. This is not an independent uptime guarantee: the monitor uses the same best-effort GitHub scheduler as the refresh.

## Partial Yahoo EOD Sessions

Yahoo can expose the newest trading date for some symbols before SPY, sector ETFs, and the full stock cross-section are all ready. Selecting the maximum date in that state can create a date with zero complete model rows.

The scorer now requires:

- SPY, every sector context ETF, and every market-strip ETF on the expected session.
- At least 90% of current S&P 500 histories on the expected session.
- Enough complete feature rows to clear the same 90% threshold before XGBoost is called.

It retries the union of missing/stale context and stock histories, including remaining stale stocks when the 90% threshold has already been met. The aim is full coverage; the threshold is an operational failure boundary, not evidence that a reduced universe is statistically unbiased. Once retries are exhausted, remaining stale stocks are excluded, never scored using yesterday's values. Missing required context or coverage below the threshold fails the phase.

Feature validation selects the expected session explicitly, rejects duplicate symbol rows, and excludes null or non-finite model features. It requires at least 90% of the reference universe to remain scorable before calling XGBoost. Recent listings can have current prices but insufficient feature history; price coverage and scored coverage are separate diagnostics.

If the feed or feature matrix is incomplete, errors name the expected date, coverage, missing context, or latest complete feature date. There is no silent fallback to an older model date. These controls improve retry behavior and diagnosis; they cannot force Yahoo to supply missing data.

Operational controls:

```text
MODEL_DATA_READINESS_ATTEMPTS=3
MODEL_DATA_READINESS_DELAY_SECONDS=30
MODEL_MIN_SESSION_COVERAGE=0.90
MODEL_SCORING_ATTEMPTS=2
MODEL_SCORING_RETRY_DELAY_MS=90000
```

`EXPECTED_MARKET_DATA_DATE=YYYY-MM-DD` is an explicit diagnostic override, not a routine way to bypass freshness checks.

## Reddit Reliability

Reddit OAuth is the supported hosted-runner path. Configure `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, and `REDDIT_USER_AGENT` as GitHub Actions secrets.

Without OAuth, the refresh uses the WallStreetBets `hot` RSS feed, with an alternate Reddit host available on failure. Public JSON and other subreddit feeds are skipped in this mode. This reduces repeated unauthenticated requests but does not bypass Reddit access controls.

Metric labels follow the actual returned posts, not the presence of credentials. Only an entirely OAuth-JSON sample includes vote/comment metrics; RSS or mixed samples are labeled `unranked_recent_mentions`, not a popularity or concentration ranking. Live posts must have a valid timestamp within the trailing 24 hours; future, undated, and older posts are excluded. A clean last-good sample may be used for up to 24 hours and is labeled cached; otherwise the dashboard shows Reddit as unavailable and excludes it from the investment conclusion.

Detailed transport errors remain in `marketIntelligence.reddit.currentFetchErrors` and per-subreddit `sortStatuses`. The UI displays only the concise `statusReason`.

## HTTP Ingestion Policy

`scripts/ingest/http.mjs` owns source timeouts, retries, failed-response cleanup, and redirects. Permanent `4xx` responses are not retried. Only `408`, `429`, selected `5xx` responses, and transient network errors are retried. Failed response bodies are cancelled before another attempt, and redirects have a six-hop ceiling. Authorization, cookie, and proxy-authorization headers are removed when a redirect changes origin.

## First Checks After A Failure

1. Open the failed Actions job and identify the last failed phase reported by `run-model-scoring.mjs` or `run-snapshot-refresh.mjs`.
2. For model failures, look for `Yahoo EOD readiness` and `Feature cross-section` diagnostics. Do not treat `Empty dataset at worker` as a sufficient root cause.
3. For Reddit, check whether OAuth secrets are present and then inspect `statusReason`, `currentFetchErrors`, and `sortStatuses` in the generated snapshot.
4. Run `npm run verify` before publishing any fix. This includes the model-readiness and Reddit parser fixtures.
5. Check the live `data/refresh-status.json` and `data/refresh-ledger.json`; a generated local snapshot does not count as a published refresh.

See [the September 10 pre-commit audit](refresh-precommit-audit-2026-09-10.md) for reproduced failures, regression coverage, and isolated live checks.
