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


def is_macro_feature(column: str) -> bool:
    return column.endswith("_level") or column.endswith("_chg_1") or column.endswith("_chg_5")


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
