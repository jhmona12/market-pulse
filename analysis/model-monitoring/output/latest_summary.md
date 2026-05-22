# Model Monitoring Summary

Generated: 2026-05-13T00:28:25.804766+00:00
Model: `models/rank/xgboost_rank_sector14_tuned.json`
Training window: `2017-05-04` to `2026-04-10`
Label: `sector_neutral_forward_return_14d_after_cost` over 14 trading days
Latest fetched SPY price date: `2026-05-12`
Fetched symbols: `520` / requested `520`

## Windows

### Strict Post-Training

This is the cleanest live-style check because it excludes dates used for training. It may be small until more 14-trading-day outcomes complete.

- Date range: `2026-04-13` to `2026-04-21`
- Scoring dates: `7`
- Rows: `3514`
- Top decile average sector-neutral 14D return after cost: 5.50%
- Top decile average daily median sector-neutral 14D return after cost: 0.91%
- Top-minus-bottom average sector-neutral spread: 6.92%
- Top-minus-bottom average daily median sector-neutral spread: 1.90%
- Top decile hit rate vs SPY: 50.14%
- Decile return Spearman rank correlation: -0.212

Top-level decile table:

| model decile | scoring dates | avg sector neutral return 14d after cost | avg daily median sector neutral return 14d after cost | avg excess vs spy 14d | avg daily median excess vs spy 14d | hit rate sector neutral after cost | hit rate vs spy |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 10 | 7 | 0.0550 | 0.0091 | 0.0581 | -0.0000 | 0.5294 | 0.5014 |
| 9 | 7 | -0.0388 | -0.0421 | -0.0370 | -0.0575 | 0.2857 | 0.3029 |
| 8 | 7 | -0.0538 | -0.0602 | -0.0571 | -0.0656 | 0.3029 | 0.3171 |
| 7 | 7 | -0.0242 | -0.0294 | -0.0367 | -0.0433 | 0.3657 | 0.3714 |
| 6 | 7 | 0.0021 | -0.0078 | -0.0186 | -0.0279 | 0.4771 | 0.3771 |
| 5 | 7 | -0.0204 | -0.0222 | -0.0542 | -0.0599 | 0.3286 | 0.1886 |
| 4 | 7 | -0.0181 | -0.0141 | -0.0515 | -0.0461 | 0.4029 | 0.1886 |
| 3 | 7 | -0.0053 | -0.0067 | -0.0366 | -0.0466 | 0.4257 | 0.1771 |
| 2 | 7 | -0.0099 | -0.0093 | -0.0465 | -0.0477 | 0.3800 | 0.1743 |
| 1 | 7 | -0.0142 | -0.0099 | -0.0512 | -0.0512 | 0.3838 | 0.1709 |

### Recent Completed

This window overlaps the training period because the production model was trained very recently. Use it as a drift diagnostic, not as a clean performance claim.

- Date range: `2026-01-26` to `2026-04-21`
- Scoring dates: `60`
- Rows: `30105`
- Top decile average sector-neutral 14D return after cost: 3.34%
- Top decile average daily median sector-neutral 14D return after cost: 2.03%
- Top-minus-bottom average sector-neutral spread: 3.94%
- Top-minus-bottom average daily median sector-neutral spread: 2.40%
- Top decile hit rate vs SPY: 56.39%
- Decile return Spearman rank correlation: -0.103

Top-level decile table:

| model decile | scoring dates | avg sector neutral return 14d after cost | avg daily median sector neutral return 14d after cost | avg excess vs spy 14d | avg daily median excess vs spy 14d | hit rate sector neutral after cost | hit rate vs spy |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 10 | 60 | 0.0334 | 0.0203 | 0.0353 | 0.0183 | 0.5739 | 0.5639 |
| 9 | 60 | -0.0122 | -0.0159 | -0.0084 | -0.0198 | 0.4193 | 0.4243 |
| 8 | 60 | -0.0170 | -0.0191 | -0.0160 | -0.0208 | 0.4207 | 0.4190 |
| 7 | 60 | -0.0085 | -0.0101 | -0.0120 | -0.0106 | 0.4480 | 0.4513 |
| 6 | 60 | 0.0033 | -0.0012 | 0.0028 | -0.0034 | 0.4993 | 0.4970 |
| 5 | 60 | -0.0078 | -0.0086 | -0.0194 | -0.0221 | 0.4340 | 0.3823 |
| 4 | 60 | -0.0069 | -0.0060 | -0.0170 | -0.0135 | 0.4447 | 0.3970 |
| 3 | 60 | -0.0049 | -0.0064 | -0.0123 | -0.0123 | 0.4480 | 0.4147 |
| 2 | 60 | -0.0102 | -0.0080 | -0.0205 | -0.0176 | 0.4097 | 0.3763 |
| 1 | 60 | -0.0060 | -0.0037 | -0.0149 | -0.0109 | 0.4696 | 0.4167 |

