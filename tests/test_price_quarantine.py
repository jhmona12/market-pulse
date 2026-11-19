from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis/model-audit"))
sys.path.insert(0, str(ROOT / "scripts/modeling"))
from price_quarantine import unresolved_flags, quarantine_bars, quarantine_exposure
from run_comparison import cohort_metrics, paired_interval
from tune_fixed_candidates import training_rows_as_known


class PriceQuarantineTests(unittest.TestCase):
    def test_only_unresolved_flags_and_no_duplicate_evidence(self):
        ledger = pd.DataFrame({"label": ["A", "B"], "date": ["2020-01-02"] * 2,
                               "status": ["unresolved_vendor_disagreement", "primary_sourced_dividend_corrected"]})
        self.assertEqual(set(unresolved_flags(ledger)), {"A"})
        with self.assertRaises(ValueError):
            unresolved_flags(pd.concat([ledger, ledger]))

    def test_raw_bars_are_immutable_only_two_sessions_masked(self):
        dates = pd.bdate_range("2020-01-01", periods=8)
        bars = pd.DataFrame({"date": dates, "close": np.arange(8.) + 100, "volume": 500.})
        clean = quarantine_bars(bars, [dates[3]], dates)
        self.assertEqual(clean.index[clean.close.isna()].tolist(), [2, 3])
        self.assertTrue(bars.close.notna().all())
        self.assertEqual(clean.close.iloc[4], bars.close.iloc[4])

    def test_future_unknown_cannot_remove_earlier_candidate(self):
        dates = pd.bdate_range("2020-01-01", periods=400)
        past, future = quarantine_exposure(dates, [dates[100]], 14)
        self.assertFalse(past[90])
        self.assertTrue(future[90])
        self.assertFalse(past[98])
        self.assertTrue(past[99])
        self.assertTrue(past[352])
        self.assertFalse(past[353])
        rows = pd.DataFrame({"date": [dates[90]] * 10, "symbol": list("ABCDEFGHIJ"),
                             "outcome": [np.nan] + [.1] * 9, "excess": [np.nan] + [.1] * 9})
        result = cohort_metrics(rows, np.arange(10, 0, -1), "test", 1)[0]
        self.assertEqual(result["top_symbols"], "A")
        self.assertEqual(result["unresolved_top"], 1)
        self.assertTrue(np.isnan(result["excess_mean"]))

    def test_no_unknown_training_grade_or_implicit_zero_loss(self):
        rows = pd.DataFrame({"date": [pd.Timestamp("2020-01-01")] * 3, "excess": [-.99, .05, np.nan]})
        with self.assertRaises(ValueError):
            training_rows_as_known(rows, [0, 1, 2])
        selected = training_rows_as_known(rows, [0, 1])
        self.assertEqual(selected.excess.iloc[0], -.99)

    def test_missing_calendar_is_not_compressed_for_confidence_interval(self):
        dates = pd.bdate_range("2020-01-01", periods=40)
        rows = pd.DataFrame({"date": list(dates) * 2, "stage": ["a"] * 40 + ["b"] * 40,
                             "excess_mean": [.1] * 80})
        rows.loc[10, "excess_mean"] = np.nan
        effect = paired_interval(rows, 14, stage="a", control="b")
        self.assertEqual(effect["status"], "incomplete_calendar")
        self.assertIsNone(effect["approximate_95pct_block_interval"])


if __name__ == "__main__":
    unittest.main()
