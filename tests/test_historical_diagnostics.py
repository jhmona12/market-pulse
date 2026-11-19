from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis/model-audit"))
sys.path.insert(0, str(ROOT / "scripts/modeling"))
from outcome_diagnostics import no_trade_calendar, unknown_reason, disputed_price_exposure, spinoff_return
from historical_universe import forward_outcomes, normalize_bars, apply_reviewed_price_corrections
from build_training_dataset import enrich_price_features


class HistoricalDiagnosticsTests(unittest.TestCase):
    def test_halt_ranges_fill_publisher_omissions_without_masking_reopening(self):
        dates = pd.bdate_range("2023-03-09", "2023-03-29")
        listed = pd.DataFrame(columns=["ticker", "date"])
        gaps = pd.DataFrame([{"ticker": "SIVB", "last_trade": "2023-03-09", "next_trade": "2023-03-28"}])
        mask = no_trade_calendar(dates, listed, gaps, [])
        self.assertIn(pd.Timestamp("2023-03-10"), mask["SIVB"])
        self.assertNotIn(pd.Timestamp("2023-03-28"), mask["SIVB"])
        self.assertNotIn(pd.Timestamp("2023-03-09"), mask["SIVB"])

    def test_reviewed_periods_are_inclusive_and_require_source(self):
        dates = pd.bdate_range("2023-06-08", periods=4)
        period = {"security_id": "BIIB", "start": "2023-06-09", "end": "2023-06-09", "source_url": "https://issuer.example/halt"}
        result = no_trade_calendar(dates, pd.DataFrame(), pd.DataFrame(), [period])
        self.assertEqual(result["BIIB"], {pd.Timestamp("2023-06-09")})
        with self.assertRaises(ValueError):
            no_trade_calendar(dates, pd.DataFrame(), pd.DataFrame(), [{**period, "source_url": ""}])

    def test_unknown_entry_not_reclassified_as_unknown_rights(self):
        kwargs = dict(entry_date=pd.Timestamp("2020-01-06"), exit_date=pd.Timestamp("2020-02-01"),
                      last_trade=pd.Timestamp("2020-01-03"), entry_valid=False, exit_valid=False)
        self.assertEqual(unknown_reason(**kwargs, event={"verified": True, "unresolved_rights": True, "effective_date": "2020-01-06"}), "entry_after_verified_termination")
        self.assertEqual(unknown_reason(**kwargs, event={"verified": True, "effective_date": "2020-01-10"}), "missing_entry_before_event")
        self.assertEqual(unknown_reason(**kwargs), "entry_after_unreviewed_series_end")
        self.assertEqual(unknown_reason(**kwargs, no_trade_dates={kwargs["entry_date"]}), "entry_during_halt")

    def test_missing_exit_is_not_assumed_bankruptcy_loss(self):
        reason = unknown_reason(entry_date=pd.Timestamp("2020-01-02"), exit_date=pd.Timestamp("2020-02-01"),
            last_trade=pd.Timestamp("2020-01-03"), entry_valid=True, exit_valid=False)
        self.assertEqual(reason, "unreviewed_series_end")

    def test_price_exposure_separates_past_predictors_from_future_labels(self):
        dates = pd.bdate_range("2020-01-01", periods=400)
        past, future = disputed_price_exposure(dates, [dates[100]], dates, 14)
        self.assertFalse(past[90])
        self.assertTrue(future[90])
        self.assertTrue(past[352])
        self.assertFalse(past[353])
        self.assertFalse(future[101])
        self.assertTrue(past[99])

    def test_total_return_includes_distributed_shares_and_reverse_split(self):
        row = SimpleNamespace(parent_close=76.10, parent_shares=1/3, ratio=1/3, spinco_close=24.81,
                              parent_prev_close=30.52, cash=np.nan)
        self.assertAlmostEqual(spinoff_return(row), .10211882918392279)

    def test_no_forward_fill_through_halt_and_distress_outcome_preserved(self):
        dates = pd.bdate_range("2023-03-01", periods=24)
        raw = pd.DataFrame({"date": dates, "open": 100., "high": 100., "low": 100., "close": 100.,
                            "adj_close": 100., "volume": 1000., "source": "tiingo", "stock_splits": 1.})
        raw.loc[15:, ["open", "high", "low", "close", "adj_close"]] = .4
        halted = set(dates[8:15])
        clean = normalize_bars(raw, halted)
        features = enrich_price_features(clean, feature_version=2)
        self.assertTrue(features.ret_1d.iloc[8:16].isna().all())
        outcome, _, _ = forward_outcomes(raw, dates, 14, security_id="FAILED", as_of=dates[-1], no_trade_dates=halted)
        self.assertAlmostEqual(outcome.iloc[0], -.996)
        self.assertTrue(pd.isna(outcome.iloc[7]))

    def test_dividend_correction_restores_special_without_double_counting_split(self):
        dates = pd.bdate_range("2020-01-01", periods=5)
        raw = pd.DataFrame({"date": dates, "close": [25., 25., 24., 26., 27.], "adj_close": [24., 24., 23.28, 25.22, 26.19],
                            "source": "yahoo", "stock_splits": [0., 0., 0., 0., 4.], "dividends": [0., 0., .25, 0., 0.]})
        original = raw.copy(deep=True)
        review = {"date": str(dates[2].date()), "action": "correct_dividend", "cash_per_then_share": 2.,
                  "expected_archive_dividend": .25, "source_url": "https://issuer.example/special"}
        corrected = apply_reviewed_price_corrections(raw, [review])
        self.assertEqual(corrected.dividends.iloc[2], .5)
        self.assertAlmostEqual(corrected.adj_close.pct_change(fill_method=None).iloc[2], 24.5 / 25 - 1)
        pd.testing.assert_frame_equal(raw, original)
        np.testing.assert_allclose(corrected.adj_close.iloc[2:], raw.adj_close.iloc[2:])
        np.testing.assert_allclose(corrected.adj_close.pct_change(fill_method=None).iloc[3:], raw.adj_close.pct_change(fill_method=None).iloc[3:])
        with self.assertRaisesRegex(ValueError, "precondition"):
            apply_reviewed_price_corrections(corrected, [review])


if __name__ == "__main__":
    unittest.main()
