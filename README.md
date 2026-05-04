# Market Pulse

Market Pulse is a static market research dashboard for a once-daily, end-of-day review. It combines macro data, public market commentary, sector performance, and momentum screens into a hedge-fund-style research note.

The project is built to run cheaply with free data sources. It is not an intraday trading terminal and it is not financial advice.

## What It Does

- Summarizes the current market setup in a daily read.
- Screens S&P 500 constituents and a curated ETF universe for momentum setups.
- Scores current S&P 500 stocks with a production XGBoost learning-to-rank model.
- Tracks sector performance using major sector ETF proxies.
- Pulls macro indicators from public FRED CSV endpoints.
- Tracks upcoming macro events such as CPI, payrolls, GDP, and FOMC dates.
- Ingests public research and commentary sources listed in `config/news-sources.md`.
- Discovers recent articles from those source landing pages and prioritizes newer dated articles.
- Optionally generates an AI Strategy Memo that combines article commentary, macro context, sector behavior, model rankings, and momentum data into structured research recommendations.
- Enriches model candidates with company descriptions, investor relations links, earnings context, and recent ticker-specific news where free sources are available.
- Uses AI to draft the Daily Read executive snapshot, with deterministic model, sector, macro, and source-tape metrics as guardrails and fallback.

## Live Site

The dashboard is designed to be published as a static GitHub Pages site:

```text
https://jhmona12.github.io/market-pulse/
```

The site reads from `data/snapshot.json`. When that file changes, the public page reflects the latest generated market snapshot after GitHub Pages redeploys.

## Local Use

This project has no required npm install step.

Start a local static server from the project root:

```bash
python3 -m http.server 4173
```

Then open:

```text
http://localhost:4173
```

If port `4173` is already in use, choose another port:

```bash
python3 -m http.server 4174
```

## Refreshing Data

Run the refresh script:

```bash
node scripts/update-data.mjs
```

The refresh script:

- Reads source URLs from `config/news-sources.md`
- Fetches source landing pages and discovers likely research/commentary articles
- Extracts article titles, summaries, excerpts, and publication dates when available
- Pulls S&P 500 constituents from Wikipedia when available
- Adds ETFs from `config/universe.json`
- Fetches delayed/end-of-day chart history from Yahoo Finance's public chart endpoint
- Pulls selected macro series from FRED
- Computes momentum, trend, breadth, RSI, volume, and relative-strength metrics
- Reads `data/model-rank-scores.json` when available and promotes the XGBoost model rank as the primary single-name score
- Enriches the top model candidates with company context from Nasdaq, company investor relations pages, and Yahoo Finance news RSS
- Writes the dashboard snapshot to `data/snapshot.json`

To refresh model rankings before the dashboard snapshot:

```bash
.venv-model/bin/python scripts/modeling/score_live_rank_model.py --output data/model-rank-scores.json
node scripts/update-data.mjs
```

`data/model-rank-scores.json` is an intermediate file and is ignored by git. The generated `data/snapshot.json` contains the model rankings needed by the static site.

Some current constituents may be fetched but not scored if they do not have enough clean trailing data to populate every required feature. The scorer records those symbols in the snapshot model metadata.

For a faster test run:

```bash
SKIP_AI=1 MAX_TICKERS=80 SNAPSHOT_OUTPUT=data/snapshot.dev.json node scripts/update-data.mjs
```

Useful source-ingestion knobs:

```bash
SOURCE_ARTICLES_PER_SOURCE=3
SOURCE_ARTICLE_CANDIDATES=10
COMPANY_CONTEXT_COUNT=12
```

## AI Strategy Memo

The AI layer is optional. If `OPENAI_API_KEY` is available, the refresh script calls the OpenAI Responses API and writes structured AI output into `data/snapshot.json`.

The intended division of labor is:

