# Morning Desk

A personal, local-first market dashboard built around a hedge-fund-style daily morning note and an end-of-day momentum opportunity book.

## Run It

This prototype has no npm dependencies.

```bash
python3 -m http.server 4173
```

Then open `http://localhost:4173`.

There is also a small `package.json` for future convenience, but this machine does not currently have `npm` installed.

## Refresh Data

```bash
node scripts/update-data.mjs
```

That script:

- Reads editable sources from `config/news-sources.md`
- Pulls the S&P 500 constituent table from Wikipedia when available
- Adds a curated ETF universe from `config/universe.json`
- Fetches free delayed/end-of-day chart history from Yahoo Finance's public chart endpoint
- Pulls several macro series from FRED CSV endpoints
- Computes momentum signals and writes `data/snapshot.json`

For a faster test refresh:

```bash
MAX_TICKERS=80 node scripts/update-data.mjs
```

## What The App Analyzes

The dashboard has two recommendation layers. The first is rules-based: it summarizes the market from refreshed data, ranks momentum opportunities, and produces "Desk Calls" with three labels:

- `Candidate`: strong momentum setup worth deeper research.
- `Watch`: close to qualifying, but needs confirmation.
- `Risk`: strong momentum but potentially crowded or extended.

The second layer is optional AI synthesis. When `OPENAI_API_KEY` is configured, the refresh script asks the model to connect macro indicators, upcoming catalysts, public source summaries, ETF momentum, and individual company momentum into a structured strategy memo.

## AI Strategy Memo

The dashboard can also generate an AI recommendation section that combines macro indicators, public source summaries, upcoming events, ETF momentum, and individual company momentum.

Create a local `.env` file:

```bash
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL=gpt-5-nano
```

Then refresh:

```bash
node scripts/update-data.mjs
```

The AI layer writes `aiRecommendations` into `data/snapshot.json`. If no API key is present, the app still runs and shows the rules-based desk calls.

You can tune the AI behavior in `config/ai-recommendation-prompt.md`.

The refresh script logs AI usage to `data/usage-log.jsonl` and includes the latest token/cost estimate in `data/snapshot.json`. The default model is `gpt-5-nano`, OpenAI's lowest-cost GPT-5 model for summarization-style work.

## Add News Sources

Edit `config/news-sources.md` and add a row to the table. The refresh script will ingest the URL on the next run.

Good source candidates are public market commentaries, official economic release pages, research landing pages, and institution blogs with stable URLs.

## Data Notes

The MVP uses free, delayed/end-of-day sources. It is designed for once-daily review, not intraday execution. The price provider is isolated in `scripts/update-data.mjs`, so a paid real-time or licensed EOD provider can be added later behind the same data shape.

This is not financial advice. It is a personal research dashboard.

## GitHub Prep

The project is intentionally simple to publish:

- Static frontend files: `index.html`, `styles.css`, `app.js`
- Data refresh script: `scripts/update-data.mjs`
- Editable source registry: `config/news-sources.md`
- Universe config: `config/universe.json`
- Generated dashboard data: `data/snapshot.json`

Before pushing publicly, review whether you want to commit `data/snapshot.json`. Keeping it committed makes the static dashboard work immediately on GitHub Pages. Excluding it keeps generated market data out of the repo, but then a deployment workflow needs to run the refresh script.
