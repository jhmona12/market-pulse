# Stop-Sell Methodology

Market Pulse uses a deterministic stop-sell price for current top-decile model names and other surfaced dashboard recommendations. The stop is meant to answer one narrow question:

```text
At what end-of-day closing price should the setup be considered broken enough to exit and reallocate?
```

It is not a price target, valuation estimate, or intraday order instruction.

## Research Basis

The stop is designed around three research-informed ideas:

- Stop-loss rules are most defensible when returns have persistence. Kaminski and Lo show that simple stop-loss rules can subtract value under a random-walk assumption, but can add value when returns exhibit momentum or regime persistence.
- Momentum strategies can reverse sharply, especially when prior losers rebound and volatility rises. AQR's momentum-crash work supports paying attention to volatility and market regime when managing momentum exposure.
- Volatility-adjusted stops are preferable to fixed-percent stops because a normal pullback for one stock can be an abnormal breakdown for another. ATR is a standard way to measure that realized price movement.

Primary references:

- Kaminski and Lo, "When Do Stop-Loss Rules Stop Losses?" https://chesler.us/resources/academia/stop%20losses.pdf
- AQR, "Momentum Crashes" https://www.aqr.com/Insights/Research/Journal-Article/Momentum-Crashes
- Fidelity, "Average True Range" https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/atr
- StockCharts, "Chandelier Exit" https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/chandelier-exit

## Dashboard Rule

The dashboard displays one compact label:

```text
Stop: $123.45
```

The operating rule is:

```text
Exit on an end-of-day close below the stop.
```

The dashboard intentionally does not show the full formula on each card. The stop should be easy to scan, not a second research memo.

## Formula

For long model-ranked names:

```text
chandelier_stop = 22-day highest high - 3.0 x ATR(22)
trend_stop      = 50-day moving average - 0.5 x ATR(20)
support_stop    = 20-day lowest low - 0.25 x ATR(20)

raw_stop = max(chandelier_stop, trend_stop, support_stop)

stop_sell_price = clamp(
  raw_stop,
  lower bound = latest close - 4.0 x ATR(20),
  upper bound = latest close - 1.5 x ATR(20)
)
```

Why each component exists:

- `chandelier_stop`: lets winners run while trailing the recent high by a volatility-adjusted distance.
- `trend_stop`: tightens the exit when price threatens the medium-term trend.
- `support_stop`: respects recent closing support without forcing an exit on routine noise.
- `clamp`: prevents the final stop from being either too tight or too far away to be useful.

## Data Handling

The stop calculation uses the same fresh Yahoo daily history fetched by the Python model scorer. Yahoo provides adjusted close separately from raw high/low, so the scorer scales high, low, and open by the same daily adjustment factor before calculating ATR. This keeps split-adjusted close, high, and low on the same basis.

The briefing builder does not make a second Yahoo request for stops. It reuses the `stopSellPrice` and related fields exported in `data/model-rank-scores.json`, which reduces rate-limit pressure and keeps model ranks, trailing returns, and stop prices on the same as-of date. The machine-readable `stopSellBasis` field identifies this calculation as `balanced_atr_chandelier`.

Stops are surfaced for all current top-decile model names and top-ranked names tagged `Not Momentum Confirmed`, including `Model Rebound Watch` candidates. Rebound-watch names can also show a separate activation level; the stop remains a close-based risk-control reference, while activation is the confirmation level that would make the setup actionable.