- The XGBoost rank model is the deterministic stock-selection engine.
- The rules-based screener provides technical context, ETF confirmation, and fallback rankings.
- The AI Strategy Memo explains the model-ranked candidates against the macro calendar and public source tape.
- The Daily Read is AI-written when available, but it is anchored to deterministic facts and falls back to a rules-based executive snapshot if the AI call fails.

Create a local `.env` file:

```bash
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL=gpt-5-nano
```

The default model is `gpt-5-nano`, chosen for low-cost summarization and synthesis. The prompt can be edited in:

```text
config/ai-recommendation-prompt.md
```

The AI recommendation schema is intentionally constrained:

- When model candidates are available, recommendations must use symbols from the model-ranked candidate list.
- Ticker spelling must match the supplied data.
- Each recommendation includes model evidence such as rank, percentile, and model reasons.
- Each recommendation includes a short company overview, earnings context, and a recent-news/catalyst read when company context is available.
- The AI prompt tells the model to prefer investor relations pages as the primary company source, use Nasdaq for earnings data when dates are machine-readable, and avoid overstating news causality.
- Each recommendation must connect macro/publication evidence with technical momentum evidence.
- Recommendations include setup, why-now, macro evidence, technical evidence, risk, and invalidation.
- Source references use source IDs from the ingested article tape when article data is available.

If no API key is configured, the dashboard still works with the model-ranked Daily Read, Desk Calls, Momentum Book, Sector Performance, Macro Pulse, and Source Tape.

AI usage estimates are written into `data/snapshot.json`; local usage logs are written to `data/usage-log.jsonl`, which is ignored by git.

## Source Registry

Add or edit research sources in:

```text
config/news-sources.md
```

The file is a markdown table with this shape:

```text
| Name | URL | Category | Cadence | Trust | Notes |
```

Good source candidates include:

- Public market commentaries
- Research landing pages
- Institution blogs
- Official economic release calendars
- Stable pages with dated article metadata

Some sites block automated requests or hide publication data. In those cases, the source may appear as failed or may show an article without a date. That is expected behavior for free, public web sources.

## Momentum Screen

The screener ranks S&P 500 stocks and selected ETFs using end-of-day technical metrics, including:

- Price versus 20-day, 50-day, 100-day, and 200-day moving averages
- Recent moving-average crossovers
- 20-day and 60-day returns
- Relative strength versus SPY
- RSI
- Volume versus 20-day average
- Proximity to recent highs

The dashboard exposes configurable signal settings in the UI, including minimum score, RSI ceiling, trend requirements, volume confirmation, and universe selection.

## Scheduled Refresh

The repository includes a GitHub Actions workflow at:

```text
.github/workflows/refresh-data.yml
```

It is configured to check daily around 5 PM Pacific. Because GitHub cron runs in UTC, the workflow checks both `00:00` and `01:00` UTC and only runs the refresh when the current `America/Los_Angeles` hour is `17`.

When the refresh runs successfully, it:

- Scores the current S&P 500 universe with the committed XGBoost rank model when dependencies and free data endpoints are available
- Regenerates `data/snapshot.json`
- Commits the updated snapshot back to `main` if it changed
- Triggers a GitHub Pages redeploy through the repository's Pages workflow

GitHub scheduled workflows may start several minutes late. Manual refreshes can also be triggered from the Actions tab with `workflow_dispatch`.

## Modeling Pipeline

The repo includes a separate Python modeling workflow for researching S&P 500 momentum signals. Raw history, research models, predictions, and reports are ignored by git. The production rank model and compact explainability artifact are exported separately into `models/rank/` so GitHub Actions and the static dashboard can use them.

The current primary model is an XGBoost learning-to-rank model. It is designed to answer a daily portfolio-selection question: which current S&P 500 stocks look most likely to outperform their sector over the next 14 trading days?

### Label

For every feature-complete stock/date row:

- Entry is assumed at the next trading day's close, not the same-day close.
- Exit is assumed 14 trading days later.
- The stock's forward return is compared with the matching sector ETF's forward return.
- A 15 bps round-trip cost is subtracted from the sector-neutral return.
- Stocks are ranked within each date by that after-cost sector-neutral return.
- The rank label is a daily `0-4` relevance grade: top decile `4`, next decile `3`, middle `2`, second-worst decile `1`, bottom decile `0`.

