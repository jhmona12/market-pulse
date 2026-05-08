# News And Research Sources

Add new free sources to this table. The ingestion script reads rows with this shape:

`| Name | URL | Category | Cadence | Trust | Notes |`

Official macro rows are treated differently from ordinary commentary sources. The table below helps the dashboard monitor release calendars, but the refresh script also checks configured primary-source release pages after a scheduled macro event has occurred. For example, once Employment Situation is released, the script pulls the BLS Employment Situation release directly, extracts payrolls, unemployment, wage growth, revisions, labor-force details, and sector job changes, and feeds those facts into the Daily Read and AI memo even if no bank or news source has reacted yet.

When adding official macro sources, prefer primary government or central-bank pages over articles summarizing them. Use the `Official macro` category, include the release calendar if available, and note the specific release family in the Notes field so the script can be extended with a targeted parser when needed.

| Name | URL | Category | Cadence | Trust | Notes |
|---|---|---|---|---|---|
| J.P. Morgan Markets Insights | https://www.jpmorgan.com/insights/markets | Institution research | Weekly | High | Public markets and macro research hub. |
| Wells Fargo Advisors Stock Market News | https://www.wellsfargoadvisors.com/research-analysis/commentary/stock-market-news.htm | Institution research | Weekly | High | Public stock market commentary; replaces retired investment-institute URL. |
| Merrill Capital Market Outlook | https://www.ml.com/capital-market-outlook.html | Institution research | Weekly | High | Public Merrill / Bank of America capital markets outlook. |
| Bank of America Institute | https://institute.bankofamerica.com/ | Institution research | Weekly | High | Public consumer, macro, and market research. |
| Raymond James Commentary And Insights | https://www.raymondjames.com/commentary-and-insights | Institution research | Periodic | High | Public market commentary and insights hub; site may intermittently reject automated checks. |
| Schwab Weekly Market Outlook | https://www.schwab.com/learn/story/weekly-market-outlook | Institution research | Weekly | High | Public weekly market outlook covering earnings, Fed, rates, economic data, and sector setup. |
| Fidelity Market Insights | https://www.fidelity.com/viewpoints/market-and-economic-insights | Institution research | Periodic | High | Public Fidelity market and economic insights hub. |
| State Street Global Advisors Insights | https://www.ssga.com/us/en/intermediary/insights | Institution research | Periodic | High | Public SPDR / State Street macro, ETF, and market insights. |
| Morgan Stanley Ideas | https://www.morganstanley.com/ideas | Institution research | Periodic | High | Public Morgan Stanley Ideas hub for markets, macro, thematic, and policy commentary. |
| Federal Reserve Press Releases | https://www.federalreserve.gov/newsevents/pressreleases.htm | Official macro | As released | Very high | FOMC and regulatory releases. |
| FOMC Calendars | https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm | Official macro | Scheduled | Very high | Rate decision schedule and materials. |
| ECB Monetary Policy Decisions | https://www.ecb.europa.eu/press/govcdec/mopo/html/index.en.html | Official macro | As released | Very high | European Central Bank policy decisions and statements. |
| Bank of England Monetary Policy Summary | https://www.bankofengland.co.uk/monetary-policy-summary-and-minutes/monetary-policy-summary-and-minutes | Official macro | As released | Very high | Bank of England policy decisions, summaries, and minutes. |
| U.S. Treasury Press Releases | https://home.treasury.gov/news/press-releases | Official macro | As released | Very high | Treasury refunding, sanctions, borrowing, and policy releases. |
| EIA Today In Energy | https://www.eia.gov/todayinenergy/ | Official macro | Daily | Very high | Energy market and oil/gas context from the U.S. Energy Information Administration. |
| BLS Economic News Releases | https://www.bls.gov/schedule/news_release/ | Official macro | Scheduled | Very high | CPI, payrolls, unemployment, PPI, and labor releases. |
| BEA News Release Schedule | https://www.bea.gov/news/schedule | Official macro | Scheduled | Very high | GDP, PCE, income, and trade data. |
| FRED Releases | https://fred.stlouisfed.org/releases | Official macro | Scheduled | Very high | St. Louis Fed release directory. |
| SEC Current Reports | https://www.sec.gov/edgar/search/#/category=custom&forms=8-K | Filings | Continuous | Very high | Company current reports and catalysts. |
| Axios Markets RSS | https://news.google.com/rss/search?q=%22Axios%20Markets%22%20when:30d&hl=en-US&gl=US&ceid=US:en | News RSS | Daily | Medium | Google News RSS scoped to Axios Markets coverage; used because direct Axios newsletter pages may block automated checks. |
| Axios Macro RSS | https://news.google.com/rss/search?q=%22Axios%20Macro%22%20when:30d&hl=en-US&gl=US&ceid=US:en | News RSS | Daily | Medium | Google News RSS scoped to Axios Macro coverage; used because direct Axios newsletter pages may block automated checks. |
| CNBC Markets | https://www.cnbc.com/markets/ | News | Continuous | Medium | Public market news page; respect site terms. |
| CNBC Top News RSS | https://www.cnbc.com/id/100003114/device/rss/rss.html | News RSS | Continuous | Medium | Structured CNBC top business and market news feed. |
| CNBC Earnings RSS | https://www.cnbc.com/id/15839135/device/rss/rss.html | News RSS | Continuous | Medium | Structured CNBC earnings news feed. |
| MarketWatch Top Stories RSS | https://feeds.content.dowjones.io/public/rss/mw_topstories | News RSS | Continuous | Medium | Structured MarketWatch top stories feed. |
| Yahoo Finance News | https://finance.yahoo.com/news/ | News | Continuous | Medium | Public finance news page; respect site terms. |
