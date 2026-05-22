# Long-Horizon Rank Model

This note documents the separate 252-trading-day research model. It is intentionally not mixed with the production 14-day dashboard model.

## Purpose

The 14-day model is a tactical trading model. The 252-day model is a research model for one-year holding candidates.

Plain-English question:

> If I buy this stock at the next close and hold it for roughly one trading year, how attractive does it look versus other S&P 500 names after controlling for sector?

## File Naming

The long-horizon path uses explicit `252d` or `sector252` names.

```text
scripts/modeling/build_long_horizon_dataset.py
data/modeling/features/long_horizon_training_dataset.csv.gz
data/modeling/features/long_horizon_training_dataset_metadata.json
data/modeling/models/xgboost_rank_sector252_research.json
data/modeling/reports/xgboost_rank_sector252_walk_forward_report.json
data/modeling/reports/xgboost_rank_sector252_monthly_cohorts.csv
data/modeling/reports/xgboost_rank_sector252_quarterly_cohorts.csv
data/modeling/reports/xgboost_rank_sector252_baseline_comparison_summary.csv
data/modeling/reports/xgboost_rank_sector252_walk_forward_baseline_comparison_summary.csv
data/modeling/reports/xgboost_rank_sector252_15y_monthly_walk_forward_baseline_comparison_summary.csv
data/modeling/reports/xgboost_rank_sector252_15y_monthly_tuned_baseline_comparison_summary.csv
models/long-horizon/xgboost_rank_sector252_research.json
models/long-horizon/xgboost_rank_sector252_research_metadata.json
models/long-horizon/xgboost_rank_sector252_research_explainability.json
models/long-horizon/xgboost_rank_sector252_15y_monthly_tuned_research.json
models/long-horizon/xgboost_rank_sector252_15y_monthly_tuned_research_metadata.json
models/long-horizon/xgboost_rank_sector252_15y_monthly_tuned_research_explainability.json
models/long-horizon/xgboost_rank_sector252_15y_monthly_tuned_baseline_comparison.json
models/long-horizon/xgboost_rank_sector252_baseline_comparison.json
models/long-horizon/xgboost_rank_sector252_walk_forward_baseline_comparison.json
models/long-horizon/xgboost_rank_sector252_drawdown_adjusted_walk_forward_baseline_comparison.json
models/long-horizon/long_horizon_label_comparison.json
data/long-horizon-research.json
```

The `data/modeling/` directory is ignored by git. Local research datasets and model artifacts can be large and should not be confused with committed dashboard artifacts.

The `models/long-horizon/` directory stores small exported 252-day research artifacts. The dashboard reads the compact committed `data/long-horizon-research.json` file, which is generated from those artifacts.

Committed dashboard artifacts remain under:

```text
models/rank/xgboost_rank_sector14_tuned.json
models/rank/xgboost_rank_sector14_tuned_metadata.json
models/rank/xgboost_rank_sector14_tuned_explainability.json
```

## Label

The starting label is a daily cross-sectional sector-neutral rank label:

```text
entry = next trading day's adjusted close
exit = adjusted close 252 trading days after entry
stock_return_252d = exit / entry - 1
sector_return_252d = matching sector ETF return over the same window
target_return = stock_return_252d - sector_return_252d - round_trip_cost
```

The model target is:

```text
relevance_grade_sector_neutral_252d
```

The economic return column is:

```text
sector_neutral_forward_return_252d_after_cost
```

Each date is ranked cross-sectionally:

```text
top decile          -> 4
next decile         -> 3
middle 60%          -> 2
second-worst decile -> 1
bottom decile       -> 0
```

An alternate path-quality label is also available for research:

```text
relative_drawdown = stock_forward_max_drawdown_252d - sector_forward_max_drawdown_252d
drawdown_adjusted_return = sector_neutral_forward_return_252d_after_cost + 0.5 * clipped(relative_drawdown, -50%, +50%)
target column = relevance_grade_drawdown_adjusted_252d
return column = drawdown_adjusted_sector_neutral_return_252d_after_cost
```

Plain English: a stock that outperforms its sector and gets there with a smaller forward drawdown receives a better label. A stock that outperforms only after a much deeper drawdown receives a penalty. This is an experiment, not the production label.

## Initial Dataset

Initial local dataset build:

```text
Rows:        963,047
Symbols:     501
Dates:       2,008
Date range:  2017-05-04 to 2025-04-29
Features:    156
```

The label window ends about one year before the latest raw price data because every training row needs a complete future 252-trading-day outcome.

## Promoted Training Method

The current promoted long-horizon research method is:

