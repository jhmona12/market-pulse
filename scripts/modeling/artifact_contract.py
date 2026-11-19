"""Version/identity contracts shared by live scoring and shadow deployment tests."""
from __future__ import annotations

import hashlib
from pathlib import Path

import xgboost as xgb
import pandas as pd

from rank_validation import label_horizon, require_feature_version


def model_feature_version(metadata: dict) -> int:
    version = metadata.get("feature_version", 1)
    if isinstance(version, bool) or version not in (1, 2):
        raise ValueError(f"Unsupported feature version: {version!r}")
    return int(version)


def artifact_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_model_artifact(path: Path, metadata: dict) -> int:
    version = model_feature_version(metadata)
    booster = xgb.Booster()
    booster.load_model(path)
    require_feature_version(booster, version)
    features = metadata["feature_columns"]
    if not features or len(set(features)) != len(features) or booster.feature_names != features:
        raise ValueError("Model/metadata feature order mismatch")
    if metadata.get("feature_count") != len(features) or metadata.get("num_boost_round") != booster.num_boosted_rounds():
        raise ValueError("Model/metadata feature or tree count mismatch")
    horizon = label_horizon(metadata["target_column"], metadata["return_column"])
    if metadata.get("target_horizon_days", horizon) != horizon:
        raise ValueError("Model/metadata horizon mismatch")
    if version == 2 and not metadata.get("model_sha256"):
        raise ValueError("Version 2 deployment metadata requires a model hash")
    if metadata.get("model_sha256") and metadata["model_sha256"] != artifact_hash(path):
        raise ValueError("Model file hash differs from metadata")
    return version


def validate_reference_version(cache: dict, metadata: dict, model_path: Path, *, require_scores: bool = False) -> int:
    if cache.get("serializationVersion") != 2:
        raise ValueError("Reference cache predates full-precision serialization; rebuild the cache")
    expected = model_feature_version(metadata)
    cached_model = cache.get("model", {})
    actual = cache.get("featureVersion", cached_model.get("featureVersion", 1))
    if isinstance(actual, bool) or actual != expected or cached_model.get("featureVersion", actual) != actual:
        raise ValueError(f"Reference cache feature version {actual} does not match model version {expected}; rebuild the cache")
    as_of = pd.to_datetime(cache.get("asOfDate"), utc=True, errors="coerce")
    dates = pd.to_datetime([row.get("date") for row in cache.get("referenceRows", [])], utc=True, errors="coerce")
    if as_of is None or pd.isna(as_of) or len(dates) == 0 or dates.isna().any() or not (dates.normalize() == as_of.normalize()).all():
        raise ValueError("Reference cache row dates do not match its as-of date; refusing to relabel stale rows")
    if require_scores:
        digest = cached_model.get("artifactSha256")
        if digest:
            if digest != artifact_hash(model_path):
                raise ValueError("Cached reference scores belong to a different model artifact")
        elif expected == 2:
            raise ValueError("Version 2 cached reference scores require model identity")
        elif cached_model.get("name") != model_path.stem or cached_model.get("trainingEndDate") != metadata.get("training_end_date"):
            raise ValueError("Legacy cached reference scores do not match the requested model")
    return expected
