from __future__ import annotations

import json
from pathlib import Path

from common import FEATURES_DIR


DEFAULT_DATASET_NAME = "training_dataset.csv.gz"
TARGET_HORIZON = 14

LEGACY_TARGET_COLUMN = "label_outperform_spy_14d"
RANK_TARGET_COLUMN = "relevance_grade_sector_neutral_14d"
META_TARGET_COLUMN = "meta_label_momentum_success"
SECTOR_POSITIVE_TARGET_COLUMN = "label_sector_neutral_positive_14d"
SECTOR_HURDLE_TARGET_COLUMN = "label_sector_neutral_hurdle_14d"
RANK_RETURN_COLUMN = "sector_neutral_forward_return_14d_after_cost"

IDENTITY_COLUMNS = {
    "date",
    "symbol",
    "sector",
    "close",
}

TARGET_COLUMNS = {
    "forward_return_14d",
    "spy_forward_return_14d",
    "excess_return_14d",
    "forward_return_14d_next_close",
    "sector_forward_return_14d_next_close",
    "sector_neutral_forward_return_14d",
    RANK_RETURN_COLUMN,
    "max_drawdown_14d_next_close",
    "sector_max_drawdown_14d_next_close",
    LEGACY_TARGET_COLUMN,
    SECTOR_POSITIVE_TARGET_COLUMN,
    SECTOR_HURDLE_TARGET_COLUMN,
    "sector_neutral_forward_return_pct_rank",
    RANK_TARGET_COLUMN,
    "candidate_momentum_setup",
    META_TARGET_COLUMN,
}

NON_FEATURE_COLUMNS = IDENTITY_COLUMNS | TARGET_COLUMNS

FEATURE_GROUP_PATTERNS = {
    "volatility_risk": (
        "volatility",
        "downside_volatility",
        "beta_60d",
        "max_daily_return_20d",
    ),
    "liquidity": (
        "volume",
        "dollar_volume",
        "amihud",
    ),
    "long_momentum": (
        "ret_120d",
        "momentum_126d",
        "momentum_252d",
    ),
    "sector_relative": (
        "_sector_pct_rank",
        "_minus_sector_median",
        "_vs_sector_etf",
        "sector_ret_",
        "sector_momentum_",
        "sector_volatility_",
        "sector_price_",
        "sector_rsi_",
        "price_vs_sector",
        "rsi_vs_sector",
    ),
    "market_context": (
        "spy_",
        "breadth_",
        "_vs_spy",
    ),
}


def is_macro_feature(column: str) -> bool:
    return column.endswith("_level") or column.endswith("_chg_1") or column.endswith("_chg_5")


def feature_matches_group(column: str, group: str) -> bool:
    patterns = FEATURE_GROUP_PATTERNS[group]
    return any(pattern in column for pattern in patterns)


def drop_feature_groups(feature_columns: list[str], groups: list[str]) -> tuple[list[str], list[str]]:
    unknown_groups = sorted(set(groups) - set(FEATURE_GROUP_PATTERNS))
    if unknown_groups:
        raise ValueError(f"Unknown feature groups: {unknown_groups}")

    dropped = [
        column
        for column in feature_columns
        if any(feature_matches_group(column, group) for group in groups)
    ]
    dropped_set = set(dropped)
    kept = [column for column in feature_columns if column not in dropped_set]
    return kept, dropped


def feature_columns_for(dataset_name: str, features_dir: Path = FEATURES_DIR) -> list[str]:
    metadata_path = features_dir / "training_dataset_metadata.json"
    if dataset_name == DEFAULT_DATASET_NAME and metadata_path.exists():
        with metadata_path.open(encoding="utf-8") as handle:
            metadata = json.load(handle)
        if metadata.get("feature_columns"):
            return list(metadata["feature_columns"])

    import pandas as pd

    frame = pd.read_csv(features_dir / dataset_name, compression="gzip", nrows=0)
    return [column for column in frame.columns if column not in NON_FEATURE_COLUMNS]