In plain English, the model is not trying to forecast the exact stock price. It is trying to sort the daily universe so the top-ranked basket contains stocks with better near-term sector-relative momentum setups than the bottom-ranked basket.

### Production Dashboard Artifacts

The dashboard uses these committed artifacts:

```text
models/rank/xgboost_rank_sector14_tuned.json                 Production XGBoost rank model
models/rank/xgboost_rank_sector14_tuned_metadata.json        Feature list, target, training window, and parameters
models/rank/xgboost_rank_sector14_tuned_explainability.json  Compact SHAP-style feature explanation
```

The production model is exported from the feature-complete training dataset:

```bash
.venv-model/bin/python scripts/modeling/export_production_rank_model.py --num-boost-round 25
```

The current SHAP-style explainability artifact was generated from the tuned holdout model. It stores the highest-impact features by mean absolute contribution, which can be surfaced in the dashboard and AI prompt as model context.

Legacy/research labels still exist for comparison:

- `label_outperform_spy_14d`: stock 14-day return minus SPY return `> 0`.
- `meta_label_momentum_success`: candidate-only success label using a return hurdle and drawdown floor.

### Universe And Null Treatment

- Universe: current S&P 500 constituents.
- Price history: about 10 years of cached daily adjusted-close history from free public endpoints.
- Short-history current constituents are kept by default once they have enough trailing data to compute every required feature and enough future data to compute the label.
- Nulls are not imputed. Rows with missing required features, infinite values, or missing labels are dropped from the training dataset.
- Macro/FRED observation-date fields are excluded by default because observation dates are not the same thing as market release dates.

The current feature-complete dataset has `1,082,323` rows, `502` symbols, `156` features, zero feature null cells, and zero label mismatches. Most symbols have full coverage: `449` of `502` have more than 95% of the available post-feature history. The shortest histories are mostly spin-offs, IPOs, or newer S&P additions such as `SNDK`, `GEV`, `SOLV`, `VLTO`, `KVUE`, `GEHC`, `CEG`, `HOOD`, `APP`, and `COIN`.

Minimum-history filters are available for research:

```bash
.venv-model/bin/python scripts/modeling/walk_forward_rank_model.py --min-symbol-rows 756
```

Recent sensitivity tests argued against removing short-history names by default:

```text
No minimum filter:     +2.75% top-decile return, 58.1% hit rate, +3.45% spread
Minimum ~2 years:      +2.51% top-decile return, 56.6% hit rate, +2.86% spread
Minimum ~3 years:      +2.31% top-decile return, 56.0% hit rate, +2.75% spread
Minimum ~5 years:      +2.31% top-decile return, 56.3% hit rate, +2.93% spread
Minimum ~7 years:      +2.18% top-decile return, 57.3% hit rate, +2.78% spread
```

### Features

The model intentionally excludes article/news features for now. The current feature set focuses on price-derived and cross-sectional signals:

- Momentum and trend: 20-day, 60-day, 120-day, skip-window 126-day and 252-day momentum, moving-average distance, and 52-week high/low distance.
- Risk and volatility: 20-day and 60-day volatility, downside volatility, beta to SPY, max daily return, and volatility relative to sector/SPY.
- Liquidity: volume ratios, dollar volume, and Amihud-style illiquidity.
- Relative strength: stock returns versus SPY, sector ETF, sector median, and same-day cross-sectional ranks.
- Market context: S&P breadth, SPY trend/volatility, sector ETF momentum, and sector ETF trend.

Feature and target definitions live in:

```text
scripts/modeling/schema.py          Label names, non-feature columns, and ablation groups
scripts/modeling/model_features.py  Shared train/live feature list and cross-sectional transforms
```