```text
raw price cache: about 20 years
labeled dataset window: 2012-05-23 to 2025-05-19
exported model training window: 2012-05-23 to 2025-05-01
training rows after monthly sampling: 70,734
monthly sampled dates: 157
training symbols: 500
target: relevance_grade_sector_neutral_252d
return column: sector_neutral_forward_return_252d_after_cost
train/validation sampling: first trading day of each month
test evaluation: full daily test windows, summarized mainly with monthly and quarterly cohorts
selected parameters: eta 0.025, max_depth 2, min_child_weight 80, subsample 0.90, colsample_bytree 0.85, lambda 4
exported boost rounds: 60
```

Rationale:

- The 252-trading-day label makes adjacent daily training rows highly overlapping. Monthly sampling reduces duplicate label information without changing the out-of-sample test window.
- A 15-year labeled window preserves more regimes than the original 10-year run while avoiding the weaker 20-year result, which may mix older regimes with current-constituent survivor bias.
- We compared 10-year daily, 15-year daily/weekly/monthly, and 20-year daily/weekly/monthly training. The 15-year monthly run had the strongest monthly and quarterly median top-decile returns.
- We then ran an efficient targeted hyperparameter tuning pass. A shallow, lower-learning-rate XGBoost preset beat the inherited default in the wider two-fold screen and a narrower four-fold confirmation.

Comparison:

| Run | Train Sample | Monthly Median | Quarterly Median | Quarterly Spread Median |
| --- | --- | ---: | ---: | ---: |
| 10y baseline | daily | +35.84% | +34.56% | +35.91% |
| 15y | daily | +27.98% | +23.50% | +30.59% |
| 15y | weekly | +27.03% | +25.97% | +31.70% |
| 15y | monthly | +36.49% | +37.24% | +43.06% |
| 20y | daily | +16.22% | +16.22% | +18.91% |
| 20y | weekly | +26.11% | +26.11% | +29.08% |
| 20y | monthly | +28.59% | +27.09% | +29.35% |

This does not prove older data is bad. It says the best current long-horizon research setup is not "more daily rows"; it is a cleaner 15-year window with monthly train/validation sampling.

## Initial Research Run

Command:

```bash
.venv-model/bin/python scripts/modeling/walk_forward_rank_model.py \
  --dataset long_horizon_training_dataset.csv.gz \
  --model-name xgboost_rank_sector252_walk_forward \
  --target-column relevance_grade_sector_neutral_252d \
  --return-column sector_neutral_forward_return_252d_after_cost \
  --folds 2 \
  --test-days 252 \
  --validation-days 252 \
  --embargo-days 252 \
  --min-train-days 700 \
  --num-boost-round 300 \
  --early-stopping-rounds 25
```

Initial walk-forward results:

```text
Combined top-decile 252D sector-neutral return: +38.94%
Top-decile hit rate:                             58.9%
Top-minus-bottom 252D spread:                    +41.16%
```

Monthly cohort summary:

```text
Monthly cohorts:                         25
Mean top-decile return:                  +40.93%
Median top-decile return:                +37.34%
Mean top-minus-bottom spread:            +43.29%
```

Quarterly cohort summary:

```text
Quarterly cohorts:                       9
Mean top-decile return:                  +46.34%
Median top-decile return:                +32.24%
Mean top-minus-bottom spread:            +49.69%
```

## Drawdown-Adjusted Label Test

Command:

```bash
.venv-model/bin/python scripts/modeling/walk_forward_rank_model.py \
  --dataset long_horizon_training_dataset.csv.gz \
  --model-name xgboost_rank_sector252_drawdown_adjusted_walk_forward \
  --target-column relevance_grade_drawdown_adjusted_252d \
  --return-column drawdown_adjusted_sector_neutral_return_252d_after_cost \
  --folds 2 \
  --test-days 252 \
  --validation-days 252 \
  --embargo-days 252 \
  --min-train-days 700 \
  --num-boost-round 300 \
  --early-stopping-rounds 25
```

The drawdown-adjusted label improved path quality, but gave up too much actual sector-neutral return in the current walk-forward test:

| Label | Daily Mean Actual Return | Daily Median Actual Return | Quarterly Median Actual Return | Mean Forward Max Drawdown |
| --- | ---: | ---: | ---: | ---: |
| Sector-neutral label | +38.94% | +35.42% | +34.56% | -19.82% |
| Drawdown-adjusted label | +21.84% | +16.86% | +19.73% | -17.39% |

The practical read: keep `relevance_grade_sector_neutral_252d` as the main label for now. The drawdown-adjusted label is useful as a diagnostic because it shows the cost of forcing smoother paths. It is not yet better for selecting top one-year candidates.

