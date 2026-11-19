from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import xgboost as xgb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/modeling"))
from artifact_contract import artifact_hash, validate_model_artifact
from promote_model_release import PRODUCTION, family, promote


class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.candidates = self.root / "candidates"
        self.originals = {}
        for horizon, name in PRODUCTION.items():
            target = self.root / name
            target.parent.mkdir(parents=True)
            for path in family(target):
                path.write_text("original " + path.name)
                self.originals[path] = path.read_bytes()
            source = self.candidates / f"tuning_{horizon}d/xgboost_rank_sector{horizon}_v2_candidate.json"
            source.parent.mkdir(parents=True)
            matrix = xgb.DMatrix(np.arange(10.).reshape(-1, 1), label=np.arange(10.) % 5, feature_names=["feature"])
            matrix.set_group([10])
            booster = xgb.train({"objective": "rank:ndcg", "nthread": 1}, matrix, num_boost_round=2)
            booster.set_attr(feature_version="2")
            booster.save_model(source)
            meta = dict(feature_version=2, feature_columns=["feature"], feature_count=1, num_boost_round=2,
                target_horizon_days=horizon, target_column=f"relevance_grade_sector_neutral_{horizon}d",
                return_column=f"sector_neutral_forward_return_{horizon}d_after_cost", model_sha256=artifact_hash(source),
                generated_at="2026-11-18", training_rows=10, training_symbols=10, training_start_date="2020-01-01",
                training_end_date="2025-01-01", train_sample_frequency="monthly", methodology_rationale=[])
            family(source)[1].write_text(json.dumps(meta))
            family(source)[2].write_text(json.dumps(dict(modelSha256=meta["model_sha256"], featureVersion=2,
                                                        topFeatures=[dict(feature="feature")], notes=[])))
            (source.parent / "results.json").write_text(json.dumps(dict(candidate_metadata=meta, test_start="2025-01-01",
                test_end="2025-03-01", common_complete_test_cohorts=2, test_cohorts=3)))

    def test_promotes_coherent_pair_preserves_dates_and_rollbacks(self):
        manifest = promote(self.candidates, "release-test", self.root)
        self.assertEqual(len(manifest["models"]), 2)
        for horizon, name in PRODUCTION.items():
            target = self.root / name
            meta = json.loads(family(target)[1].read_text())
            self.assertEqual(meta["status"], "production")
            self.assertEqual(meta["generated_at"], "2026-11-18")
            self.assertEqual(validate_model_artifact(target, meta), 2)
        for path, original in self.originals.items():
            self.assertEqual((self.root / "data/modeling/rollbacks/release-test" / path.relative_to(self.root)).read_bytes(), original)
        evidence = json.loads((self.root / "models/releases/release-test/strategic-evidence.json").read_text())
        self.assertEqual(evidence["baselineComparison"]["quarterly"], [])
        self.assertEqual(evidence["evaluation"]["status"], "development_only")
        with self.assertRaisesRegex(ValueError, "already exists"):
            promote(self.candidates, "release-test", self.root)

    def test_rejects_wrong_shap_before_any_production_write(self):
        path = self.candidates / "tuning_252d/xgboost_rank_sector252_v2_candidate_explainability.json"
        data = json.loads(path.read_text()); data["modelSha256"] = "wrong"
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "SHAP"):
            promote(self.candidates, "bad", self.root)
        for path, original in self.originals.items():
            self.assertEqual(path.read_bytes(), original)

    def test_write_failure_restores_both_artifact_families(self):
        with patch("promote_model_release.write_json", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                promote(self.candidates, "failed", self.root)
        for path, original in self.originals.items():
            self.assertEqual(path.read_bytes(), original)

    def test_deployed_models_and_explanations_have_consistent_identity(self):
        for name in PRODUCTION.values():
            path = ROOT / name
            meta = json.loads(family(path)[1].read_text())
            version = validate_model_artifact(path, meta)
            if version == 2:
                self.assertEqual(json.loads(family(path)[2].read_text())["modelSha256"], artifact_hash(path))


if __name__ == "__main__":
    unittest.main()