Keeping the shared model features in one module reduces train/live drift between the historical dataset builder and the daily scorer.

### Evaluation

The main evaluation is an embargoed walk-forward test:

- 4 recent non-overlapping test folds.
- 63 trading days per test fold.
- 126 trading days for validation.
- 14 trading-day embargo around validation/test windows.
- Daily top-decile basket evaluation against sector-neutral after-cost 14-day returns.

The current tuned rank model results:

```text
Top-decile sector-neutral 14-day return: +2.75%
Top-decile hit rate:                     58.1%
Top-minus-bottom spread:                 +3.45%
```

The backtest report also includes `non_overlapping_offsets`, which evaluates every possible 14-trading-day rebalance offset separately. This helps avoid over-reading overlapping daily forward labels. For the tuned model, the non-overlapping offset top-decile returns ranged from `+2.31%` to `+3.05%`, with a mean of `+2.75%`.

### Ablation And Tuning

Feature-group ablation showed that risk/volatility, liquidity, and market context are doing real work:

```text
Full tuned model:      +2.75% top-decile return
No volatility/risk:    +1.60%
No liquidity:          +2.43%
No market context:     +2.53%
No long momentum:      +2.70%
No sector-relative:    +2.62%
```

The current default rank-model hyperparameters were selected from a compact walk-forward tuning pass. The winning direction was simpler and more regularized than the prior baseline:

```text
eta:                   0.04
max_depth:             3
min_child_weight:      80
subsample:             0.85
colsample_bytree:      0.80
lambda:                4.0
num_boost_round:       700
early_stopping_rounds: 40
```

The tuning objective should remain practical, not purely statistical: prioritize top-decile sector-neutral return and top-minus-bottom spread in walk-forward tests, use hit rate as a sanity check, and confirm behavior with non-overlapping rebalance offsets. Future tuning should use a wider random or Bayesian search, but only after adding transaction-cost assumptions, position sizing, and portfolio-level risk controls.

Modeling scripts:

```text
scripts/modeling/fetch_training_data.py    Cache raw price and macro data locally
scripts/modeling/build_training_dataset.py Build the labeled feature matrix
scripts/modeling/data_quality_report.py    Audit labels, nulls, outliers, and raw price alignment
scripts/modeling/model_features.py         Shared feature definitions for train/live scoring parity
scripts/modeling/train_xgboost_model.py    Train the XGBoost classifier and save reports
scripts/modeling/train_rank_model.py       Train the sector-neutral XGBoost learning-to-rank model
scripts/modeling/train_meta_label_model.py Train the candidate-only momentum meta-label model
scripts/modeling/walk_forward_rank_model.py Run embargoed walk-forward rank-model evaluation
scripts/modeling/backtest_model.py         Compare model scores against baseline ranking strategies
scripts/modeling/explain_model.py          Generate XGBoost SHAP-style contribution summaries
scripts/modeling/export_model_explainability.py Export compact SHAP metadata for the app
scripts/modeling/export_production_rank_model.py Export the committed production rank model
scripts/modeling/score_live_rank_model.py  Score the latest S&P 500 universe for the dashboard
scripts/modeling/feature_ablation_rank_model.py Run rank-model feature-group ablations
scripts/modeling/tune_rank_model.py        Run compact walk-forward hyperparameter tuning presets
scripts/modeling/run_model_pipeline.py     Run the full modeling pipeline
scripts/modeling/setup_training_env.sh     Create a local training venv and install the OpenMP runtime
scripts/modeling/requirements.txt          Python package requirements for training
```

Local modeling cache and outputs:

```text
data/modeling/raw/                         Cached price and macro histories
data/modeling/features/                    Labeled training dataset
data/modeling/models/                      Saved trained model artifacts
data/modeling/reports/                     Training reports and test predictions
models/rank/                               Committed production model artifacts for the app
```

The modeling cache is ignored by git so large raw history files and research artifacts stay local by default.

Model environment setup:

```bash
bash scripts/modeling/setup_training_env.sh
```

