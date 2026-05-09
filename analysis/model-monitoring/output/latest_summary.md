# Model Monitoring Summary

Generated: 2026-05-09T21:27:35.004510+00:00
Model: `models/rank/xgboost_rank_sector14_tuned.json`
Training window: `2017-05-04` to `2026-04-10`
Label: `sector_neutral_forward_return_14d_after_cost` over 14 trading days
Latest fetched SPY price date: `2026-05-08`
Fetched symbols: `520` / requested `520`

## Windows

### Strict Post-Training

This is the cleanest live-style check because it excludes dates used for training. It may be small until more 14-trading-day outcomes complete.

- Date range: `2026-04-13` to `2026-04-17`
- Scoring dates: `5`
- Rows: `2510`
- Top decile average sector-neutral 14D return after cost: 5.25%
- Top decile average daily median sector-neutral 14D return after cost: 0.76%
- Top-minus-bottom average sector-neutral spread: 6.50%
- Top-minus-bottom average daily median sector-neutral spread: 1.68%
- Top decile hit rate vs SPY: 50.59%
- Decile return Spearman rank correlation: -0.200

Top-level decile table:

| model decile | scoring dates | avg sector neutral return 14d after cost | avg daily median sector neutral return 14d after cost | avg excess vs spy 14d | avg daily median excess vs spy 14d | hit rate sector neutral after cost | hit rate vs spy |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 10 | 5 | 0.0525 | 0.0076 | 0.0556 | 0.0006 | 0.5176 | 0.5059 |
| 9 | 5 | -0.0327 | -0.0405 | -0.0325 | -0.0521 | 0.2960 | 0.3280 |
| 8 | 5 | -0.0442 | -0.0503 | -0.0478 | -0.0514 | 0.3320 | 0.3320 |
| 7 | 5 | -0.0194 | -0.0250 | -0.0310 | -0.0389 | 0.3720 | 0.3720 |
| 6 | 5 | 0.0079 | -0.0035 | -0.0125 | -0.0253 | 0.4960 | 0.3920 |
| 5 | 5 | -0.0155 | -0.0180 | -0.0476 | -0.0527 | 0.3680 | 0.2160 |
| 4 | 5 | -0.0164 | -0.0130 | -0.0496 | -0.0441 | 0.4080 | 0.2000 |
| 3 | 5 | -0.0054 | -0.0037 | -0.0371 | -0.0430 | 0.4440 | 0.1640 |
| 2 | 5 | -0.0095 | -0.0079 | -0.0455 | -0.0469 | 0.3760 | 0.1720 |
| 1 | 5 | -0.0125 | -0.0093 | -0.0493 | -0.0523 | 0.3686 | 0.1647 |

### Recent Completed

This window overlaps the training period because the production model was trained very recently. Use it as a drift diagnostic, not as a clean performance claim.

- Date range: `2026-01-22` to `2026-04-17`
- Scoring dates: `60`
- Rows: `30103`
- Top decile average sector-neutral 14D return after cost: 3.16%
- Top decile average daily median sector-neutral 14D return after cost: 1.95%
- Top-minus-bottom average sector-neutral spread: 3.70%
- Top-minus-bottom average daily median sector-neutral spread: 2.28%
- Top decile hit rate vs SPY: 56.45%
- Decile return Spearman rank correlation: 0.006

Top-level decile table:

| model decile | scoring dates | avg sector neutral return 14d after cost | avg daily median sector neutral return 14d after cost | avg excess vs spy 14d | avg daily median excess vs spy 14d | hit rate sector neutral after cost | hit rate vs spy |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 10 | 60 | 0.0316 | 0.0195 | 0.0335 | 0.0183 | 0.5716 | 0.5645 |
| 9 | 60 | -0.0096 | -0.0137 | -0.0056 | -0.0160 | 0.4290 | 0.4397 |
| 8 | 60 | -0.0155 | -0.0170 | -0.0141 | -0.0179 | 0.4273 | 0.4247 |
| 7 | 60 | -0.0071 | -0.0076 | -0.0090 | -0.0069 | 0.4560 | 0.4617 |
| 6 | 60 | 0.0039 | -0.0005 | 0.0052 | -0.0008 | 0.5030 | 0.5087 |
| 5 | 60 | -0.0073 | -0.0075 | -0.0169 | -0.0190 | 0.4437 | 0.3967 |
| 4 | 60 | -0.0059 | -0.0051 | -0.0138 | -0.0096 | 0.4497 | 0.4160 |
| 3 | 60 | -0.0048 | -0.0060 | -0.0098 | -0.0086 | 0.4523 | 0.4323 |
| 2 | 60 | -0.0100 | -0.0073 | -0.0176 | -0.0139 | 0.4143 | 0.3963 |
| 1 | 60 | -0.0054 | -0.0033 | -0.0117 | -0.0070 | 0.4732 | 0.4346 |