## Hyperparameter Tuning

The tuning pass was intentionally efficient. The 252-day label creates highly overlapping daily outcomes, so the first pass compared a small number of defensible presets using monthly train/validation sampling, one-year validation windows, one-year test windows, and a one-year embargo between train, validation, and test. The wider screen used two recent folds; the confirmation run compared only the leading tuned shape and the inherited default across four folds.

Command:

```bash
.venv-model/bin/python scripts/modeling/tune_rank_model.py \
  --dataset long_horizon_training_dataset_15y.csv.gz \
  --output-name xgboost_rank_sector252_15y_monthly_tuning \
  --target-column relevance_grade_sector_neutral_252d \
  --return-column sector_neutral_forward_return_252d_after_cost \
  --folds 2 \
  --test-days 252 \
  --validation-days 252 \
  --embargo-days 252 \
  --min-train-days 700 \
  --train-sample-frequency monthly \
  --preset current_default \
  --preset shallow_strong_regularized \
  --preset depth2_more_rounds \
  --preset depth3_less_child \
  --preset legacy_baseline \
  --preset deeper_regularized \
  --preset less_regularized
```

Two-fold tuning screen:

| Preset | Daily Mean | Daily Median | Monthly Median | Quarterly Median | Median Best Iteration |
| --- | ---: | ---: | ---: | ---: | ---: |
| current_default | +38.35% | +34.95% | +36.36% | +36.36% | 78.0 |
| depth2_more_rounds | +39.45% | +34.78% | +37.41% | +36.02% | 59.5 |
| depth3_less_child | +35.83% | +33.26% | +32.25% | +32.25% | 61.0 |
| deeper_regularized | +36.24% | +33.20% | +34.01% | +32.02% | 125.5 |
| shallow_strong_regularized | +34.19% | +32.03% | +33.78% | +30.96% | 64.5 |
| legacy_baseline | +33.83% | +32.06% | +29.84% | +24.35% | 42.0 |

Four-fold confirmation:

| Preset | Daily Mean | Daily Median | Monthly Median | Quarterly Median | Top-Bottom Spread | Median Best Iteration |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| depth2_more_rounds | +30.63% | +24.05% | +22.35% | +26.66% | +32.75% | 27.5 |
| current_default | +27.77% | +23.07% | +24.59% | +25.26% | +30.41% | 31.5 |

The selected tuned preset is not a clean sweep. It improved daily mean return, hit rate, and quarterly median return in the four-fold confirmation run, while the inherited default kept a slightly better monthly median and quarterly spread. The selection favors the tuned preset because it is simpler, less deep, lower-learning-rate, and still improved the broader confirmation profile.

Selected production export:

```bash
.venv-model/bin/python scripts/modeling/export_production_rank_model.py \
  --dataset long_horizon_training_dataset_15y.csv.gz \
  --output-dir models/long-horizon \
  --model-name xgboost_rank_sector252_15y_monthly_tuned_research \
  --target-column relevance_grade_sector_neutral_252d \
  --return-column sector_neutral_forward_return_252d_after_cost \
  --num-boost-round 60 \
  --train-sample-frequency monthly \
  --eta 0.025 \
  --max-depth 2 \
  --min-child-weight 80 \
  --subsample 0.9 \
  --colsample-bytree 0.85 \
  --lambda-reg 4 \
  --alpha 0
```

The tuned preset won, but this should still be read as research-grade. The strongest SHAP features are illiquidity and volatility, so the model is not a pure long-momentum engine.

## SHAP Explainability

SHAP-style contribution values were generated against the long-horizon research model's holdout predictions.

Command:

```bash
.venv-model/bin/python scripts/modeling/explain_model.py \
  --model-name xgboost_rank_sector252_15y_monthly_tuned_research \
  --model-path models/long-horizon/xgboost_rank_sector252_15y_monthly_tuned_research.json \
  --dataset long_horizon_training_dataset_15y.csv.gz \
  --predictions xgboost_rank_sector252_15y_monthly_tuned_walk_forward_test_predictions.csv \
  --top-n 50
```

Output files:

```text
data/modeling/reports/xgboost_rank_sector252_15y_monthly_tuned_research_shap_summary.csv
data/modeling/reports/xgboost_rank_sector252_15y_monthly_tuned_research_shap_summary.json
models/long-horizon/xgboost_rank_sector252_15y_monthly_tuned_research_explainability.json
```

Top SHAP features:

