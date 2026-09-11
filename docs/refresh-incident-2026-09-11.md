# September 10 Evening Refresh Recurrence

## Confirmed Evidence

- Morning refresh [34501684788](https://github.com/jhmona12/market-pulse/actions/runs/34501684788) succeeded on September 10. The backup [34503630083](https://github.com/jhmona12/market-pulse/actions/runs/34503630083) was green after that target was complete.
- Evening primary [34549425084](https://github.com/jhmona12/market-pulse/actions/runs/34549425084) started September 10 at 6:08 PM Pacific, about 111 minutes after its 4:17 PM slot. Backup [34550834964](https://github.com/jhmona12/market-pulse/actions/runs/34550834964) started at 6:28 PM, about 102 minutes after its 4:47 PM slot.
- Both failed tactical price readiness for **2026-09-10**: only **1/503** current stock histories, plus missing/stale SPY and sector/market ETFs. Each exhausted two model attempts, with three readiness checks per attempt. Long-horizon scoring, news/Reddit ingestion, and normal publication never ran.
- The recovery path published the last verified snapshot with an explicit failure status. The monitor [34578195514](https://github.com/jhmona12/market-pulse/actions/runs/34578195514) correctly failed for the missing September 10 evening target, even though it ran after Pacific midnight.
- The schedule target and expected trading session were correct. Scheduling delay and price readiness were separate problems. These failures were not caused by Reddit, Pages deployment, or missing Python dependencies.

## What The Old Logs Cannot Prove

The loader discarded missing adjusted closes and interpreted daily timestamps in UTC without retaining raw-response evidence. The readiness log only saw the resulting feature frames. Thus it cannot distinguish absent provider data, incomplete adjusted fields, or locally discarded/misdated bars.

Current endpoint responses contain September 10 bars, but requesting an old end timestamp today does **not** reproduce the response Yahoo served last night. A successful later rerun likewise does not prove an evening fix. The previously added retries and coverage guards improved containment; they did not resolve or identify this source-level recurrence.

## Targeted Changes

- Parse daily dates in the exchange's IANA timezone. Tests reproduce a U.S. evening timestamp incorrectly assigned to the following UTC day and removed by the EOD filter, as well as Sydney and winter-offset cases. This is a verified code defect, not a proven historical incident cause.
- Reject missing/nonfinite adjusted prices, including absent or short adjustment arrays. Never replace them with raw quotes or yesterday's data.
- Try the alternate Yahoo chart endpoint on the existing stale-history retry, replacing the full adjusted series. No additional retry rounds, model changes, or freshness exceptions.
- Capture per-attempt raw/parsed evidence separately from reusable caches and public snapshots. Retain it in optional Actions artifacts, including on failed jobs. The next recurrence can be investigated from actual responses instead of inferred from output dates.

## Verification

- Before implementation, the new parser suite had one passing test and seven failures/errors. It reproduced the exchange-date defect, absent diagnostics, nonfinite acceptance, and opaque provider errors.
- The expanded suite and whole-project verification cover parser boundaries, complete-history alternate-endpoint recovery, persistent stale failure, diagnostic retention, duplicate daily updates, and the existing publication contracts. Final command results are recorded below.
- On identical live raw responses, all 752 bars for each of SPY, AAPL, BRK.B, XLK, and GLD produced unchanged OHLC/close/volume/date values compared with the previous parser (3,760 bars total). A separate SATS probe returned HTTP 404 and was not counted as a successful comparison.
- Final local whole-project verification passed against the newly recovered published dataset: **53 JavaScript tests and 24 Python tests**, plus syntax/schema/data-contract checks. `git diff --check` and workflow YAML parsing also passed.
- A full price/model pass with the updated parser fetched **503/503** current stock histories and all required context (520 histories in total), then scored **500** complete tactical rows. The strategic scorer produced **500** rows from that new reference cache. Outputs stayed in ignored `data/cache/incident-20260911/`; no model artifacts or training labels were changed.
- The 500 tactical published scores, beta/return fields, stops, and activation values matched the newly fetched local results by symbol. The 500 strategic published scores also matched. Ordinal ranks differ within equal-score groups across environments: the existing single-column, unstable score sort has no explicit tie-breaker. That is a separate monitoring-quality follow-up, not evidence of stale data or a change in model predictions; this incident patch does not change ranking methodology. Strategic internal `return60` is a trading-day model field, while the UI deliberately reuses the tactical calendar-lookback return.

## Recovery Result

Manually reran [34550834964, attempt 2](https://github.com/jhmona12/market-pulse/actions/runs/34550834964/attempts/2). It used the previously deployed code, not this uncommitted patch. Both scorers succeeded on their first attempt at 10:02 UTC September 11, followed by briefing generation and verification. Live publication was confirmed at **10:05:34 UTC (3:05 AM Pacific)**. Generated data was committed as `2612f2f`.

An independent check confirmed the live status reports success/published, run `34550834964`, attempt `2`, and model/expected date `2026-09-10`. SHA-256 hashes of snapshot, tactical scorebook, strategic research, and model monitoring all matched commit `2612f2f`. The live-enabled monitor then reported no missing morning/evening targets and no live-check errors. Reddit status was `ready`.

This proves recovery, not permanent evening reliability. The new preventive patch is supported by regression tests and isolated live scoring; the next naturally occurring evening response is still needed to establish whether the original provider/parse failure recurs.

## Remaining Operational Risk

The alternate chart host is not an independent supplier. A provider-wide missing session must still block scoring. An independently licensed EOD feed would be the next reliability option if captured responses show both endpoints repeatedly lack a completed session. Changing the expected date, forwarding old prices, or suppressing the failure would conceal the problem rather than solve it.
