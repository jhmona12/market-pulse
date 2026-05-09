# Analysis Workbench

This folder is intentionally separate from the public dashboard. It is for model
diagnostics, backtests, charts, and research notes that help decide whether the
Market Pulse model is still performing in line with expectations.

Nothing in this folder is imported by `index.html`, `app.js`, or the GitHub
Pages dashboard unless we explicitly wire it in later.

## Current Analyses

- `model-monitoring/` - recent out-of-sample decile checks for the production
  XGBoost rank model.