| Feature | What It Means | Mean Abs SHAP | Mean SHAP | Positive Share |
| --- | --- | ---: | ---: | ---: |
| `amihud_20d_pct_rank` | 20-day illiquidity rank across the universe | 0.33340 | -0.27929 | 13.5% |
| `volatility_60d_pct_rank` | 60-day volatility rank across the universe | 0.04822 | -0.02007 | 18.2% |
| `volatility_60d_minus_sector_median` | stock volatility versus sector median | 0.03308 | -0.01247 | 17.7% |
| `amihud_20d_sector_pct_rank` | 20-day illiquidity rank versus sector peers | 0.03264 | -0.02668 | 21.3% |
| `volatility_60d_vs_sector_etf` | stock 60-day volatility versus sector ETF | 0.02476 | -0.01866 | 9.4% |
| `rel_volatility_60d_vs_spy` | stock 60-day volatility versus SPY | 0.01556 | -0.01224 | 0.0% |
| `volatility_60d` | raw 60-day volatility | 0.00893 | -0.00657 | 23.4% |
| `downside_volatility_60d_pct_rank` | downside volatility rank across the universe | 0.00778 | -0.00260 | 39.0% |
| `rel_volatility_60d_vs_spy_pct_rank` | volatility versus SPY rank | 0.00723 | -0.00304 | 33.7% |
| `downside_volatility_60d_minus_sector_median` | downside volatility versus sector median | 0.00120 | -0.00030 | 22.9% |

Plain-English read:

- The tuned 252-day model is currently more of a liquidity and volatility regime sorter than a pure momentum model.
- `amihud_20d_pct_rank` dominates the explanation. The model is very sensitive to where a stock sits in the cross-sectional illiquidity stack.
- Volatility still matters, especially universe-relative, sector-relative, and SPY-relative 60-day volatility.
- Classic long momentum is not a dominant SHAP driver in the tuned artifact, which is why the dashboard should keep showing baseline-agreement labels rather than blindly presenting every top model name as a classic momentum candidate.
- Mean SHAP direction should not be read as a standalone rule. These are tree-model margin contributions and can change sign depending on the interaction path.

## Baseline Comparison

The long-horizon model should not be judged only against zero. It should beat simple strategies that would be easy to implement without machine learning. The baseline comparison script evaluates the XGBoost ranker on the same holdout rows as the simple rules below:

- Low volatility: favor lower 60-day realized volatility.
- Liquidity plus low risk: favor higher dollar volume, lower illiquidity, lower volatility, and lower volatility versus SPY.
- 12-1 month momentum: favor trailing 12-month momentum excluding the most recent month.
- Sector-relative momentum: favor 12-1 month momentum versus sector peers and the sector ETF.
- Risk-adjusted momentum: blend 12-1 month momentum, 60-day volatility-adjusted return, and lower volatility.
- Technical composite: use the existing rules-style technical composite score.
- Sector momentum: favor sectors with stronger 12-1 month sector ETF momentum.

Command:

```bash
.venv-model/bin/python scripts/modeling/evaluate_rank_baselines.py \
  --dataset long_horizon_training_dataset_15y.csv.gz \
  --predictions xgboost_rank_sector252_15y_monthly_tuned_walk_forward_test_predictions.csv \
  --output-name xgboost_rank_sector252_15y_monthly_tuned_baseline_comparison \
  --artifact-output models/long-horizon/xgboost_rank_sector252_15y_monthly_tuned_baseline_comparison.json \
  --metadata-artifact models/long-horizon/xgboost_rank_sector252_15y_monthly_tuned_research_metadata.json
```

Four-fold tuned walk-forward comparison:

| Strategy | Daily Mean | Monthly Median | Quarterly Median | Quarterly Spread Median |
| --- | ---: | ---: | ---: | ---: |
| XGBoost rank model | +30.64% | +22.05% | +25.04% | +28.47% |
| 12-1 month momentum | +18.36% | +16.12% | +15.91% | +6.00% |
| Sector-relative momentum | +18.87% | +15.06% | +10.98% | +4.66% |
| Technical composite | +10.38% | +6.39% | +6.39% | +7.15% |
| Sector momentum | +2.92% | +2.90% | +2.84% | +1.68% |
| Risk-adjusted momentum | +0.72% | +1.24% | +1.28% | +0.13% |
| Liquidity plus low risk | -1.10% | -2.14% | -1.58% | -17.44% |
| Low volatility | -1.66% | -2.23% | -1.91% | -31.94% |

Interpretation:

