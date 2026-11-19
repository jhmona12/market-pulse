from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
import xgboost as xgb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/modeling"))
sys.path.insert(0, str(ROOT / "analysis/model-audit"))
from artifact_contract import artifact_hash, model_feature_version, validate_model_artifact, validate_reference_version
from score_live_rank_model import load_price_frame, score_rows
from tune_fixed_candidates import recipes, select_recipe, training_rows_as_known
from run_comparison import paired_interval
from score_live_rank_model import frame_records, cached_dashboard_metrics, dashboard_metrics_for_frame


class CandidateDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "candidate.json"
        self.frame = pd.DataFrame({"date": pd.Timestamp("2026-11-17"), "symbol": [f"S{i}" for i in range(5)], "feature": np.arange(5.)})
        matrix = xgb.DMatrix(self.frame[["feature"]], label=np.arange(5.))
        matrix.set_group([5])
        model = xgb.train({"objective": "rank:ndcg", "nthread": 1, "max_depth": 2}, matrix, num_boost_round=2)
        model.set_attr(feature_version="2")
        model.save_model(self.path)
        self.metadata = {"feature_version": 2, "feature_columns": ["feature"], "feature_count": 1, "num_boost_round": 2,
            "target_column": "relevance_grade_sector_neutral_14d", "return_column": "sector_neutral_forward_return_14d_after_cost",
            "target_horizon_days": 14, "model_sha256": artifact_hash(self.path)}
        self.cache = {"serializationVersion": 2, "featureVersion": 2, "asOfDate": "2026-11-17", "referenceRows": [{"date": "2026-11-17", "symbol": "S0"}],
            "model": {"featureVersion": 2, "artifactSha256": artifact_hash(self.path)}}

    def test_metadata_model_hash_version_names_horizon_and_rounds_are_enforced(self):
        self.assertEqual(validate_model_artifact(self.path, self.metadata), 2)
        for change in ({"feature_version": 1}, {"feature_columns": ["wrong"]}, {"model_sha256": "bad"},
                       {"num_boost_round": 3}, {"target_horizon_days": 252}, {"feature_version": 3}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_model_artifact(self.path, {**self.metadata, **change})
        for value in (True, "2", 0, 3):
            with self.assertRaises(ValueError):
                model_feature_version({"feature_version": value})

    def test_version_two_scores_only_matching_features_and_serializes_ranks(self):
        result = score_rows(self.frame, self.path, ["feature"], feature_version=2)
        self.assertEqual(result.model_rank.tolist(), [1, 2, 3, 4, 5])
        self.assertTrue(np.isfinite(result.model_score).all())
        self.assertTrue(result.model_percentile.between(0, 100).all())
        with self.assertRaisesRegex(ValueError, "version 2"):
            score_rows(self.frame, self.path, ["feature"])

    def test_cache_features_can_be_shared_but_scores_require_exact_model(self):
        self.assertEqual(validate_reference_version(self.cache, self.metadata, self.path, require_scores=True), 2)
        self.cache["model"]["artifactSha256"] = "different-model"
        self.assertEqual(validate_reference_version(self.cache, self.metadata, self.path), 2)
        with self.assertRaisesRegex(ValueError, "different model"):
            validate_reference_version(self.cache, self.metadata, self.path, require_scores=True)
        self.cache["featureVersion"] = 1
        with self.assertRaisesRegex(ValueError, "feature version"):
            validate_reference_version(self.cache, self.metadata, self.path)

    def test_cache_asof_cannot_relabel_stale_or_missing_row_dates(self):
        for value in ("2026-11-16", None, "invalid"):
            self.cache["referenceRows"][0]["date"] = value
            with self.assertRaisesRegex(ValueError, "row dates"):
                validate_reference_version(self.cache, self.metadata, self.path)

    def test_price_loader_propagates_version_to_actual_feature_engine(self):
        frame = pd.DataFrame({"date": pd.bdate_range("2026-10-01", periods=25), "open": np.arange(100., 125.),
            "high": np.arange(101., 126.), "low": np.arange(99., 124.), "close": np.arange(100., 125.), "volume": 1000.})
        with patch("score_live_rank_model.fetch_yahoo_history", return_value=frame), patch.dict("os.environ", {"EXPECTED_MARKET_DATA_DATE": "2026-11-17"}):
            _, legacy, error1 = load_price_frame("ABC", 3)
            _, revised, error2 = load_price_frame("ABC", 3, feature_version=2)
        self.assertIsNone(error1); self.assertIsNone(error2)
        self.assertEqual(legacy.rsi_14.iloc[-1], 50.)
        self.assertEqual(revised.rsi_14.iloc[-1], 100.)

    def test_recipe_selection_uses_only_common_complete_validation_baskets(self):
        rows = [dict(date=d, stage=s, excess_mean=e, stock_member_median=e, spread=e)
                for d, s, e in [("2020-01-01", "A", .1), ("2020-01-01", "B", .2),
                                ("2020-01-02", "A", 99.), ("2020-01-02", "B", np.nan)]]
        selected, metrics = select_recipe(pd.DataFrame(rows), ["A", "B"])
        self.assertEqual(selected, "B")
        self.assertEqual(metrics.loc["A", "dates"], 1)
        empty = pd.DataFrame(rows); empty["excess_mean"] = np.nan
        with self.assertRaisesRegex(ValueError, "No common"):
            select_recipe(empty, ["A", "B"])

    def test_bounded_presets_include_exact_inherited_fixed_recipe(self):
        for horizon, rounds in ((14, 25), (252, 60)):
            # Freeze the inherited recipe; changing production must not change this experiment.
            metadata = {"num_boost_round": rounds, "params": {"eta": .025, "max_depth": 2, "nthread": 4}}
            presets = recipes(metadata)
            self.assertEqual(len(presets), 6)
            self.assertEqual(len({p[0] for p in presets}), 6)
            self.assertEqual(presets[0][2], metadata["num_boost_round"])
            self.assertTrue(all(p[1]["nthread"] == 4 and p[2] <= 120 for p in presets))

    def test_training_grades_exclude_labels_not_available_at_fit_time(self):
        frame = pd.DataFrame({"date": ["2020-01-01"] * 10, "excess": np.arange(10.), "grade": [99.] * 10})
        rows = training_rows_as_known(frame, np.arange(5))
        self.assertEqual(rows.grade.iloc[-1], 4)
        self.assertEqual(rows.excess.max(), 4.)
        frame.loc[0, "excess"] = np.nan
        with self.assertRaisesRegex(ValueError, "Unknown"):
            training_rows_as_known(frame, np.arange(5))

    def test_short_monthly_sample_cannot_claim_a_bootstrap_interval(self):
        rows = [{"date": date, "stage": stage, "excess_mean": value}
                for date in pd.date_range("2025-01-01", periods=6, freq="MS")
                for stage, value in (("corrected_features", .10), ("corrected_evaluation", .12))]
        result = paired_interval(pd.DataFrame(rows), 252)
        self.assertEqual(result["status"], "insufficient_blocks")
        self.assertIsNone(result["approximate_95pct_block_interval"])
        self.assertAlmostEqual(result["mean_difference"], -.02)

    def test_feature_cache_preserves_tiny_liquidity_values(self):
        value = 1.234567891234567e-12
        rows = frame_records(pd.DataFrame({"amihud_20d": [value], "missing": [np.nan]}))
        self.assertEqual(rows[0]["amihud_20d"], value)
        self.assertIsNone(rows[0]["missing"])
        self.assertEqual(json.loads(json.dumps(rows, allow_nan=False))[0]["amihud_20d"], value)

    def test_low_precision_caches_cannot_be_reused_after_upgrade(self):
        del self.cache["serializationVersion"]
        with self.assertRaisesRegex(ValueError, "precision"):
            validate_reference_version(self.cache, self.metadata, self.path)

    def test_stop_metrics_are_preserved_for_names_promoted_by_second_model(self):
        metrics = {"ABC": {"return60": 12.3, "stopSellSignal": {"stopSellPrice": 95.0}}}
        self.assertEqual(cached_dashboard_metrics({"dashboardMetrics": metrics}), metrics)

    def test_ytd_uses_prior_year_last_close_not_first_january_close(self):
        frame = pd.DataFrame({"date": pd.to_datetime(["2025-12-31", "2026-01-02", "2026-01-05"]),
            "close": [100., 110., 121.], "high": [101., 111., 122.], "low": [99., 109., 120.], "volume": [1000., 1000., 1000.]})
        self.assertEqual(dashboard_metrics_for_frame("ABC", frame)["ytdReturn"], 21.)
        self.assertIsNone(dashboard_metrics_for_frame("IPO", frame.iloc[1:])["ytdReturn"])


if __name__ == "__main__":
    unittest.main()
