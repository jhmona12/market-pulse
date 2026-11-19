from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/modeling"))
from historical_universe import member_mask, security_candidates, resolve_sector_identity, dated_sectors, normalize_bars, forward_outcomes, outcome_bounds


def prices(dates, close):
    return pd.DataFrame({"date": dates, "close": close, "adj_close": np.asarray(close) * .8,
                         "open": close, "high": close, "low": close, "volume": 1000., "source": "tiingo", "stock_splits": 1.})


class HistoricalUniverseTests(unittest.TestCase):
    def test_membership_is_end_exclusive_and_preserves_reentry(self):
        dates = pd.date_range("2020-01-01", periods=6)
        spans = pd.DataFrame({"start": [dates[1], dates[4]], "end": [dates[3], pd.NaT]})
        self.assertEqual(member_mask(dates, spans).tolist(), [False, True, True, False, True, True])
        spans.loc[1, "start"] = dates[2]
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            member_mask(dates, spans)

    def test_alias_resolution_never_guesses_between_recycled_securities(self):
        self.assertEqual(security_candidates("SNDK", ["SNDK-201605"], []), ["SNDK-201605"])
        self.assertEqual(security_candidates("SNDK", ["SNDK-201605", "SNDK"], []), ["SNDK", "SNDK-201605"])
        self.assertEqual(security_candidates("FB", ["META"], [("FB", "META")]), ["META"])
        self.assertEqual(security_candidates("AAPL*", ["AAPL"], []), ["AAPL"])
        self.assertEqual(security_candidates("BRKB", ["BRK-B"], []), ["BRK-B"])

    def test_isin_alias_mapping_rejects_conflicts_and_ineligible_identities(self):
        ids = {"US1": {"BKNG"}, "US2": {"OTHER"}, "US3": {"OLD", "NEW"}}
        self.assertEqual(resolve_sector_identity("PCLN", "US1", ["BKNG"], [], ids), ["BKNG"])
        self.assertEqual(resolve_sector_identity("PCLN", "US1", ["OTHER"], [], ids), [])
        self.assertEqual(resolve_sector_identity("OTHER", "US1", ["OTHER"], [], ids), [])
        self.assertEqual(resolve_sector_identity("BKNG", "US2", ["BKNG", "OTHER"], [], ids), [])
        self.assertEqual(resolve_sector_identity("X", "US3", ["OLD", "NEW"], [], ids), [])

    def test_sector_history_uses_only_prior_observations_and_expires(self):
        dates = pd.bdate_range("2020-01-01", periods=60)
        obs = pd.DataFrame({"date": [dates[2]], "sector": ["Information Technology"]})
        result = dated_sectors(dates, obs, max_age_days=10)
        self.assertTrue(result.sector.iloc[:3].isna().all())
        self.assertEqual(result.sector.iloc[3], "Information Technology")
        self.assertTrue(pd.isna(result.sector.iloc[-1]))

    def test_no_trade_carry_is_not_a_feature_price_or_valid_entry(self):
        dates = pd.bdate_range("2020-01-01", periods=20)
        raw = prices(dates, np.arange(100., 120.))
        clean = normalize_bars(raw, [dates[1]])
        self.assertTrue(pd.isna(clean.close.iloc[1]))
        outcome, _, _ = forward_outcomes(raw, dates, 14, security_id="OLD", as_of=dates[-1], no_trade_dates=[dates[1]])
        self.assertTrue(pd.isna(outcome.iloc[0]))

    def test_cash_event_finishes_original_horizon_without_synthetic_entries(self):
        dates = pd.bdate_range("2020-01-01", periods=22)
        raw = prices(dates[:5], [90., 100., 105., 110., 120.])
        event = {"verified": True, "kind": "cash_acquisition", "cash": 125., "effective_date": str(dates[5].date()),
                 "value_known_at": str(dates[5].date()), "source_url": "https://issuer.example/completion"}
        outcome, available, resolved = forward_outcomes(raw, dates, 14, security_id="OLD", as_of=dates[-1], event_record=event)
        self.assertAlmostEqual(outcome.iloc[0], .25)
        self.assertEqual(available.iloc[0], dates[15])
        self.assertTrue(resolved[0])
        self.assertTrue(pd.isna(outcome.iloc[4]))

    def test_missing_event_or_unvalued_cvr_remains_unknown(self):
        dates = pd.bdate_range("2020-01-01", periods=22)
        raw = prices(dates[:5], [90., 100., 105., 110., 120.])
        for event in (None, {"verified": True, "effective_date": str(dates[5].date()), "value_known_at": str(dates[5].date()), "unresolved_rights": True}):
            outcome, _, resolved = forward_outcomes(raw, dates, 14, security_id="OLD", as_of=dates[-1], event_record=event)
            self.assertTrue(pd.isna(outcome.iloc[0]))
            self.assertFalse(resolved.any())

    def test_mixed_event_uses_cash_plus_successor_not_a_history_splice(self):
        dates = pd.bdate_range("2020-01-01", periods=22)
        raw = prices(dates[:5], [90., 100., 105., 110., 120.])
        successor = prices(dates, [200.] * len(dates))
        event = {"verified": True, "kind": "mixed_acquisition", "cash": 25., "stock_ratio": .5,
                 "effective_date": str(dates[5].date()), "value_known_at": str(dates[16].date()), "source_url": "https://issuer.example/completion"}
        outcome, available, _ = forward_outcomes(raw, dates, 14, security_id="OLD", as_of=dates[-1], event_record=event, successor_raw=successor)
        self.assertAlmostEqual(outcome.iloc[0], .25)
        self.assertEqual(available.iloc[0], dates[16])
        outcome, _, _ = forward_outcomes(raw, dates, 14, security_id="OLD", as_of=dates[15], event_record=event, successor_raw=successor)
        self.assertTrue(pd.isna(outcome.iloc[0]))

    def test_observed_post_removal_prices_finish_the_horizon(self):
        dates = pd.bdate_range("2020-01-01", periods=22)
        raw = prices(dates, np.arange(100., 122.))
        outcome, _, _ = forward_outcomes(raw, dates, 14, security_id="REMOVED", as_of=dates[-1])
        self.assertAlmostEqual(outcome.iloc[0], 115 / 101 - 1)

    def test_nontraded_rights_bounds_do_not_become_point_labels(self):
        dates = pd.bdate_range("2020-01-01", periods=22)
        raw = prices(dates[:5], [90., 100., 105., 110., 120.])
        event = {"verified": True, "kind": "cash_acquisition", "cash": 125., "rights_max": 25., "unresolved_rights": True,
                 "effective_date": str(dates[5].date()), "value_known_at": str(dates[5].date()), "source_url": "https://issuer.example/completion"}
        outcome, _, _ = forward_outcomes(raw, dates, 14, security_id="OLD", as_of=dates[-1], event_record=event)
        low, high = outcome_bounds(raw, dates, 14, outcome=outcome, event_record=event, security_id="OLD", as_of=dates[-1])
        self.assertTrue(pd.isna(outcome.iloc[0]))
        self.assertAlmostEqual(low.iloc[0], .25)
        self.assertAlmostEqual(high.iloc[0], .50)
        self.assertTrue(pd.isna(low.iloc[4]))

    def test_stock_consideration_uses_first_trading_session_and_correct_share_units(self):
        dates = pd.bdate_range("2020-01-01", periods=22)
        raw = prices(dates[:3], [90., 100., 110.])
        successor = prices(dates, [100.] * len(dates))
        successor["source"] = "yahoo"
        successor["stock_splits"] = 0.
        successor.loc[10, "stock_splits"] = 2.
        event = {"verified": True, "kind": "stock_acquisition", "stock_ratio": .75,
                 "effective_date": "2020-01-05", "value_known_at": "2020-01-05", "source_url": "https://issuer.example/completion"}
        outcome, available, _ = forward_outcomes(raw, dates, 14, security_id="OLD", as_of=dates[-1], event_record=event, successor_raw=successor)
        self.assertAlmostEqual(outcome.iloc[0], .5)
        self.assertEqual(available.iloc[0], dates[15])

    def test_label_and_identity_columns_cannot_become_model_features(self):
        from model_features import model_feature_columns
        columns = model_feature_columns(pd.DataFrame(columns=["outcome", "outcome_lower", "excess_upper", "terminal_resolved", "symbol", "label_available_at"]))
        self.assertFalse(set(columns) & {"outcome", "outcome_lower", "excess_upper", "terminal_resolved", "symbol", "label_available_at"})

    def test_terminal_registry_has_unique_sourced_identities(self):
        events = json.loads((ROOT / "config/research-terminal-events.json").read_text())["events"]
        self.assertEqual(len(events), len({e["security_id"] for e in events}))
        self.assertTrue(all(e["source_url"].startswith("https://") and e["verified"] for e in events))


if __name__ == "__main__":
    unittest.main()