- The tuned XGBoost model is beating the simple baselines in this test, which gives us a reason to keep developing it rather than replacing it with a rules-only 12-1 momentum screen.
- Sector-relative momentum is the most relevant benchmark. It performs well and is simpler, so the dashboard should show whether XGBoost is agreeing with or overruling sector-relative momentum.
- Low-volatility and liquidity-only screens performed poorly in this recent regime. That matters because SHAP shows the XGBoost model uses volatility and liquidity heavily. The model is not simply buying low-volatility stocks; it is combining those risk signals with momentum and market context.
- The yearly cohort count is still thin. Monthly and quarterly medians are more useful than daily averages, but more completed periods are needed before treating the long-horizon model as production-grade.

## Dashboard Use

The model now appears in a separate `Long-Horizon Book` tab. It is not mixed into the 14-day Momentum Book.

Dashboard fields:

- Full current S&P 500 table with search, sector filter, and sortable columns.
- Company name, industry, market cap, close, trailing 60D return, and YTD return.
- XGBoost one-year score, one-year rank, and one-year percentile.
- 14-day tactical model rank/percentile and setup tags joined from the daily scorebook.
- Cross-horizon agreement label:
  - `Both Models Agree`: one-year top-decile strength and tactical momentum confirmation.
  - `1Y Strong + Tactical Momentum`: one-year top-decile strength with tactical momentum even when the tactical percentile is not top tier.
  - `1Y Strong / Tactical Watch`: one-year top-decile strength and high tactical score, but the tactical setup is still a watch/rebound setup rather than momentum-confirmed.
  - `1Y Strong / Tactical Weak`: one-year top-decile strength but weak 14-day tactical confirmation.
  - `Tactical Only`: tactical model is strong but one-year model is not.
- Compact sector and market-cap mix summaries for the current top-decile one-year book.
- Baseline comparison using quarterly and monthly median top-decile returns.
- SHAP feature summary showing the largest model drivers.
- Drawdown-adjusted label comparison so path quality stays visible without replacing the main return label.

The key product idea is to show whether the model is finding genuine long-horizon momentum, an unconfirmed rebound/watch setup, or a more defensive/risk-adjusted setup. That will keep the dashboard from presenting every high-ranked name as the same type of trade.

## Caveats

- The current universe is current S&P 500 constituents, not point-in-time S&P 500 membership.
- One-year labels overlap heavily when evaluated daily. Monthly and quarterly cohorts are more interpretable than daily averages.
- The initial model is price/technical only. One-year holding models will probably benefit from fundamentals.
- The early-stopping best iteration is low in the first research run, which suggests the signal may be simple, the validation window may be narrow, or the current hyperparameters may be too regularized.
- The current SHAP profile says the model is dominated by volatility and liquidity effects. Before using this as a true one-year capital-allocation engine, compare it against simple low-volatility, liquidity, and sector-relative momentum baselines.

## History, Universe, And Fundamentals Audits

History coverage is audited with:

```bash
.venv-model/bin/python scripts/modeling/audit_long_horizon_history.py
```

Current audit:

```text
Feature-complete symbols: 501 of 503 current constituents
Earliest feature-complete date: 2017-05-04
95% coverage start date: 2017-05-04 with the current 10-year raw cache
```

The raw cache currently starts in 2016, and the 252-day feature/label requirements push the clean labeled start date to 2017. Extending further back requires fetching a longer raw price cache first. Use fewer years with cleaner features if older data introduces stale symbols, missing sector context, or unreliable prices.

Free point-in-time S&P 500 membership is audited with:

```bash
.venv-model/bin/python scripts/modeling/build_sp500_point_in_time_universe.py
```

This parses current constituents plus the free Wikipedia changes table. Current output found 357 index-change rows and 222 current members with known start dates from the changes table. This is useful for survivorship-bias research, but it is not a complete commercial-grade point-in-time universe because many removed names do not have full start/end intervals, sector metadata, or validated price histories.

SEC fundamentals are audited with:

```bash
.venv-model/bin/python scripts/modeling/audit_sec_fundamentals.py
```

The initial smoke test matched 25 of 25 sampled S&P constituents to SEC Company Facts with zero request failures. Fundamentals should still remain audit-first until tag definitions, filing-date alignment, and refresh reliability are reviewed. If fundamentals are added to training, they must be aligned by SEC `filed` date, not fiscal period end date.

## Next Research Steps

- Fetch and audit a longer raw price cache, then decide whether the cleaner training start can move earlier than 2017.
- Keep the drawdown-adjusted label as a diagnostic until it improves actual sector-neutral returns, not just path smoothness.
- Run a broader hyperparameter search only after the label decision is stable.
- Promote SEC fundamentals only after the coverage audit proves the fields are reliable enough to refresh without daily fragility.
- Use the point-in-time universe artifact for bias analysis first; do not force incomplete removed-name data into training.
