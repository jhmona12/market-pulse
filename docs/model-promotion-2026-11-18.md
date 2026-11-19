# November 18 Model Replacement

The user requested public replacement instead of shadow rollout. Both
targeted-quarantine candidates replace the old artifacts at their existing
paths. Data-quality controls improve, but **better investment performance is
not established**. The earlier economic recommendation to hold remains part of
the research record; this is an explicit user-directed promotion.

| Model | Raw history / sampling | Training signal dates | Rows / securities | Trees |
| --- | --- | --- | --- | --- |
| Tactical | About 10 years / daily | 2017-11-16 to 2026-10-26 | 1,120,350 / 658 | 25 |
| Strategic | About 15 years / monthly | 2012-11-19 to 2025-11-03 | 76,483 / 739 | 120 |

Both have 142 version-2 price/context features, depth-2 XGBoost ranking trees,
dated membership, reviewed corporate actions and price-quality quarantine.
Labels remain 14/252-trading-day sector-neutral returns with next-close entry
and 15 bp estimated cost. Scores are ranks, not calibrated probabilities.
Six recipes were tuned on earlier validation windows; final fits include all
matured labels through the dates above. Research prices end November 16. Daily
serving fetches newer prices but does not retrain weights. Unresolved outcomes,
thin annual cohorts and retrospective price reviews remain material limitations.

## Evidence

The [manifest](../models/releases/2026-11-18-quarantine/manifest.json) records new
and old hashes. The release also stores the tactical and strategic evaluation
JSON, with selection, coverage and development results. See the
[quarantine analysis](targeted-quarantine-experiment-2026-11-18.md) for negative
tactical results and mixed annual mean/median evidence. These are not untouched
tests of the final fitted weights or expected-return estimates.

SHAP matches each replacement. Old-model backtest medians are not displayed
beside new scores. Annual evidence is loaded from the release on every refresh,
not yesterday's public JSON. Monitoring retains old history but compares rank
changes only within the same model hash; replacement starts a new baseline.

## Deployment And Rollback

`scripts/modeling/promote_model_release.py` validates both families before
replacement, backs up six files under ignored
`data/modeling/rollbacks/2026-11-18-quarantine/`, and restores on write failure.
Training timestamps remain intact. Git retains old models at `822d8c1`.

Normal code pushes run static Pages checks, not scoring. A push changing
`models/releases/**/manifest.json` on `main` additionally starts **Refresh Market
Data**, bypassing scheduled-window deduplication. Generated snapshot commits do
not match that trigger. Manual dispatch remains available. Static Pages syncs
the latest branch after acquiring the shared deployment lock, so a queued static
run cannot republish an older checkout after a refresh. The full flow fetches
prices, rebuilds the v2 cache, scores both models, synthesizes and publishes.
Never relabel old caches or prices.
Check public tactical `model.artifactSha256`, annual
`modelMetadata.liveModel.artifactSha256`, as-of dates and refresh success against
the manifest. Tests or a code push alone do not establish a public refresh.

Rollback uses a new commit restoring both old model families and the legacy
annual research/evaluation base, followed by full fresh scoring. Do not rewrite
git history or restore old public prices as though they were freshly scored.

## Tests

Tests cover SHAP identity, validation before writes, timestamps, rollback after
write failures, backup protection, feature/cache identity, stop/return fields,
evidence mismatch rejection and model-boundary rank tracking. `verify` also runs
syntax, unit and JSON contracts. Live deployment verification is a separate gate.
