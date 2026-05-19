from __future__ import annotations

import numpy as np
import pandas as pd

from schema import RANK_RETURN_COLUMN


RANK_BASE_COLUMNS = [
    "ret_5d_reversal",
    "ret_20d",
    "ret_60d",
    "ret_120d",
    "momentum_126d_skip_10",
    "momentum_252d_skip_20",
    "ret_20d_vol_adj",
    "ret_60d_vol_adj",
    "rsi_14",
    "volatility_20d",
    "volatility_60d",
    "downside_volatility_20d",
    "downside_volatility_60d",
    "max_daily_return_20d",
    "price_vs_sma_50",
    "price_vs_sma_200",
    "distance_to_52w_high",
    "distance_to_52w_low",
    "volume_ratio_20",
    "volume_ratio_60",
    "log_dollar_volume_20d",
    "amihud_20d",
    "beta_60d",
    "rel_ret_20d_vs_spy",
    "rel_ret_60d_vs_spy",
    "rel_ret_120d_vs_spy",
    "rel_rsi_vs_spy",
    "rel_volatility_20d_vs_spy",
    "rel_volatility_60d_vs_spy",
    "idiosyncratic_ret_20d",
    "idiosyncratic_ret_60d",
]

SECTOR_MEDIAN_COLUMNS = [
    "ret_20d",
    "ret_60d",
    "ret_120d",
    "momentum_126d_skip_10",
    "momentum_252d_skip_20",
    "ret_20d_vol_adj",
    "ret_60d_vol_adj",
    "rsi_14",
    "volatility_20d",
    "volatility_60d",
    "downside_volatility_20d",
    "downside_volatility_60d",
    "max_daily_return_20d",
    "volume_ratio_20",
    "volume_ratio_60",
    "distance_to_52w_high",
    "distance_to_52w_low",
    "beta_60d",
    "idiosyncratic_ret_20d",
    "idiosyncratic_ret_60d",
]

RAW_PRICE_FEATURE_COLUMNS = [
    "ret_1d",
    "ret_5d",
    "ret_10d",
    "ret_20d",
    "ret_60d",
    "ret_120d",
    "ret_5d_reversal",
    "momentum_126d_skip_10",
    "momentum_252d_skip_20",
    "volatility_20d",
    "volatility_60d",
    "downside_volatility_20d",
    "downside_volatility_60d",
    "max_daily_return_20d",
    "ret_20d_vol_adj",
    "ret_60d_vol_adj",
    "price_vs_sma_20",
    "price_vs_sma_50",
    "price_vs_sma_100",
    "price_vs_sma_200",
    "sma_20_vs_50",
    "sma_50_vs_200",
    "rsi_14",
    "volume_ratio_20",
    "volume_ratio_60",
    "distance_to_52w_high",
    "distance_to_52w_low",
    "log_dollar_volume_20d",
    "amihud_20d",
    "beta_60d",
    "idiosyncratic_ret_20d",
    "idiosyncratic_ret_60d",
]

MARKET_CONTEXT_FEATURE_COLUMNS = [
    "spy_ret_20d",
    "spy_ret_60d",
    "spy_ret_120d",
    "spy_volatility_20d",
    "spy_volatility_60d",
    "spy_price_vs_sma_50",
    "spy_price_vs_sma_200",
    "spy_rsi_14",
    "rel_ret_20d_vs_spy",
    "rel_ret_60d_vs_spy",
    "rel_ret_120d_vs_spy",
    "rel_rsi_vs_spy",
    "rel_volatility_20d_vs_spy",
    "rel_volatility_60d_vs_spy",
    "breadth_above_50",
    "breadth_above_200",
    "breadth_ret_20d_median",
    "breadth_ret_60d_median",
    "breadth_ret_120d_median",
    "breadth_volatility_20d_median",
    "breadth_volatility_60d_median",
]

SECTOR_CONTEXT_FEATURE_COLUMNS = [
    "sector_ret_20d",
    "sector_ret_60d",
    "sector_ret_120d",
    "sector_momentum_126d_skip_10",
    "sector_momentum_252d_skip_20",
    "sector_volatility_20d",
    "sector_volatility_60d",
    "sector_price_vs_sma_50",
    "sector_price_vs_sma_200",
    "sector_rsi_14",
]

RELATIVE_SECTOR_FEATURE_COLUMNS = [
    "rel_ret_20d_vs_sector_etf",
    "rel_ret_60d_vs_sector_etf",
    "rel_ret_120d_vs_sector_etf",
    "rel_momentum_126d_vs_sector_etf",
    "rel_momentum_252d_vs_sector_etf",
    "volatility_20d_vs_sector_etf",
    "volatility_60d_vs_sector_etf",
    "price_vs_sector_sma_50",
    "price_vs_sector_sma_200",
    "rsi_vs_sector_etf",
]

