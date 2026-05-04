# Market Pulse

Market Pulse is a static market research dashboard for a once-daily, end-of-day review. It combines macro data, public market commentary, sector performance, and momentum screens into a hedge-fund-style research note.

The project is built to run cheaply with free data sources. It is not an intraday trading terminal and it is not financial advice.

## What It Does

- Summarizes the current market setup in a daily read.
- Screens S&P 500 constituents and a curated ETF universe for momentum setups.
- Tracks sector performance using major sector ETF proxies.
- Pulls macro indicators from public FRED CSV endpoints.
- Tracks upcoming macro events such as CPI, payrolls, GDP, and FOMC dates.
- Ingests public research and commentary sources listed in `config/news-sources.md`.
- Discovers recent articles from those source landing pages and prioritizes newer dated articles.
- Optionally generates an AI Strategy Memo that combines article commentary, macro context, sector behavior, and momentum data into structured research recommendations.

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
- Writes the dashboard snapshot to `data/snapshot.json`

For a faster test run:

```bash
MAX_TICKERS=80 node scripts/update-data.mjs
```

Useful source-ingestion knobs:

```bash
SOURCE_ARTICLES_PER_SOURCE=3
SOURCE_ARTICLE_CANDIDATES=10
```

## AI Strategy Memo

The AI layer is optional. If `OPENAI_API_KEY` is available, the refresh script calls the OpenAI Responses API and writes structured AI output into `data/snapshot.json`.

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

- Recommendations must use symbols from the screened momentum universe.
- Ticker spelling must match the supplied data.
- Each recommendation must connect macro/publication evidence with technical momentum evidence.
- Recommendations include setup, why-now, macro evidence, technical evidence, risk, and invalidation.
- Source references use source IDs from the ingested article tape when article data is available.

If no API key is configured, the dashboard still works with the rules-based Daily Read, Desk Calls, Momentum Book, Sector Performance, Macro Pulse, and Source Tape.

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

- Regenerates `data/snapshot.json`
- Commits the updated snapshot back to `main` if it changed
- Triggers a GitHub Pages redeploy through the repository's Pages workflow

GitHub scheduled workflows may start several minutes late. Manual refreshes can also be triggered from the Actions tab with `workflow_dispatch`.

## Modeling Pipeline

The repo now includes a separate Python modeling workflow for training a directional classifier on the current S&P 500 universe.

Model target:

- Label: stock total return over the next 14 trading days minus SPY total return over the next 14 trading days `> 0`
- Universe: current S&P 500 constituents only
- History window: 10 years of cached daily price history

Modeling scripts:

```text
scripts/modeling/fetch_training_data.py    Cache raw price and macro data locally
scripts/modeling/build_training_dataset.py Build the labeled feature matrix
scripts/modeling/train_xgboost_model.py    Train the XGBoost classifier and save reports
scripts/modeling/backtest_model.py         Compare model scores against baseline ranking strategies
scripts/modeling/explain_model.py          Generate XGBoost SHAP-style contribution summaries
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
```

The modeling cache is ignored by git so large raw history files and model artifacts stay local by default.

Model environment setup:

```bash
bash scripts/modeling/setup_training_env.sh
```

Example workflow:

```bash
/Users/harrisonmona/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 scripts/modeling/fetch_training_data.py --years 10
/Users/harrisonmona/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 scripts/modeling/build_training_dataset.py
.venv-model/bin/python scripts/modeling/train_xgboost_model.py
.venv-model/bin/python scripts/modeling/backtest_model.py
.venv-model/bin/python scripts/modeling/explain_model.py
```

After the raw cache exists, the venv can run the build, train, backtest, and explanation steps together:

```bash
.venv-model/bin/python scripts/modeling/run_model_pipeline.py --skip-fetch
```

To test whether raw macro fields are swamping the cross-sectional stock signal:

```bash
.venv-model/bin/python scripts/modeling/train_xgboost_model.py --model-name xgboost_spy14_no_macro --exclude-macro
.venv-model/bin/python scripts/modeling/backtest_model.py --predictions xgboost_spy14_no_macro_test_predictions.csv --output-name xgboost_spy14_no_macro_backtest
```

The current feature set is v1 and intentionally excludes article/news features. It focuses on:

- Momentum and trend
- Volume and volatility
- Relative strength versus SPY
- Sector-relative momentum and trend
- Market breadth
- Sector ETF context
- Macro series when the FRED cache is available

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
