from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "modeling"))

from score_live_rank_model import (  # noqa: E402
    MarketDataNotReadyError,
    expected_session_readiness,
    scoring_rows_for_date,
    wait_for_expected_session_frames,
)


def frame(*dates: str) -> pd.DataFrame:
    return pd.DataFrame({"date": pd.to_datetime(list(dates)), "close": range(1, len(dates) + 1)})


class SessionReadinessTests(unittest.TestCase):
    def test_context_and_stocks_are_retried_together(self) -> None:
        frames = {"AAA": frame("2026-09-09"), "BBB": frame("2026-09-08"), "SPY": frame("2026-09-08")}
        with patch.dict("os.environ", {"MODEL_DATA_READINESS_ATTEMPTS": "2", "MODEL_DATA_READINESS_DELAY_SECONDS": "0"}), \
             patch("score_live_rank_model.fetch_symbol_frames", return_value=({"BBB": frame("2026-09-09"), "SPY": frame("2026-09-09")}, {})) as fetch:
            _, _, readiness = wait_for_expected_session_frames(frames, {}, {"AAA", "BBB"}, ["SPY"], date(2026, 9, 9), 3, 4)
        self.assertTrue(readiness["ready"])
        self.assertEqual(set(fetch.call_args.args[0]), {"BBB", "SPY"})

    def test_stale_stock_is_retried_even_above_minimum_coverage(self) -> None:
        names = {f"S{i}" for i in range(10)}
        frames = {symbol: frame("2026-09-09") for symbol in names | {"SPY"}}
        frames["S0"] = frame("2026-09-08")
        with patch.dict("os.environ", {"MODEL_DATA_READINESS_ATTEMPTS": "2", "MODEL_DATA_READINESS_DELAY_SECONDS": "0"}), \
             patch("score_live_rank_model.fetch_symbol_frames", return_value=({"S0": frame("2026-09-09")}, {})) as fetch:
            _, _, readiness = wait_for_expected_session_frames(frames, {}, names, ["SPY"], date(2026, 9, 9), 3, 4)
        fetch.assert_called_once()
        self.assertEqual(readiness["coverage"], 1.0)

    def test_exhausted_stale_feed_fails_without_using_prior_session(self) -> None:
        frames = {symbol: frame("2026-09-08") for symbol in ("AAA", "SPY")}
        with patch.dict("os.environ", {"MODEL_DATA_READINESS_ATTEMPTS": "2", "MODEL_DATA_READINESS_DELAY_SECONDS": "0"}), \
             patch("score_live_rank_model.fetch_symbol_frames", return_value=(frames, {})):
            with self.assertRaisesRegex(MarketDataNotReadyError, "2026-09-09.*Readiness checks exhausted"):
                wait_for_expected_session_frames(frames, {}, {"AAA"}, ["SPY"], date(2026, 9, 9), 3, 4)

    def test_duplicate_rows_cannot_satisfy_universe_coverage(self) -> None:
        dataset = pd.DataFrame([{"date": "2026-09-09", "symbol": "AAA", "feature": 1.0}] * 2)
        with self.assertRaisesRegex(MarketDataNotReadyError, "duplicate"):
            scoring_rows_for_date(dataset, ["feature"], date(2026, 9, 9), {"AAA", "BBB"}, 0.90)

    def test_infinite_feature_values_are_not_scorable(self) -> None:
        dataset = pd.DataFrame([{"date": "2026-09-09", "symbol": "AAA", "feature": float("inf")}])
        with self.assertRaisesRegex(MarketDataNotReadyError, "incomplete"):
            scoring_rows_for_date(dataset, ["feature"], date(2026, 9, 9), {"AAA"}, 0.90)

    def test_required_context_must_match_expected_session(self) -> None:
        expected = date(2026, 9, 8)
        frames = {
            "AAA": frame("2026-09-05", "2026-09-08"),
            "BBB": frame("2026-09-05", "2026-09-08"),
            "SPY": frame("2026-09-05"),
            "XLK": frame("2026-09-05", "2026-09-08"),
        }

        readiness = expected_session_readiness(frames, {"AAA", "BBB"}, ["SPY", "XLK"], expected, 0.90)

        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["coverage"], 1.0)
        self.assertEqual(readiness["missingContext"], ["SPY"])

    def test_reference_coverage_must_clear_threshold(self) -> None:
        expected = date(2026, 9, 8)
        frames = {
            "AAA": frame("2026-09-08"),
            "BBB": frame("2026-09-05"),
            "SPY": frame("2026-09-08"),
        }

        readiness = expected_session_readiness(frames, {"AAA", "BBB"}, ["SPY"], expected, 0.90)

        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["readyReferenceCount"], 1)
        self.assertEqual(readiness["staleReference"], ["BBB"])

    def test_partial_latest_date_never_falls_back_silently(self) -> None:
        dataset = pd.DataFrame(
            [
                {"date": "2026-09-05", "symbol": "AAA", "feature_a": 1.0, "feature_b": 2.0},
                {"date": "2026-09-05", "symbol": "BBB", "feature_a": 1.5, "feature_b": 2.5},
                {"date": "2026-09-08", "symbol": "AAA", "feature_a": 1.2, "feature_b": None},
                {"date": "2026-09-08", "symbol": "BBB", "feature_a": 1.7, "feature_b": None},
            ]
        )

        with self.assertRaisesRegex(MarketDataNotReadyError, "Feature cross-section is incomplete for 2026-09-08"):
            scoring_rows_for_date(
                dataset,
                ["feature_a", "feature_b"],
                scoring_date=date(2026, 9, 8),
                reference_symbols={"AAA", "BBB"},
                minimum_coverage=0.90,
            )

    def test_complete_expected_date_is_selected(self) -> None:
        dataset = pd.DataFrame(
            [
                {"date": "2026-09-08", "symbol": "AAA", "feature_a": 1.2, "feature_b": 2.2},
                {"date": "2026-09-08", "symbol": "BBB", "feature_a": 1.7, "feature_b": 2.7},
            ]
        )

        rows = scoring_rows_for_date(
            dataset,
            ["feature_a", "feature_b"],
            scoring_date=date(2026, 9, 8),
            reference_symbols={"AAA", "BBB"},
            minimum_coverage=0.90,
        )

        self.assertEqual(rows["symbol"].tolist(), ["AAA", "BBB"])


if __name__ == "__main__":
    unittest.main()