Example workflow:

```bash
/Users/harrisonmona/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 scripts/modeling/fetch_training_data.py --years 10
/Users/harrisonmona/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 scripts/modeling/build_training_dataset.py
.venv-model/bin/python scripts/modeling/data_quality_report.py
.venv-model/bin/python scripts/modeling/train_rank_model.py
.venv-model/bin/python scripts/modeling/walk_forward_rank_model.py
.venv-model/bin/python scripts/modeling/backtest_model.py --predictions xgboost_rank_sector14_walk_forward_test_predictions.csv --output-name xgboost_rank_sector14_walk_forward_backtest
.venv-model/bin/python scripts/modeling/explain_model.py --model-name xgboost_rank_sector14
```

After the raw cache exists, the venv can run the build, train, backtest, and explanation steps together:

```bash
.venv-model/bin/python scripts/modeling/run_model_pipeline.py --skip-fetch
```

To run the newer ranking and meta-label experiments:

```bash
.venv-model/bin/python scripts/modeling/run_model_pipeline.py --skip-fetch --train-rank --train-meta --walk-forward-rank
```

To run feature ablations:

```bash
.venv-model/bin/python scripts/modeling/feature_ablation_rank_model.py --output-name xgboost_rank_sector14_feature_ablation
```

To run compact hyperparameter tuning:

```bash
.venv-model/bin/python scripts/modeling/tune_rank_model.py --output-name xgboost_rank_sector14_tuning
```

To refresh SHAP-style explainability for the tuned holdout model:

```bash
.venv-model/bin/python scripts/modeling/train_rank_model.py --model-name xgboost_rank_sector14_feature_v2_tuned_holdout
.venv-model/bin/python scripts/modeling/explain_model.py --model-name xgboost_rank_sector14_feature_v2_tuned_holdout
.venv-model/bin/python scripts/modeling/export_model_explainability.py
```

Raw FRED macro fields are excluded from the default training dataset because their observation dates are not the same thing as market release dates. They can be included for experimentation with:

```bash
/Users/harrisonmona/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 scripts/modeling/build_training_dataset.py --include-macro
```

The older SPY-relative classifier is still available for research comparisons:

```bash
.venv-model/bin/python scripts/modeling/train_xgboost_model.py --model-name xgboost_spy14_no_macro --exclude-macro
.venv-model/bin/python scripts/modeling/backtest_model.py --predictions xgboost_spy14_no_macro_test_predictions.csv --output-name xgboost_spy14_no_macro_backtest
.venv-model/bin/python scripts/modeling/train_xgboost_model.py --model-name xgboost_spy14_cap50 --train-excess-return-cap 0.50
.venv-model/bin/python scripts/modeling/backtest_model.py --predictions xgboost_spy14_cap50_test_predictions.csv --output-name xgboost_spy14_cap50_backtest
```

Backtest reports compare XGBoost predictions against simple momentum and sector-neutral baselines. The reports are intentionally local-only and are written to `data/modeling/reports/`.

## Project Structure

```text
index.html                         Static dashboard shell
styles.css                         Dashboard styling
app.js                             Browser rendering logic
scripts/update-data.mjs            Data refresh, source ingestion, screening, AI call
config/news-sources.md             Editable public source registry
config/universe.json               ETF and fallback universe configuration
config/ai-recommendation-prompt.md AI memo prompt
data/snapshot.json                 Generated dashboard data
.github/workflows/                 GitHub Pages and scheduled refresh workflows
```

## Limitations

- Data is free, delayed, and best-effort.
- The dashboard is designed for once-daily research, not intraday execution.
- Public websites may change markup, block automated requests, or omit publication dates.
- Yahoo Finance and other free endpoints are not a licensed market data feed.
- AI output should be treated as research synthesis, not trading advice.

## Disclaimer

Market Pulse is a research tool. It does not provide financial, investment, tax, or legal advice. Any trading or investment decision should be independently verified against reliable data and your own risk framework.
