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
models/long-horizon/xgboost_rank_sector252_research.json
models/long-horizon/xgboost_rank_sector252_research_metadata.json
models/long-horizon/xgboost_rank_sector252_research_explainability.json
models/long-horizon/xgboost_rank_sector252_baseline_comparison.json
models/long-horizon/xgboost_rank_sector252_walk_forward_baseline_comparison.json
```

The `data/modeling/` directory is ignored by git. Local research datasets and model artifacts can be large and should not be confused with committed dashboard artifacts.

The `models/long-horizon/` directory stores the small exported 252-day research artifact. The dashboard does not read this directory yet.

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

## Hyperparameter Tuning

The first tuning pass was intentionally compact. The 252-day label creates highly overlapping daily outcomes, so the right first step was to compare a small number of defensible presets using two one-year walk-forward test folds, a one-year validation window, and a one-year embargo between train, validation, and test.

Command:

```bash
.venv-model/bin/python scripts/modeling/tune_rank_model.py \
  --dataset long_horizon_training_dataset.csv.gz \
  --output-name xgboost_rank_sector252_tuning \
  --target-column relevance_grade_sector_neutral_252d \
  --return-column sector_neutral_forward_return_252d_after_cost \
  --folds 2 \
  --test-days 252 \
  --validation-days 252 \
  --embargo-days 252 \
  --min-train-days 700
```

Tuning summary:

| Preset | Top-Decile Return | Hit Rate | Top-Bottom Spread | Median Best Iteration |
| --- | ---: | ---: | ---: | ---: |
| current_default | +38.44% | 58.6% | +40.79% | 9.0 |
| deeper_regularized | +34.13% | 56.4% | +39.37% | 7.5 |
| less_regularized | +33.69% | 57.7% | +38.87% | 2.5 |
| legacy_baseline | +32.19% | 57.9% | +37.34% | 46.5 |
| lower_eta | +31.78% | 57.2% | +36.81% | 2.5 |

The current/default parameter set won this compact pass, but the low best-iteration count means the model is still shallow. That is useful for discipline, but it also explains why live scores can cluster in repeated bands.

## SHAP Explainability

SHAP-style contribution values were generated against the long-horizon research model's holdout predictions.

Command:

```bash
.venv-model/bin/python scripts/modeling/explain_model.py \
  --model-name xgboost_rank_sector252_research \
  --dataset long_horizon_training_dataset.csv.gz \
  --predictions xgboost_rank_sector252_research_test_predictions.csv \
  --top-n 50
```

Output files:

```text
data/modeling/reports/xgboost_rank_sector252_research_shap_summary.csv
data/modeling/reports/xgboost_rank_sector252_research_shap_summary.json
models/long-horizon/xgboost_rank_sector252_research_explainability.json
```

Top SHAP features:

| Feature | What It Means | Mean Abs SHAP | Mean SHAP | Positive Share |
| --- | --- | ---: | ---: | ---: |
| `volatility_60d_pct_rank` | 60-day volatility rank across the universe | 0.21301 | -0.16120 | 15.6% |
| `amihud_20d_pct_rank` | 20-day illiquidity rank across the universe | 0.05180 | -0.03345 | 9.0% |
| `log_dollar_volume_20d_pct_rank` | 20-day dollar-volume rank across the universe | 0.03486 | -0.03095 | 0.9% |
| `amihud_20d` | 20-day illiquidity estimate | 0.03401 | -0.03097 | 2.1% |
| `rel_volatility_60d_vs_spy_pct_rank` | volatility versus SPY rank | 0.03310 | -0.02467 | 15.6% |
| `breadth_volatility_60d_median` | market-wide median 60-day volatility | 0.01898 | +0.00754 | 93.6% |
| `volatility_60d_minus_sector_median` | stock volatility versus sector median | 0.01778 | -0.01312 | 14.3% |
| `sector_momentum_252d_skip_20` | sector 12-month momentum excluding the latest month | 0.01370 | -0.01051 | 42.8% |
| `momentum_252d_skip_20_pct_rank` | stock 12-month momentum rank excluding the latest month | 0.01021 | -0.00873 | 0.8% |
| `distance_to_52w_low_minus_sector_median` | distance from 52-week low versus sector median | 0.00915 | -0.00649 | 5.9% |

Plain-English read:

- The 252-day model is currently more of a risk, liquidity, and volatility regime sorter than a pure momentum model.
- `volatility_60d_pct_rank` dominates the explanation. It has roughly four times the mean absolute contribution of the next feature, so the model is very sensitive to where a stock sits in the cross-sectional volatility stack.
- Liquidity and tradability matter. Both Amihud illiquidity and dollar-volume rank show up near the top.
- Classic long momentum is present, but it is secondary. The model uses 12-month skip-month momentum after it has already made large splits on volatility/liquidity context.
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
  --predictions xgboost_rank_sector252_walk_forward_test_predictions.csv \
  --output-name xgboost_rank_sector252_walk_forward_baseline_comparison \
  --artifact-output models/long-horizon/xgboost_rank_sector252_walk_forward_baseline_comparison.json
```

