# October Refresh Reliability Audit

## Scope And Current State

Audited the most recent 100 Actions runs available on October 18, 2026: 66 refresh attempts and 34 health-monitor runs, covering October 2 morning through October 18 morning. Cross-checked run conclusions against job steps, raw failure logs, check annotations, and the committed publication ledger. A green backup that skips work is not counted as a new publication.

| Refresh outcome | Attempts |
| --- | ---: |
| Confirmed publication, matched to the ledger | 32 |
| Backup correctly skipped an already-published target | 28 |
| Failed application/data attempt | 4 |
| Failed to acquire a GitHub runner; no application steps ran | 2 |

Of 33 due morning/evening targets in this interval, 32 eventually published. The missing target was October 5 morning. This is eventual completion, **not** an on-time success rate. Two monitor failures correctly detected that one missed target; they were not two additional failed refreshes.

Among the 32 confirmed publications, recorded start delays ranged from 107 to 539 minutes, with a median of 222.5 minutes (about 3 hours 43 minutes). These delays are measured from each successful attempt's own scheduled slot, including backup slots, and include dispatch/queue/start latency. They are not application execution durations.

At the time of the audit, the latest confirmed run was [39142262920](https://github.com/jhmona12/market-pulse/actions/runs/39142262920), generated October 18 at **8:58 AM Pacific**, with 499 scored names. Its expected/model price session is Friday, October 16, correct for a Sunday refresh. An independent strict live probe matched the successful run ID and SHA-256 hashes of all four dashboard JSON files to local HEAD `d65fcbce695a734b3a0ea21de362ccd918a0a3c9`. This demonstrates publication at the audit boundary, not that the new patch had run in production.

## Cause 1: Broken Optional-Narrative Fallback

Affected primary runs:

- [October 2 evening, 37088282122](https://github.com/jhmona12/market-pulse/actions/runs/37088282122), job `111102954265`.
- [October 6 morning, 37510483383](https://github.com/jhmona12/market-pulse/actions/runs/37510483383), job `112430031983`.

Both had successful scoring, then failed snapshot assembly twice with `ReferenceError: articleBrief is not defined`. AI Deeper Read output failed evidence checks, invoking `deterministicDeeperRead()` with actual article candidates. That fallback referenced a helper which no longer existed. The outer recovery path invoked the same broken function, so an optional narrative failure became a full-refresh failure. Syntax checks and empty-candidate tests cannot catch this branch.

Later backups [37089428806](https://github.com/jhmona12/market-pulse/actions/runs/37089428806) and [37512811699](https://github.com/jhmona12/market-pulse/actions/runs/37512811699) succeeded. That does not validate the fallback; it only shows that subsequent inputs/AI output did not trigger the same fatal path.

**Repair:** reuse the existing `articleConclusion()` helper, test the actual nonempty fallback through the AI wrapper, and clear its abort timer even when the fetch itself rejects. The fallback remains labeled source-derived output. No scores, prices, or market claims are manufactured to fill an optional section.

## Cause 2: Provider Returned Null Closing Prices

| Pacific target | Failed primary | Current stock histories | Required context | Successful backup |
| --- | --- | ---: | --- | --- |
| October 15 evening | [38834915819](https://github.com/jhmona12/market-pulse/actions/runs/38834915819) | 434/503 (86.3%) | XLC and XLRE stale | [38836361649](https://github.com/jhmona12/market-pulse/actions/runs/38836361649) |
| October 16 evening | [38990384723](https://github.com/jhmona12/market-pulse/actions/runs/38990384723) | 0/503 (0%) | All 17 context histories stale | [38991391220](https://github.com/jhmona12/market-pulse/actions/runs/38991391220) |

The saved raw-tail diagnostics resolve the earlier uncertainty: Yahoo returned correctly dated session timestamps with **both raw and adjusted closing prices null**. These were not valid prices shifted onto the wrong day. On October 16, all 520 stock/context histories stopped at the previous accepted session; on October 15, 71 of 520 did. Both Yahoo chart hosts behaved the same across the failed readiness checks. A second hostname is not an independent data provider.

Failing closed was correct. The weakness was spending both model attempts within roughly five to six minutes, while the backups first observed sufficient data later:

| Session | First model attempt failed (UTC) | Last failed check (UTC) | First backup readiness success (UTC) | Recovery observed after first failure |
| --- | --- | --- | --- | --- |
| October 15 | Oct 16 01:26:49 | Oct 16 01:29:52 | Oct 16 01:41:15 | 14m 26s |
| October 16 | Oct 17 01:22:47 | Oct 17 01:26:33 | Oct 17 01:34:48 | 12m 01s |

The first backup reached 503/503 current histories; the second reached 502/503 and excluded the stale remaining stock. Final feature-complete score counts were 499 and 498 respectively. Price coverage and model feature coverage are different checks.

**Mitigation:** incomplete tactical prices/features now exit with code `75`. Only that condition gets a 15-minute outer wait before the second attempt. Generic failures retain 90 seconds. The number of attempts, inner retries, coverage/feature gates, and 35-minute step timeout remain bounded. The strategic phase reuses a successful tactical reference cache.

Fifteen minutes spans these two observed recovery intervals; it is not a statistical guarantee about future Yahoo delays. Tests replay the observed availability times with a virtual clock and prove that persistently missing prices still fail. They cannot establish continuous provider availability between observations. If missing data persists, publish failure status with the prior snapshot visibly marked, not a falsely fresh or partially assembled dashboard.

## Cause 3: No GitHub Runner Was Acquired

October 5 morning attempts [37370409197](https://github.com/jhmona12/market-pulse/actions/runs/37370409197) and [37371974218](https://github.com/jhmona12/market-pulse/actions/runs/37371974218) had empty runner names, no executed steps, and cancelled jobs underneath failed workflow conclusions. Check annotations for jobs `111965850068` and `111971108325` state:

> The job was not acquired by Runner of type hosted even after multiple attempts

Neither the model nor ingestion ran. The monitor failures [37385057495](https://github.com/jhmona12/market-pulse/actions/runs/37385057495) and [37451204442](https://github.com/jhmona12/market-pulse/actions/runs/37451204442) correctly reported the missing `2026-10-05-morning` target.

This is separate from the pervasive scheduled-start delays. GitHub explicitly says [scheduled workflows can be delayed or dropped](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule). The existing monitor shares the GitHub scheduler and runner dependency; it cannot independently guarantee timely detection. No repository unit test can provision an unavailable hosted runner.

## Solution Options

1. **Apply the scoped code patch and preflight tests first.** It fixes the reproduced undefined-helper crash and gives delayed EOD data a meaningful, bounded recovery interval without weakening correctness. Both unit suites run before calendar fetching/scoring/AI calls; full generated-output verification remains mandatory before publication. This does not solve GitHub infrastructure availability.
2. **Free-first interim: independent dispatch and monitoring.** An external timer can call GitHub's dispatch endpoint, and a separate health check can compare the expected Pacific target with live successful publication. For example, [Cloudflare Cron Triggers](https://developers.cloudflare.com/workers/configuration/cron-triggers/) can run a dispatch/check function, not the Python/XGBoost workload itself. This addresses reliance on GitHub cron but still depends on GitHub hosted runners and Pages. Before enabling it, add a validated target date/window input: today's `workflow_dispatch` deliberately uses `manual` and bypasses target idempotency. Automated dispatch must not create duplicate paid runs or make the monitor miss successful windows. Tokens must remain server-side and narrowly scoped. No service or credentials were provisioned in this patch.
3. **Recommended for dependable daily operation: separately scheduled execution on an always-available Python-capable host, plus an independent publication monitor.** Keep the deterministic pipeline, tests, target ledger, and atomic output contract. Use a process lock, bounded job retries, and explicit target IDs. A local Mac is a no-new-hosting-cost option only if awake and connected; an always-on managed host is more operationally suitable but needs a hosting/budget decision. Merely putting a self-hosted runner behind the same GitHub cron leaves the dispatch weakness intact. Keeping GitHub Pages still retains a separate deployment dependency, which must be monitored and retried independently of scoring.

Do not respond by disabling freshness checks, treating a skipped backup as new data, removing the failing monitor, increasing retries without bounds, or introducing an unvalidated price feed. An independent licensed EOD source is a separate future option if Yahoo null sessions remain frequent; adjusted-history consistency and corporate-action handling must be validated before using it as a substitute.

## Acceptance And Verification

| Contract | Regression coverage |
| --- | --- |
| Nonempty source fallback cannot call a nonexistent helper | `deeper-read-fallback.test.mjs`: failed against original code, passes after repair |
| Missing key, rejected evidence, malformed output, HTTP 429, network error retain cited fallback | AI wrapper tests with injected HTTP/usage dependencies; no paid requests |
| Network rejection clears the Deeper Read timeout | Timer-cleanup assertions on each mocked response/error path |
| Correctly dated null closes are not accepted, forward-filled, or attributed to timezone conversion | `test_price_ingestion.py` with a minimal fixture reconstructed from the failed AAPL diagnostic |
| A readiness failure reaches the orchestrator as 75, ordinary bugs remain errors | `test_model_scoring_readiness.py` CLI tests |
| Delayed feed can recover, persistent failure blocks the dependent scorer, generic errors keep short retries | `refresh-health.test.mjs`, virtual-clock October timeline replay and failure cases |
| A configuration override cannot grow the settlement sleep beyond 15 minutes | Retry-bound unit test |
| Tests run before expensive refresh work; dependency setup and timeout remain intact | `workflow-environment.test.mjs` |
| Invalid or stale output cannot be marked successfully published | Existing schema, freshness, recovery, live-hash, and ledger contract tests |

Local verification on October 18:

- Full `scripts/verify.mjs`: **66 JavaScript tests and 28 Python tests passed**, plus JavaScript syntax, Python compile, dashboard schema/data contracts, and workflow checks.
- Workflow YAML parsing passed.
- Independent strict live status/four-artifact hash check passed for existing production run `39142262920`.
- New tests are isolated from external services; mocked availability proves retry policy, not future service uptime. Local Python is 3.12; hosted Python remains 3.11, so the next hosted run is still an environment verification boundary.

No retraining, model/label changes, schedule changes, paid service setup, fresh AI calls, or generated dashboard writes were performed for this investigation. The verification results above were recorded before rollout; the user subsequently approved deploying this patch. Deployment status belongs to the [Pages workflow history](https://github.com/jhmona12/market-pulse/actions/workflows/pages.yml), while new scoring/publication belongs to the [refresh workflow history](https://github.com/jhmona12/market-pulse/actions/workflows/refresh-data.yml). Match the run's commit to the patch: the October 18 morning success predates it, and a Pages-only deployment does not recalculate model scores.
