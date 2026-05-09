# Model Monitoring

This folder is the rerunnable workbench for checking whether the production
XGBoost rank model is still behaving the way we expect.

The main check is a recent decile backtest:

1. Fetch fresh Yahoo daily price history for the current S&P 500, SPY, and
   sector ETFs.
2. Rebuild the same feature matrix used by the live scorer.
3. Score each eligible historical date with the frozen production model.
4. Sort names into deciles by model score.
5. Measure what each decile actually did over the next 14 trading days.

The output is not embedded in the dashboard. It lives here so we can review it
as a model-quality control file.

## Run

From the repo root:

```bash
.venv-model/bin/python analysis/model-monitoring/run_recent_decile_backtest.py
```

If the virtual environment is not available:

```bash
python3 -m venv .venv-model
.venv-model/bin/python -m pip install -r scripts/modeling/requirements.txt
.venv-model/bin/python analysis/model-monitoring/run_recent_decile_backtest.py
```

## Outputs

The script writes to `analysis/model-monitoring/output/`:

- `summary.json` - machine-readable monitor summary.
- `latest_summary.md` - plain-English readout.
- `latest_summary.html` - standalone browser report with charts and tables.
- `strict_post_training_decile_summary.csv` - truly out-of-sample decile table.
- `strict_post_training_daily_deciles.csv` - daily decile performance rows.
- `strict_post_training_top_bottom_daily.csv` - daily top/bottom spread rows.
- `recent_completed_decile_summary.csv` - recent diagnostic table.
- `recent_completed_daily_deciles.csv` - daily diagnostic rows.
- `recent_completed_top_bottom_daily.csv` - daily top/bottom diagnostic rows.
- `current_top_decile.csv` - latest scored top-decile names, including prior-rank comparisons.
- `recent_top_decile_entrants.csv` - names newly entering or sharply improving into the top decile.
- `shap_feature_summary.csv` - stored SHAP feature explanation values used in the HTML report.
- `*_sector_neutral_return.svg` - decile return charts.
- `*_median_sector_neutral_return.svg` - median decile return charts.
- `*_hit_rate.svg` - hit-rate charts.
- `*_top_bottom_spread.svg` - top-minus-bottom daily spread charts.
- `shap_feature_influence.svg` - feature-importance chart for the stored SHAP artifact.

## How To Read It

The strict post-training window is the cleanest check because it starts after
the production model's training end date. It will be small immediately after a
model is trained because a 14-trading-day label cannot be measured until the
future has happened.

The recent completed window is a broader drift diagnostic. It can overlap the
training period when the model was trained very recently, so it should not be
treated as a clean performance claim. It is still useful for spotting whether
the latest market regime looks abnormal versus recent model behavior.

The best sign is not one perfect top-decile number. We want to see:

- Top deciles outperform lower deciles.
- Top-minus-bottom spread is positive.
- Hit rate improves as model score rises.
- Results do not depend on one extreme date or one extreme ticker.
