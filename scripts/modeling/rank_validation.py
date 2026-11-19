"""Shared evaluation contracts for the two native-XGBoost rank models."""
from __future__ import annotations

import json
from pathlib import Path
import re

import numpy as np
import pandas as pd

from schema import LONG_DRAWDOWN_ADJUSTED_TARGET_COLUMN, NON_FEATURE_COLUMNS, dataset_metadata_path


def validate_label_metadata(dataset_path: Path, target_column: str) -> None:
    if target_column != LONG_DRAWDOWN_ADJUSTED_TARGET_COLUMN:
        return
    metadata_path = dataset_metadata_path(dataset_path)
    metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
    if metadata.get("label_version", 0) < 2:
        raise ValueError("Rebuild the long-horizon dataset with label_version >= 2 before using peak-to-trough drawdown labels.")


def strict_monitoring_start(metadata: dict, requested_start: str | None = None) -> pd.Timestamp:
    fitted = pd.to_datetime(metadata.get("generated_at"), utc=True, errors="coerce")
    training_end = pd.to_datetime(metadata.get("training_end_date"), utc=True, errors="coerce")
    if pd.isna(fitted) or pd.isna(training_end):
        raise ValueError("Strict monitoring requires saved model export and training dates.")
    minimum = max(fitted, training_end).normalize().tz_localize(None) + pd.Timedelta(days=1)
    if requested_start is None:
        return minimum
    requested = pd.to_datetime(requested_start, utc=True, errors="coerce")
    if pd.isna(requested) or requested.tz_localize(None) < minimum:
        raise ValueError(f"Strict monitoring cannot start before {minimum.date()}, after the model was exported.")
    return requested.tz_localize(None)


def label_horizon(target_column: str, return_column: str) -> int:
    horizons = []
    for column in (target_column, return_column):
        match = re.search(r"_(\d+)d(?:_|$)", column)
        if not match:
            raise ValueError(f"Cannot infer label horizon from {column!r}")
        horizons.append(int(match.group(1)))
    if horizons[0] != horizons[1] or horizons[0] not in (14, 252):
        raise ValueError("Target and economic-return columns must use the same supported horizon (14 or 252).")
    return horizons[0]


def resolve_embargo(embargo_days: int | None, horizon_days: int) -> int:
    # Signal at t, entry at t+1, exit at t+h+1. The exit must precede
    # the next block's first signal date, not coincide with that date.
    minimum = horizon_days + 1
    if embargo_days is None:
        return minimum
    if embargo_days < minimum:
        raise ValueError(f"Next-close {horizon_days}D labels require at least {minimum} excluded trading dates; got {embargo_days}.")
    return embargo_days


def best_iteration_model(booster):
    """Trim stopped boosters once so prediction, SHAP and saved trees agree."""
    best = booster.attr("best_iteration")
    if best is None:
        return booster
    rounds = int(best) + 1
    if not 1 <= rounds <= booster.num_boosted_rounds():
        raise ValueError("Invalid saved best_iteration; refusing to select trees.")
    if rounds == booster.num_boosted_rounds():
        return booster
    selected = booster[:rounds]
    selected.set_attr(**booster.attributes())
    return selected


def require_feature_version(booster, actual_version: int) -> None:
    expected = int(booster.attr("feature_version") or 1)
    if expected != actual_version:
        raise ValueError(f"Model requires feature version {expected}; received version {actual_version}. Rebuild matching features before scoring.")


def validate_training_frame(frame: pd.DataFrame, features: list[str], target: str) -> None:
    forbidden = set(features) & NON_FEATURE_COLUMNS
    if forbidden or target in features:
        raise ValueError(f"Identity/target leakage in feature list: {sorted(forbidden | ({target} & set(features)))}")
    if not features or len(features) != len(set(features)):
        raise ValueError("Model features must be nonempty and unique.")
    if frame.empty or frame[["date", "symbol"]].isna().any().any() or frame.duplicated(["date", "symbol"]).any():
        raise ValueError("Training rows must have unique, non-null symbol/date keys.")
    values = frame[features].to_numpy(dtype=float)
    labels = frame[target].to_numpy(dtype=float)
    if not np.isfinite(values).all() or not np.isfinite(labels).all():
        raise ValueError("Training features and labels must be finite; rebuild/clean the dataset instead of implicit imputation.")
    if not np.isin(labels, np.arange(5)).all():
        raise ValueError("Rank labels must be integer relevance grades 0 through 4.")
