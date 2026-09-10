# Refresh Pre-Commit Audit: September 10, 2026

## Scope And Decision

Review the uncommitted September 9 changes before committing or publishing them. The original patch needed corrections: passing the initial tests did not cover several important failure paths. This audit adds regression coverage, exercises both real models using isolated fresh data, and checks the Reddit fallback without changing the published dashboard.

The corrected changes are supported by the local checks below. They are not yet proven on a GitHub-hosted runner. No model weights, training labels, or feature definitions were changed by this audit.

## Evidence From Failed Runs

- [Refresh #745](https://github.com/jhmona12/market-pulse/actions/runs/34424105647) and [refresh #746](https://github.com/jhmona12/market-pulse/actions/runs/34425639778) ran the older remote code, not the local fixes. The evening jobs started roughly 109 and 102 minutes after their scheduled slots, respectively.
- Their logs rejected September 8 model rankings when September 9 was required. The long-horizon step then tried a stale reference cache. Snapshot assembly, Reddit ingestion, and publication did not run. Missing Reddit output in these runs was therefore not proof that Reddit itself failed.
- The logs establish a stale final feature/model date, but do not preserve enough raw per-symbol history to prove exactly which upstream data caused it. Partial EOD delivery is a tested failure scenario, not a conclusively identified historical provider incident.
- [Monitor #144](https://github.com/jhmona12/market-pulse/actions/runs/34454615606) started at 1:20 AM Pacific on September 10 and reported no expected targets. The same-day-only monitor forgot the missed prior evening after midnight. That is a directly reproduced code bug.

## Acceptance Contracts And Corrections

| Failure boundary | Required behavior | Verification |
| --- | --- | --- |
| Delayed monitor | Check the latest due morning and evening even after midnight; retain correct Pacific dates across DST/year boundaries. | Date fixtures and a CLI test reproducing the missed evening with exit code 1. |
| Partial market data | Retry stale context and stocks together; attempt all remaining stale stocks even after 90% coverage. Never score yesterday as today. | Python readiness fixtures, exhausted-retry rejection, and a real full-universe fetch. |
| Invalid feature cross-section | Require the expected session, unique symbols, finite complete features, and sufficient reference coverage before XGBoost. | Empty/incomplete date, duplicate symbol, and infinite feature tests. |
| Model phase dependencies | Never run the long model after a failed tactical phase; retry only the failed phase when its dependency is ready. | Injected command failures and cache-only long-model execution. |
| Publication | A successful run id alone is insufficient; all four served dashboard artifacts must match the verified bundle. | Stale/missing artifact fixtures, eventual recovery, and strict CLI failure. |
| Recovery | Restore the whole checked-in data bundle only when the remote has not advanced and the new site is not already confirmed live. | Three tests with temporary local Git repositories, including a competing push and post-publication failure. |
| Optional cache service | A cache-save failure must not block otherwise verified publication. | Workflow configuration and contract review. |
| Reddit fallback | Derive metrics from actual returned transport; RSS/mixed samples cannot claim vote/comment rankings. Exclude old, future, or undated live posts. | OAuth/RSS/mixed fixtures, 24-hour boundary tests, and a real RSS sample. |
| HTTP redirects | Bound redirect chains and never forward credentials/cookies across origins. | Redirect, retry, cancellation, and cross-origin header tests. |
| Source interpretation | Do not forbid legitimate uncertainty in a sourced Deeper Read thesis. | Qualified-thesis fixture; prompt and guardrail alignment. |

New regression tests initially exposed four JavaScript and four Python failures in the proposed implementation. The fixes were made after reproducing those cases, not by weakening their assertions.

## Local Verification Results

Before integrating newer remote artifacts, this verification command passed under Node 24.19.0 and local Python 3.12:

```sh
PYTHON=.venv-model/bin/python node scripts/verify.mjs
```

- 50 JavaScript tests passed.
- 9 Python model-readiness tests passed.
- JavaScript syntax, Python compilation, dashboard JSON contracts, rank/freshness/stop/activation assertions, and workflow publication contracts passed.
- Workflow YAML parsed successfully and `git diff --check` passed.

These contract checks validate the stored artifacts against their declared dates. They do not by themselves prove that an older local snapshot is current today. The separate live checks below establish the newly fetched market session.

## Isolated Live Checks

Outputs are under ignored `data/cache/precommit-audit/`, not the tracked dashboard files.

| Check | Observed result |
| --- | --- |
| Tactical fetch and scoring | 503/503 reference histories current through September 9; required ETFs ready; 500 stocks scored; zero fetch failures. |
| Feature exclusions | FDXF, HONA, and Q had insufficient complete features and were excluded, not filled from stale model rows. |
| Long-horizon scoring | 500 stocks scored for September 9 from the newly generated tactical reference cache, without a second history fetch. |
| Change versus September 8 | All 500 symbols matched; 79 model scores and 454 ranks changed. These establish changed output, not evidence of improved investment performance. |
| Reddit fallback | 22 recent WallStreetBets RSS posts yielded nine ticker mentions. Output correctly reported `unranked_recent_mentions`, zero OAuth posts, and no vote/comment metrics. |

Tickers in that Reddit sample were META, AAPL, UBER, CHTR, EXEL, SNDK, UUUU, NKE, and VRT. These are observed mentions, not recommended trades or a validated popularity ranking.

Reproduce the model checks from the repository root:

```sh
.venv-model/bin/python -B -u scripts/modeling/score_live_rank_model.py \
  --output data/cache/precommit-audit/tactical.json \
  --reference-cache data/cache/precommit-audit/reference.json --max-workers 4

.venv-model/bin/python -B scripts/modeling/score_live_rank_model.py \
  --model-dir models/long-horizon \
  --model-name xgboost_rank_sector252_15y_monthly_tuned_research \
  --output data/cache/precommit-audit/strategic.json \
  --reference-cache data/cache/precommit-audit/reference.json --score-reference-cache
```

## Remaining Limits And Publication Plan

1. GitHub-hosted Python is 3.11; the full local scoring check used 3.12. Hosted networking, OAuth secrets, AI API calls, and Pages deployment still require a real workflow run. No new paid AI synthesis or hosted deployment was run during this audit.
2. The earlier generated dashboard files remain pending locally and use September 8 prices. Do not blindly commit those as the latest dashboard or overwrite a newer remote publication. Commit the reviewed code, tests, and documentation separately, retaining the latest remote published data.
3. After that commit is pushed, manually dispatch the full refresh. Require both models on the expected session, successful snapshot verification, matching live hashes for all four dashboard artifacts, and a committed target ledger. Check the subsequent scheduled run and monitor as well.
4. Free data providers can remain unavailable beyond the retry budget. The correct result then is an explicit failed refresh with the prior dataset clearly identified, not invented data or a silently relaxed date.
5. These fixes cannot guarantee exact execution time. GitHub documents that [scheduled workflows can be delayed or dropped](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule). The monitor shares that scheduler. Dependable external notification or a hard delivery deadline would require an independent monitoring/scheduling service.

Operational details: [Refresh Operations](refresh-operations.md).

## Pre-Push Integration Check

Before committing, the checkout was fast-forwarded to remote commit `3515bc8`, preserving the newer automated data/status commits. The six older local generated files were kept separately, not included in the code commit.

All 50 JavaScript and nine Python tests passed again. Full artifact verification correctly rejected the remote's existing output under the new contracts:

- 499 long-horizon rows had beta/return display fields inconsistent with the tactical scorebook.
- Six AI memo company-news blurbs failed evidence-grounding checks: MRNA, STX, LITE, MU, DELL, and WDC.

These are legacy generated-output incompatibilities. The code fixes for canonical return fields and grounded company news must run through the full refresh before the new Pages deployment can pass. The gates were not weakened and older local snapshots were not substituted to make the code-only push appear deployable. A code push alone is not evidence that the live dashboard has updated.

## Hosted Failure And Environment Correction

[Pages run #85](https://github.com/jhmona12/market-pulse/actions/runs/34457454107), triggered by commit `6163944`, exposed an additional deployment setup omission: all 50 JavaScript tests passed, but Python test collection failed with `ModuleNotFoundError: No module named 'pandas'`. The Pages workflow installed Node but not the dependencies required by the newly added Python tests. The two legacy-output failures above also appeared. No deployment occurred; the Node deprecation messages were warnings, not the cause.

The correction provisions Python 3.11 and installs the existing model requirements in the Pages workflow, matching the refresh workflow. Two new workflow tests enforce setup/install/verification ordering. The verifier now honors an explicit `PYTHON` selection for both compilation and tests, instead of silently switching test execution to the local model environment.

Validation reproduced the import error using a newly created temporary virtual environment. After installing the declared requirements, 52 JavaScript and nine Python tests passed, along with verification of the preserved local artifacts. This macOS test used the existing native OpenMP runtime via `DYLD_LIBRARY_PATH`; it did not reuse the project's Python packages. The hosted Python/Linux environment still requires its own run. Existing remote JSON still needs regeneration under the new contracts; a clean local test is not a successful public deployment.

Recovery requires a new **Refresh Market Data** workflow dispatch on `main` after the correction is pushed, not a rerun of Pages #85 against its original commit. That refresh must regenerate, verify, deploy, and confirm all four dashboard files before this incident can be considered resolved end to end.

Hosted follow-up: correction `a07ad9b` ran in [Pages #86](https://github.com/jhmona12/market-pulse/actions/runs/34459636692). Python setup and dependency installation succeeded, and all 52 JavaScript plus nine Python tests passed on the hosted runner. Verification then rejected only the 499 inconsistent long-horizon rows and six ungrounded legacy company-news blurbs described above. Deployment remained blocked; the full refresh had not yet been dispatched when these results were recorded.

## Successful Full Refresh

[Refresh #746, attempt 2](https://github.com/jhmona12/market-pulse/actions/runs/34425639778/attempts/2) completed successfully on September 10. It retained the original workflow template but its explicit default-branch sync advanced the code from `324a1fb` to `a07ad9b` before execution.

- All 503 reference histories were current through September 9; both models scored 500 names.
- Full report generation, 52 JavaScript tests, nine Python tests, and dashboard verification passed. The prior return-field and company-news failures were absent from the newly generated output.
- Pages deployed and the live probe confirmed the run. Independent checks matched SHA-256 hashes of all four public dashboard JSON files against refresh commit `a11eaec`.
- The published snapshot was generated at `2026-09-10T09:40:28.097Z`, with fresh September 9 price/model data and `staleDataReused: false`.
- Reddit returned 21 recent RSS posts and nine ticker mentions, explicitly unranked with no vote/comment metrics.

The inherited workflow template predates the ledger-recording step. After confirming publication, its missing `2026-09-09-evening` entry was added from the actual published status so subsequent monitors do not misreport it as missed. New scheduled runs use the current workflow's automatic confirmed-publication ledger step.

The reported 589-minute delay belongs to a manually retried prior evening slot; it is not evidence of a new 589-minute scheduler delay. Runtime cache saving produced a nonfatal warning during the rerun; scoring and publication completed. The local project was synchronized to the published data, with earlier local generated snapshots preserved in the named Git stash `Preserve local snapshots before verified refresh sync 2026-09-10`.