Two-fold walk-forward comparison:

| Strategy | Daily Mean | Monthly Median | Quarterly Median | Quarterly Spread Median |
| --- | ---: | ---: | ---: | ---: |
| XGBoost rank model | +38.94% | +35.84% | +34.56% | +35.91% |
| Sector-relative momentum | +34.00% | +27.92% | +29.10% | +25.68% |
| 12-1 month momentum | +31.96% | +27.71% | +27.71% | +22.07% |
| Technical composite | +17.00% | +19.42% | +21.25% | +12.58% |
| Sector momentum | +3.00% | +2.94% | +4.46% | -0.19% |
| Risk-adjusted momentum | +2.22% | +1.61% | +1.24% | +9.70% |
| Liquidity plus low risk | -1.64% | -2.91% | -3.64% | -25.11% |
| Low volatility | -3.35% | -4.59% | -5.49% | -45.29% |

Latest single-holdout comparison:

| Strategy | Daily Mean | Monthly Median | Quarterly Median | Quarterly Spread Median |
| --- | ---: | ---: | ---: | ---: |
| XGBoost rank model | +46.96% | +45.55% | +46.85% | +47.94% |
| Sector-relative momentum | +31.48% | +26.19% | +24.92% | +29.05% |
| 12-1 month momentum | +30.82% | +25.87% | +22.60% | +24.60% |
| Technical composite | +11.82% | +13.61% | +19.77% | +18.07% |

Interpretation:

- The XGBoost model is beating the simple baselines in this test, which gives us a reason to keep developing it rather than replacing it with a rules-only 12-1 momentum screen.
- Sector-relative momentum is the most relevant benchmark. It performs well and is simpler, so the dashboard should show whether XGBoost is agreeing with or overruling sector-relative momentum.
- Low-volatility and liquidity-only screens performed poorly in this recent regime. That matters because SHAP shows the XGBoost model uses volatility and liquidity heavily. The model is not simply buying low-volatility stocks; it is combining those risk signals with momentum and market context.
- The yearly cohort count is still thin. Monthly and quarterly medians are more useful than daily averages, but more completed periods are needed before treating the long-horizon model as production-grade.

## Dashboard Use

When this model is eventually surfaced in the larger dashboard, it should be a separate long-horizon research view, not mixed into the 14-day Momentum Book.

Recommended dashboard fields:

- XGBoost long-horizon score and percentile.
- Sector-relative momentum percentile.
- 12-1 month momentum percentile.
- Volatility/liquidity SHAP explanation tag.
- Baseline agreement label:
  - `Consensus Long-Horizon Candidate`: XGBoost top decile and sector-relative momentum above the 70th percentile.
  - `Model Candidate With Classic Momentum`: XGBoost top decile and 12-1 month momentum above the 70th percentile, but sector-relative momentum below the 70th percentile.
  - `Model-Only Candidate`: XGBoost top decile but sector-relative momentum below the 70th percentile.
  - `Rules Candidate`: sector-relative momentum top decile but XGBoost outside the top decile.

The key product idea is to show whether the model is finding genuine long-horizon momentum or a more defensive/risk-adjusted setup. That will keep the dashboard from presenting every high-ranked name as the same type of trade.

## Caveats

- The current universe is current S&P 500 constituents, not point-in-time S&P 500 membership.
- One-year labels overlap heavily when evaluated daily. Monthly and quarterly cohorts are more interpretable than daily averages.
- The initial model is price/technical only. One-year holding models will probably benefit from fundamentals.
- The early-stopping best iteration is low in the first research run, which suggests the signal may be simple, the validation window may be narrow, or the current hyperparameters may be too regularized.
- The current SHAP profile says the model is dominated by volatility and liquidity effects. Before using this as a true one-year capital-allocation engine, compare it against simple low-volatility, liquidity, and sector-relative momentum baselines.

## Next Research Steps

- Add a richer dashboard-ready long-horizon artifact that combines XGBoost score, baseline agreement, and SHAP feature tags.
- Run a broader long-horizon tuning sweep after the baseline comparisons are in place.
- Add point-in-time constituent history if the model becomes decision-critical.
- Add fundamentals from free sources before considering dashboard integration.