TECHNICAL_COMPOSITE_INPUT_COLUMNS = [
    "ret_60d_sector_pct_rank",
    "ret_120d_sector_pct_rank",
    "momentum_126d_skip_10_sector_pct_rank",
    "ret_60d_vol_adj_sector_pct_rank",
    "price_vs_sma_200_sector_pct_rank",
    "distance_to_52w_high_sector_pct_rank",
    "rel_ret_60d_vs_spy_pct_rank",
    "idiosyncratic_ret_60d_sector_pct_rank",
]


def unique_columns(columns: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for column in columns:
        if column not in seen:
            result.append(column)
            seen.add(column)
    return result


def macro_feature_columns(dataset: pd.DataFrame) -> list[str]:
    return [
        column
        for column in dataset.columns
        if column.endswith("_level") or column.endswith("_chg_1") or column.endswith("_chg_5")
    ]


def model_feature_columns(dataset: pd.DataFrame) -> list[str]:
    feature_columns = unique_columns(
        [
            *RAW_PRICE_FEATURE_COLUMNS,
            *MARKET_CONTEXT_FEATURE_COLUMNS,
            *SECTOR_CONTEXT_FEATURE_COLUMNS,
            *[f"{column}_pct_rank" for column in RANK_BASE_COLUMNS],
            *[f"{column}_sector_pct_rank" for column in RANK_BASE_COLUMNS],
            *[f"{column}_minus_sector_median" for column in SECTOR_MEDIAN_COLUMNS],
            *RELATIVE_SECTOR_FEATURE_COLUMNS,
            "technical_composite_score",
        ]
    )
    feature_columns.extend(column for column in macro_feature_columns(dataset) if column not in feature_columns)
    return feature_columns


def add_cross_sectional_model_features(dataset: pd.DataFrame, round_trip_cost: float | None = None) -> pd.DataFrame:
    rank_features = []
    for column in RANK_BASE_COLUMNS:
        rank_features.append(dataset.groupby("date")[column].rank(pct=True).rename(f"{column}_pct_rank"))
        rank_features.append(
            dataset.groupby(["date", "sector"])[column].rank(pct=True).rename(f"{column}_sector_pct_rank")
        )
    dataset = pd.concat([dataset, *rank_features], axis=1).copy()

    sector_median_features = []
    for column in SECTOR_MEDIAN_COLUMNS:
        sector_median = dataset.groupby(["date", "sector"])[column].transform("median")
        sector_median_features.append((dataset[column] - sector_median).rename(f"{column}_minus_sector_median"))
    dataset = pd.concat([dataset, *sector_median_features], axis=1).copy()

    derived_features = pd.DataFrame(
        {
            "rel_ret_20d_vs_sector_etf": dataset["ret_20d"] - dataset["sector_ret_20d"],
            "rel_ret_60d_vs_sector_etf": dataset["ret_60d"] - dataset["sector_ret_60d"],
            "rel_ret_120d_vs_sector_etf": dataset["ret_120d"] - dataset["sector_ret_120d"],
            "rel_momentum_126d_vs_sector_etf": dataset["momentum_126d_skip_10"]
            - dataset["sector_momentum_126d_skip_10"],
            "rel_momentum_252d_vs_sector_etf": dataset["momentum_252d_skip_20"]
            - dataset["sector_momentum_252d_skip_20"],
            "volatility_20d_vs_sector_etf": dataset["volatility_20d"] - dataset["sector_volatility_20d"],
            "volatility_60d_vs_sector_etf": dataset["volatility_60d"] - dataset["sector_volatility_60d"],
            "price_vs_sector_sma_50": dataset["price_vs_sma_50"] - dataset["sector_price_vs_sma_50"],
            "price_vs_sector_sma_200": dataset["price_vs_sma_200"] - dataset["sector_price_vs_sma_200"],
            "rsi_vs_sector_etf": dataset["rsi_14"] - dataset["sector_rsi_14"],
        },
        index=dataset.index,
    )
    if {"forward_return_14d_next_close", "sector_forward_return_14d_next_close"}.issubset(dataset.columns):
        derived_features["sector_neutral_forward_return_14d"] = (
            dataset["forward_return_14d_next_close"] - dataset["sector_forward_return_14d_next_close"]
        )
    if round_trip_cost is not None and "sector_neutral_forward_return_14d" in derived_features.columns:
        derived_features[RANK_RETURN_COLUMN] = derived_features["sector_neutral_forward_return_14d"] - round_trip_cost
    dataset = pd.concat([dataset, derived_features], axis=1).copy()

    technical_composite_score = dataset[TECHNICAL_COMPOSITE_INPUT_COLUMNS].mean(axis=1).rename("technical_composite_score")
    dataset = pd.concat([dataset, technical_composite_score], axis=1).copy()
    return dataset.replace([np.inf, -np.inf], np.nan)
