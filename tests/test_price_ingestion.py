from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "modeling"))

from common import fetch_yahoo_history
from score_live_rank_model import MarketDataNotReadyError, fetch_symbol_frames, load_price_frame, wait_for_expected_session_frames


def chart(stamp="2026-09-10T13:30:00+00:00", adjusted=100, exchange="America/New_York"):
    return {"chart": {"error": None, "result": [{
        "meta": {"exchangeTimezoneName": exchange},
        "timestamp": [int(datetime.fromisoformat(stamp).timestamp())],
        "indicators": {
            "quote": [{"open": [198], "high": [204], "low": [196], "close": [200], "volume": [1000]}],
            "adjclose": [{"adjclose": [adjusted]}],
        },
    }]}}


class PriceIngestionTests(unittest.TestCase):
    def fetch(self, payload, **kwargs):
        with patch("common.fetch_text", return_value=json.dumps(payload)):
            return fetch_yahoo_history("SPY", datetime(2023, 9, 1, tzinfo=timezone.utc),
                                       datetime(2026, 9, 11, tzinfo=timezone.utc), **kwargs)

    def test_adjusted_ohlc_stays_on_one_price_basis(self):
        row = self.fetch(chart()).iloc[0]
        self.assertEqual([row[k] for k in ("open", "high", "low", "close", "volume")],
                         [99, 102, 98, 100, 1000])

    def test_evening_timestamp_uses_exchange_day_not_utc_day(self):
        frame = self.fetch(chart(stamp="2026-09-11T00:01:00+00:00"))
        self.assertEqual(frame.iloc[0]["date"], "2026-09-10")

    def test_asian_exchange_open_uses_its_local_day(self):
        frame = self.fetch(chart(stamp="2026-09-09T23:30:00+00:00", exchange="Australia/Sydney"))
        self.assertEqual(frame.iloc[0]["date"], "2026-09-10")

    def test_missing_adjusted_price_is_not_replaced_with_raw_price(self):
        frame = self.fetch(chart(adjusted=None))
        self.assertTrue(frame.empty)
        diagnostic = frame.attrs["priceHistory"]
        self.assertEqual(diagnostic["tail"][0]["close"], 200)
        self.assertIsNone(diagnostic["tail"][0]["adjustedClose"])
        self.assertEqual(diagnostic["tail"][0]["rejectedReason"], "missing_or_invalid_adjusted_close")

    def test_nonfinite_adjusted_price_is_rejected(self):
        self.assertTrue(self.fetch(chart(adjusted=float("inf"))).empty)

    def test_absent_adjustment_series_is_not_silently_treated_as_total_return(self):
        payload = chart()
        del payload["chart"]["result"][0]["indicators"]["adjclose"]
        self.assertTrue(self.fetch(payload).empty)

    def test_missing_exchange_timezone_fails_explicitly(self):
        payload = chart()
        payload["chart"]["result"][0]["meta"] = {}
        with self.assertRaisesRegex(ValueError, "exchangeTimezoneName"):
            self.fetch(payload)

    def test_winter_exchange_offset_is_resolved_from_iana_timezone(self):
        frame = self.fetch(chart(stamp="2026-01-16T04:30:00+00:00"))
        self.assertEqual(frame.iloc[0]["date"], "2026-01-15")

    def test_final_daily_update_replaces_bar_without_double_counting_volume(self):
        payload = chart()
        result = payload["chart"]["result"][0]
        result["timestamp"].append(int(datetime.fromisoformat("2026-09-11T00:01:00+00:00").timestamp()))
        for values in result["indicators"]["quote"][0].values():
            values.append(values[0])
        result["indicators"]["quote"][0]["volume"][-1] = 1100
        result["indicators"]["adjclose"][0]["adjclose"].append(101)
        frame = self.fetch(payload)
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.iloc[0]["volume"], 1100)
        self.assertEqual(frame.iloc[0]["close"], 101)
        self.assertEqual(frame.attrs["priceHistory"]["consolidatedDailyRows"], 1)

    def test_multiple_duplicate_sessions_are_not_silently_repaired(self):
        payload = chart()
        result = payload["chart"]["result"][0]
        result["timestamp"] *= 3
        for values in result["indicators"]["quote"][0].values():
            values *= 3
        result["indicators"]["adjclose"][0]["adjclose"] *= 3
        with self.assertRaisesRegex(ValueError, "duplicate sessions"):
            self.fetch(payload)

    def test_alternate_endpoint_is_explicit_and_host_is_restricted(self):
        with patch("common.fetch_text", return_value=json.dumps(chart())) as fetch:
            fetch_yahoo_history("BRK.B", datetime(2023, 9, 1, tzinfo=timezone.utc),
                                datetime(2026, 9, 11, tzinfo=timezone.utc), host="query2.finance.yahoo.com")
        self.assertIn("https://query2.finance.yahoo.com/v8/finance/chart/BRK-B?", fetch.call_args.args[0])
        self.assertIn("includePrePost=false", fetch.call_args.args[0])
        with self.assertRaises(ValueError):
            self.fetch(chart(), host="example.com")

    def test_live_loader_preserves_exchange_date_through_eod_filter(self):
        with patch("common.fetch_text", return_value=json.dumps(chart(stamp="2026-09-11T00:01:00+00:00"))), \
             patch("score_live_rank_model.latest_expected_market_data_date", return_value=date(2026, 9, 10)), \
             patch("score_live_rank_model.time.sleep"):
            _, frame, error = load_price_frame("SPY", 3)
        self.assertIsNone(error)
        self.assertEqual(frame.iloc[-1]["date"].date(), date(2026, 9, 10))

    def test_provider_error_is_identified_instead_of_index_error(self):
        with self.assertRaisesRegex(ValueError, "Not Found"):
            self.fetch({"chart": {"result": None, "error": {"code": "Not Found"}}})

    def test_transport_to_readiness_uses_alternate_without_splicing_prices(self):
        stale = chart(stamp="2026-09-09T13:30:00+00:00")
        fresh = chart(adjusted=101)
        requested = []

        def fetch(url):
            requested.append(url)
            return json.dumps(fresh if "query2.finance" in url else stale)

        with tempfile.TemporaryDirectory() as directory, \
             patch("common.fetch_text", side_effect=fetch), \
             patch("score_live_rank_model.latest_expected_market_data_date", return_value=date(2026, 9, 10)), \
             patch.dict("os.environ", {"MODEL_DATA_READINESS_ATTEMPTS": "2", "MODEL_DATA_READINESS_DELAY_SECONDS": "0"}):
            frames, failures = fetch_symbol_frames(["AAA", "SPY"], 3, 1)
            frames, _, ready = wait_for_expected_session_frames(
                frames, failures, {"AAA"}, ["SPY"], date(2026, 9, 10), 3, 1, diagnostic_dir=Path(directory))
            records = [json.loads(path.read_text()) for path in Path(directory).glob("*.json")]
        self.assertTrue(ready["ready"])
        self.assertEqual(frames["AAA"]["close"].tolist(), [101])
        self.assertEqual(len(requested), 4)
        self.assertEqual(sum("query2.finance" in url for url in requested), 2)
        self.assertEqual(len(records), 2)
        failed = next(record for record in records if not record["readiness"]["ready"])
        self.assertEqual(failed["symbols"][0]["response"]["tail"][0]["sessionDate"], "2026-09-09")

    def test_both_endpoints_stale_still_fail_and_save_final_evidence(self):
        stale = json.dumps(chart(stamp="2026-09-09T13:30:00+00:00"))
        with tempfile.TemporaryDirectory() as directory, \
             patch("common.fetch_text", return_value=stale), \
             patch("score_live_rank_model.latest_expected_market_data_date", return_value=date(2026, 9, 10)), \
             patch.dict("os.environ", {"MODEL_DATA_READINESS_ATTEMPTS": "2", "MODEL_DATA_READINESS_DELAY_SECONDS": "0"}):
            frames, failures = fetch_symbol_frames(["AAA", "SPY"], 3, 1)
            with self.assertRaises(MarketDataNotReadyError):
                wait_for_expected_session_frames(frames, failures, {"AAA"}, ["SPY"], date(2026, 9, 10),
                                                 3, 1, diagnostic_dir=Path(directory))
            records = [json.loads(path.read_text()) for path in Path(directory).glob("*.json")]
        self.assertEqual(len(records), 2)
        self.assertTrue(all(not record["readiness"]["ready"] for record in records))


if __name__ == "__main__":
    unittest.main()
