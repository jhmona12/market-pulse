from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd
import xgboost as xgb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/modeling"))

from build_long_horizon_dataset import add_forward_path_labels
from build_training_dataset import enrich_price_features, validate_price_calendar
from rank_validation import best_iteration_model, label_horizon, resolve_embargo, strict_monitoring_start, validate_label_metadata, validate_training_frame
from schema import dataset_metadata_path, feature_columns_for
from train_rank_model import make_dmatrix, sample_dates, split_dates
from walk_forward_rank_model import fold_slices, train_fold


class RankModelContractTests(unittest.TestCase):
    def frame(self):
        rows = []
        for day in pd.bdate_range("2020-01-01", periods=8):
            for index in range(5):
                rows.append({"date": day, "symbol": f"S{index}", "feature": float(index), "grade": index})
        return pd.DataFrame(rows)

    def test_horizons_cannot_be_mixed(self):
        self.assertEqual(label_horizon("relevance_grade_sector_neutral_252d", "sector_neutral_forward_return_252d_after_cost"), 252)
        with self.assertRaises(ValueError):
            label_horizon("relevance_grade_sector_neutral_14d", "sector_neutral_forward_return_252d_after_cost")

    def test_strict_monitoring_starts_after_export_not_last_training_signal(self):
        metadata = {"training_end_date": "2026-04-10", "generated_at": "2026-05-04T10:00:00Z"}
        self.assertEqual(strict_monitoring_start(metadata), pd.Timestamp("2026-05-05"))
        self.assertEqual(strict_monitoring_start(metadata, "2026-06-01"), pd.Timestamp("2026-06-01"))
        with self.assertRaisesRegex(ValueError, "cannot start"):
            strict_monitoring_start(metadata, "2026-04-11")
        with self.assertRaisesRegex(ValueError, "export"):
            strict_monitoring_start({"training_end_date": "2026-04-10"})

    def test_next_close_delay_is_included_in_embargo(self):
        for horizon in (14, 252):
            self.assertEqual(resolve_embargo(None, horizon), horizon + 1)
            with self.assertRaisesRegex(ValueError, "entry|Next-close"):
                resolve_embargo(horizon, horizon)

    def test_holdout_label_exit_precedes_next_block_for_both_horizons(self):
        dates = pd.bdate_range("2010-01-01", periods=1600)
        frame = pd.DataFrame({"date": dates})
        for horizon in (14, 252):
            train, validation, test, metadata = split_dates(frame, 100, 100, None, horizon)
            self.assertLess(dates.get_loc(train[-1]) + horizon + 1, dates.get_loc(validation[0]))
            self.assertLess(dates.get_loc(validation[-1]) + horizon + 1, dates.get_loc(test[0]))
            self.assertEqual(metadata["embargo_days"], horizon + 1)

    def test_walk_forward_auto_purge_matches_label_and_samples_after_split(self):
        dates = pd.bdate_range("2010-01-01", periods=2600)
        args = argparse.Namespace(target_column="relevance_grade_sector_neutral_252d", return_column="sector_neutral_forward_return_252d_after_cost",
            embargo_days=None, folds=2, test_days=252, validation_days=252, min_train_days=756)
        folds = fold_slices(dates, args)
        self.assertEqual(len(folds), 2)
        self.assertEqual(args.embargo_days, 253)
        for fold in folds:
            self.assertLess(dates.get_loc(fold["train_dates"][-1]) + 253, dates.get_loc(fold["validation_dates"][0]))
            self.assertLess(dates.get_loc(fold["validation_dates"][-1]) + 253, dates.get_loc(fold["test_dates"][0]))
        sampled = sample_dates(folds[0]["train_dates"], "monthly")
        self.assertEqual(len(sampled), len(sampled.to_period("M").unique()))

    def test_leaky_long_horizon_default_is_rejected(self):
        args = argparse.Namespace(target_column="relevance_grade_sector_neutral_252d", return_column="sector_neutral_forward_return_252d_after_cost",
            embargo_days=14, folds=1, test_days=252, validation_days=252, min_train_days=500)
        with self.assertRaisesRegex(ValueError, "253"):
            fold_slices(pd.bdate_range("2010-01-01", periods=2600), args)

    def test_training_rejects_identity_leakage_nulls_duplicates_and_bad_grades(self):
        frame = self.frame()
        for features in (["symbol"], ["close"], ["grade"], ["feature", "feature"]):
            with self.assertRaises(ValueError):
                validate_training_frame(frame, features, "grade")
        for value in (np.nan, np.inf):
            broken = frame.copy(); broken.loc[0, "feature"] = value
            with self.assertRaisesRegex(ValueError, "finite"):
                make_dmatrix(broken, ["feature"], "grade")
        with self.assertRaisesRegex(ValueError, "unique"):
            make_dmatrix(pd.concat([frame, frame.iloc[:1]]), ["feature"], "grade")
        frame["grade"] = frame["grade"].astype(float)
        frame.loc[0, "grade"] = 4.5
        with self.assertRaisesRegex(ValueError, "grades"):
            make_dmatrix(frame, ["feature"], "grade")

    def test_dmatrix_groups_match_sorted_dates(self):
        matrix = make_dmatrix(self.frame().sample(frac=1, random_state=4), ["feature"], "grade")
        np.testing.assert_array_equal(matrix.get_uint_info("group_ptr"), np.arange(0, 41, 5))

    def test_best_tree_selection_prediction_shap_and_serialization_agree(self):
        frame = self.frame()
        matrix = make_dmatrix(frame, ["feature"], "grade")
        booster = xgb.train({"objective": "rank:ndcg", "max_depth": 2, "nthread": 1}, matrix, num_boost_round=8)
        booster.set_attr(best_iteration="2", best_score="0.75")
        expected = booster.predict(matrix, iteration_range=(0, 3))
        selected = best_iteration_model(booster)
        self.assertEqual(selected.num_boosted_rounds(), 3)
        self.assertEqual(selected.best_iteration, 2)
        np.testing.assert_allclose(selected.predict(matrix), expected)
        np.testing.assert_allclose(selected.predict(matrix, pred_contribs=True).sum(axis=1), expected, atol=1e-6)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "model.json"
            selected.save_model(path)
            loaded = xgb.Booster(); loaded.load_model(path)
            np.testing.assert_allclose(loaded.predict(matrix), expected)

    def test_fixed_round_production_models_are_not_trimmed(self):
        for path in (ROOT / "models/rank/xgboost_rank_sector14_tuned.json", ROOT / "models/long-horizon/xgboost_rank_sector252_15y_monthly_tuned_research.json"):
            booster = xgb.Booster(); booster.load_model(path)
            self.assertIsNone(booster.attr("best_iteration"))
            self.assertIs(best_iteration_model(booster), booster)

    def test_train_fold_returns_only_selected_rounds(self):
        train = self.frame()
        validation = train.copy(); validation["grade"] = 4 - validation["grade"]
        booster = train_fold(train, validation, ["feature"], target_column="grade",
            params={"objective": "rank:ndcg", "eval_metric": "ndcg", "max_depth": 2, "nthread": 1}, num_boost_round=20, early_stopping_rounds=3)
        self.assertEqual(booster.num_boosted_rounds(), booster.best_iteration + 1)

    def test_long_horizon_drawdown_measures_peak_to_trough_not_entry_loss(self):
        frame = pd.DataFrame({"close": [90, 100, 150, 120]})
        labeled = add_forward_path_labels(frame, 2)
        self.assertAlmostEqual(labeled.loc[0, "forward_return_2d_next_close"], 0.20)
        self.assertAlmostEqual(labeled.loc[0, "max_drawdown_2d_next_close"], -0.20)
        self.assertTrue(pd.isna(labeled.loc[1, "max_drawdown_2d_next_close"]))

    def test_labels_use_next_close_entry_and_exact_holding_horizon(self):
        frame = pd.DataFrame({"close": np.arange(100., 365.), "volume": 1000.})
        tactical = enrich_price_features(frame.copy())
        strategic = add_forward_path_labels(frame, 252)
        self.assertAlmostEqual(tactical.loc[0, "forward_return_14d_next_close"], 115. / 101. - 1)
        self.assertAlmostEqual(strategic.loc[0, "forward_return_252d_next_close"], 353. / 101. - 1)
        self.assertTrue(pd.isna(strategic.iloc[-253]["forward_return_252d_next_close"]))

    def test_incomplete_forward_paths_cannot_create_drawdown_labels(self):
        frame = pd.DataFrame({"close": [90, 100, np.nan, 120]})
        self.assertTrue(pd.isna(add_forward_path_labels(frame, 2).loc[0, "max_drawdown_2d_next_close"]))

    def test_feature_metadata_is_scoped_to_each_dataset(self):
        for filename in ("custom.csv.gz", "custom.csv", "custom"):
            self.assertEqual(dataset_metadata_path(Path(filename)), Path("custom_metadata.json"))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "training_dataset_metadata.json").write_text(json.dumps({"feature_columns": ["short_feature"]}))
            (path / "long_horizon_training_dataset_15y_metadata.json").write_text(json.dumps({"feature_columns": ["long_feature"]}))
            self.assertEqual(feature_columns_for("training_dataset.csv.gz", path), ["short_feature"])
            self.assertEqual(feature_columns_for("long_horizon_training_dataset_15y.csv.gz", path), ["long_feature"])

    def test_old_drawdown_labels_require_explicit_rebuild(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "long.csv.gz"
            metadata = path.with_name("long_metadata.json")
            validate_label_metadata(path, "relevance_grade_sector_neutral_252d")
            with self.assertRaisesRegex(ValueError, "Rebuild"):
                validate_label_metadata(path, "relevance_grade_drawdown_adjusted_252d")
            metadata.write_text(json.dumps({"label_version": 1}))
            with self.assertRaisesRegex(ValueError, "Rebuild"):
                validate_label_metadata(path, "relevance_grade_drawdown_adjusted_252d")
            metadata.write_text(json.dumps({"label_version": 2}))
            validate_label_metadata(path, "relevance_grade_drawdown_adjusted_252d")

    def test_training_calendar_keeps_short_histories_but_rejects_missing_sessions(self):
        dates = pd.bdate_range("2026-01-01", periods=6)
        frame = pd.DataFrame({"date": dates[2:], "close": [10., 11., 12., 13.]})
        validate_price_calendar(frame, "IPO", dates)
        with self.assertRaisesRegex(ValueError, "missing"):
            validate_price_calendar(frame.drop(index=1), "GAP", dates)
        with self.assertRaisesRegex(ValueError, "refresh"):
            validate_price_calendar(frame.iloc[:-1], "STALE", dates)
        with self.assertRaisesRegex(ValueError, "unique"):
            validate_price_calendar(pd.concat([frame, frame.iloc[:1]]), "DUP", dates)
        for value in (0., -1., np.nan, np.inf):
            broken = frame.copy()
            broken.loc[0, "close"] = value
            with self.assertRaisesRegex(ValueError, "invalid"):
                validate_price_calendar(broken, "INVALID", dates)


if __name__ == "__main__":
    unittest.main()
