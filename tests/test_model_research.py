from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from datetime import date
from dataclasses import replace

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/modeling"))
sys.path.insert(0, str(ROOT / "analysis/model-audit"))
from build_training_dataset import enrich_price_features, rsi
from build_sp500_point_in_time_universe import parse_changes, fetch_changes
from rank_validation import require_feature_version
from prepare_experiment import cache_history
from render_comparison import comparison, effect_conclusion, finite_json, table
from run_comparison import cohort_metrics, paired_interval
from security_lifecycle import MembershipInterval, TerminalEvent, resolve_terminal_outcome


class ResearchContractTests(unittest.TestCase):
    def terminal(self, **overrides):
        event = TerminalEvent("SECURITY-OLD-1", "cash_acquisition", date(2020, 5, 4), date(2020, 5, 4), 110., "https://issuer.example/verified-event", True)
        args = dict(security_id="SECURITY-OLD-1", total_return_to_last=.2, last_raw_close=100., last_trade_date=date(2020, 5, 1), exit_date=date(2020, 5, 20), as_of=date(2020, 5, 21), event=event)
        args.update(overrides)
        return resolve_terminal_outcome(**args)

    def test_removal_changes_new_entry_eligibility_not_past_records(self):
        interval = MembershipInterval("SECURITY-OLD-1", date(2010, 1, 1), date(2020, 5, 4), "https://index.example/notice", True)
        self.assertTrue(interval.eligible(date(2020, 5, 1)))
        self.assertFalse(interval.eligible(date(2020, 5, 4)))
        with self.assertRaises(ValueError):
            replace(interval, verified=False).eligible(date(2020, 5, 1))

    def test_cash_and_stock_consideration_compounds_with_prior_return(self):
        self.assertAlmostEqual(self.terminal()["return"], .32)
        event = TerminalEvent("SECURITY-OLD-1", "stock_acquisition", date(2020, 5, 4), date(2020, 5, 4), 2 * 60., "https://issuer.example/terms", True)
        self.assertAlmostEqual(self.terminal(event=event)["return"], .44)
        self.assertEqual(self.terminal(as_of=date(2020, 5, 5))["status"], "not_mature")

    def test_bankruptcy_filing_not_zero_and_new_equity_not_same_security(self):
        filing = TerminalEvent("SECURITY-OLD-1", "bankruptcy_filing", date(2020, 5, 4), date(2020, 5, 4), 0., "https://court.example/filing", True)
        self.assertEqual(self.terminal(event=filing)["status"], "unresolved")
        self.assertEqual(self.terminal(event=replace(filing, kind="worthless_cancellation"))["return"], -1.)
        self.assertEqual(self.terminal(event=None)["status"], "unresolved")
        with self.assertRaises(ValueError):
            self.terminal(event=replace(filing, security_id="NEW-EQUITY"))
        with self.assertRaises(ValueError):
            self.terminal(event=replace(filing, included_in_adjusted_history=True))

    def test_terminal_accounting_rejects_wrong_units_and_future_evidence(self):
        event = TerminalEvent("SECURITY-OLD-1", "mixed_acquisition", date(2020, 5, 4), date(2020, 6, 1), 110., "https://issuer.example/terms", True)
        self.assertEqual(self.terminal(event=event)["status"], "not_mature")
        self.assertEqual(self.terminal(event=replace(event, verified=False))["status"], "unresolved")
        with self.assertRaises(ValueError):
            self.terminal(event=replace(event, price_basis="adjusted_close"))
        with self.assertRaises(ValueError):
            self.terminal(event=replace(event, kind="worthless_cancellation"))

    def test_versioned_rsi_edges_and_warmup(self):
        up = pd.Series(np.arange(1., 31.))
        self.assertEqual(rsi(up).iloc[-1], 50.)
        self.assertEqual(rsi(up, feature_version=2).iloc[-1], 100.)
        self.assertEqual(rsi(-up, feature_version=2).iloc[-1], 0.)
        self.assertEqual(rsi(up * 0 + 10, feature_version=2).iloc[-1], 50.)
        self.assertTrue(rsi(up, feature_version=2).iloc[:14].isna().all())
        with self.assertRaises(ValueError):
            rsi(up, feature_version=3)

    def test_missing_volume_not_imputed_in_v2(self):
        frame = pd.DataFrame({"close": np.arange(1., 41.), "volume": 100.})
        frame.loc[20, "volume"] = np.nan
        old = enrich_price_features(frame.copy())
        new = enrich_price_features(frame.copy(), feature_version=2)
        self.assertTrue(np.isfinite(old.loc[21, "volume_ratio_20"]))
        self.assertTrue(pd.isna(new.loc[21, "volume_ratio_20"]))
        pd.testing.assert_series_equal(old.forward_return_14d_next_close, new.forward_return_14d_next_close)

    def test_live_scorer_cannot_silently_use_v1_for_v2_model(self):
        booster = Mock()
        booster.attr.return_value = None
        require_feature_version(booster, 1)
        booster.attr.return_value = "2"
        with self.assertRaisesRegex(ValueError, "requires feature version 2"):
            require_feature_version(booster, 1)
        require_feature_version(booster, 2)

    def test_membership_parser_preserves_one_sided_changes(self):
        html = '<table id="changes"><tr><td>May 1, 2026</td><td></td><td></td><td>OLD</td><td>Old Co</td><td>Removed</td></tr><tr><td>May 2, 2026</td><td>NEW</td><td>New Co</td><td></td><td></td><td>Spin-off</td></tr></table>'
        changes = parse_changes(html)
        self.assertEqual(changes.iloc[0].removed_symbol, "OLD")
        self.assertEqual(changes.iloc[0].added_symbol, "")
        with patch("build_sp500_point_in_time_universe.fetch_text", side_effect=["<html/>", html]):
            found, url, _ = fetch_changes()
        self.assertEqual(len(found), 2)
        self.assertIn("Historical_components", url)
        with patch("build_sp500_point_in_time_universe.fetch_text", return_value="<html/>"):
            with self.assertRaises(ValueError):
                fetch_changes()
        with patch("build_sp500_point_in_time_universe.fetch_text", side_effect=[OSError("unavailable"), html]):
            self.assertEqual(len(fetch_changes()[0]), 2)

    def test_frozen_cache_is_not_silently_changed_on_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            path = folder / "ABC.csv"
            path.write_text("date,close\n2026-01-02,100\n")
            expected = {"status": "fetched", "rows": 1, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            with patch("prepare_experiment.fetch_yahoo_history") as fetch:
                self.assertEqual(cache_history("ABC", folder, None, None, "2026-01-02", expected)[1], expected)
                path.write_text("date,close\n2026-01-02,101\n")
                with self.assertRaisesRegex(ValueError, "hash changed"):
                    cache_history("ABC", folder, None, None, "2026-01-02", expected)
                path.unlink()
                with self.assertRaisesRegex(ValueError, "file missing"):
                    cache_history("ABC", folder, None, None, "2026-01-02", expected)
                fetch.assert_not_called()

    def test_report_does_not_claim_improvement_without_complete_cohorts(self):
        effect = {"mean_difference": 0., "approximate_95pct_block_interval": [0., 0.]}
        self.assertIn("No measured", effect_conclusion(effect))
        self.assertIsNone(finite_json({"spread": float("nan")})["spread"])
        self.assertNotIn("<script>", table(["Name"], [["<script>alert(1)</script>"]]))
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            target = folder / "comparison_14d"
            target.mkdir()
            (target / "results.json").write_text(json.dumps({"feature_correction_effect": {"status": "no_common_cohorts"}}))
            pd.DataFrame([{"date": "2026-01-02", "stage": s, "excess_mean": np.nan} for s in ("corrected_evaluation", "corrected_features")]).to_csv(target / "cohorts.csv", index=False)
            html, summary = comparison(folder, 14)
            self.assertIn("No common complete baskets", html)
            self.assertEqual(summary["status"], "no_common_cohorts")

    def test_cohort_metrics_and_paired_interval(self):
        frame = pd.DataFrame({"date": pd.Timestamp("2026-01-01"), "symbol": [f"S{i}" for i in range(20)], "outcome": np.arange(20)/100, "excess": np.arange(20)/100-.05})
        result = cohort_metrics(frame, np.arange(20), "corrected_features", 1)[0]
        self.assertAlmostEqual(result["stock_mean"], .185)
        self.assertAlmostEqual(result["excess_mean"], .135)
        self.assertAlmostEqual(result["spread"], .18)
        records = []
        for date in pd.bdate_range("2026-01-01", periods=40):
            for stage, value in (("corrected_features", .03), ("corrected_evaluation", .02)):
                records.append({"date": date, "stage": stage, "excess_mean": value})
        interval = paired_interval(pd.DataFrame(records), 14)
        self.assertAlmostEqual(interval["mean_difference"], .01)
        np.testing.assert_allclose(interval["approximate_95pct_block_interval"], [.01, .01])

    def test_rank_before_filtering_unknown_terminal_returns(self):
        frame = pd.DataFrame({"date": pd.Timestamp("2026-01-01"), "symbol": [f"S{i}" for i in range(20)], "outcome": .1, "excess": .05})
        frame.loc[19, ["outcome", "excess"]] = np.nan
        result = cohort_metrics(frame, np.arange(20), "corrected_features", 1)[0]
        self.assertEqual(result["universe"], 20)
        self.assertIn("S19", result["top_symbols"])
        self.assertEqual(result["unresolved_top"], 1)
        self.assertTrue(np.isnan(result["excess_mean"]))


if __name__ == "__main__":
    unittest.main()
