from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from common import ROOT, write_json


DEFAULT_PRIMARY_ARTIFACT = "models/long-horizon/xgboost_rank_sector252_15y_monthly_tuned_baseline_comparison.json"
DEFAULT_DRAWDOWN_ARTIFACT = "models/long-horizon/xgboost_rank_sector252_drawdown_adjusted_walk_forward_baseline_comparison.json"
DEFAULT_EXPLAINABILITY = "models/long-horizon/xgboost_rank_sector252_15y_monthly_tuned_research_explainability.json"
DEFAULT_METADATA = "models/long-horizon/xgboost_rank_sector252_15y_monthly_tuned_research_metadata.json"
DEFAULT_LABEL_COMPARISON = "models/long-horizon/long_horizon_label_comparison.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a compact long-horizon research JSON artifact for the dashboard.")
    parser.add_argument("--primary-artifact", default=DEFAULT_PRIMARY_ARTIFACT)
    parser.add_argument("--drawdown-artifact", default=DEFAULT_DRAWDOWN_ARTIFACT)
    parser.add_argument("--explainability", default=DEFAULT_EXPLAINABILITY)
    parser.add_argument("--metadata", default=DEFAULT_METADATA)
    parser.add_argument("--label-comparison", default=DEFAULT_LABEL_COMPARISON)
    parser.add_argument("--live-scores", default="data/model-rank-scores-long-horizon.json")
    parser.add_argument("--scorebook", default="data/model-scorebook.json")
    parser.add_argument("--output", default="data/long-horizon-research.json")
    return parser.parse_args()


def read_json_if_exists(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def strategy_row(artifact: dict, cohort: str, strategy: str = "xgboost_rank") -> dict | None:
    for row in artifact.get(cohort, []):
        if row.get("strategy") == strategy:
            return row
    return None


def strategy_rows(artifact: dict, cohort: str) -> list[dict]:
    rows = artifact.get(cohort, [])
    return sorted(
        rows,
        key=lambda row: (
            row.get("top_decile_return_median") is None,
            -(row.get("top_decile_return_median") or -999),
        ),
    )


def label_summary(artifact: dict, label: str, description: str) -> dict:
    return {
        "label": label,
        "description": description,
        "generatedAt": artifact.get("generatedAt"),
        "targetColumn": artifact.get("targetColumn"),
        "returnColumn": artifact.get("returnColumn"),
        "daily": strategy_row(artifact, "dailySummary") or {},
        "monthly": strategy_row(artifact, "monthlySummary") or {},
        "quarterly": strategy_row(artifact, "quarterlySummary") or {},
        "latestSnapshotDate": artifact.get("latestSnapshotDate"),
    }


def compact_candidate(row: dict) -> dict:
    keys = [
        "date",
        "symbol",
        "sector",
        "close",
        "score_xgboost_rank",
        "xgboost_rank_percentile",
        "sector_relative_momentum_percentile",
        "twelve_one_momentum_percentile",
        "risk_adjusted_momentum_percentile",
        "technical_composite_percentile",
        "baseline_agreement_label",
    ]
    return {key: row.get(key) for key in keys if key in row}


def finite_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(number):
        return None
    return number


def agreement_label(long_row: dict, tactical_row: dict | None) -> str:
    long_percentile = finite_number(long_row.get("modelPercentile"))
    tactical_percentile = finite_number((tactical_row or {}).get("modelPercentile"))
    tactical_momentum = (tactical_row or {}).get("setupType") == "momentum_confirmed"
    if long_percentile is not None and long_percentile >= 90 and tactical_percentile is not None and tactical_percentile >= 80 and tactical_momentum:
        return "Both Models Agree"
    if long_percentile is not None and long_percentile >= 90 and tactical_momentum:
        return "1Y Strong + Tactical Momentum"
    if long_percentile is not None and long_percentile >= 90 and tactical_percentile is not None and tactical_percentile >= 80:
        return "1Y Strong / Tactical Watch"
    if long_percentile is not None and long_percentile >= 90 and tactical_percentile is not None and tactical_percentile < 50:
        return "1Y Strong / Tactical Weak"
    if long_percentile is not None and long_percentile >= 90:
        return "Long-Horizon Leader"
    if tactical_percentile is not None and tactical_percentile >= 90:
        return "Tactical Only"
    if long_percentile is not None and long_percentile >= 80:
        return "Long-Horizon Watch"
    return "Monitor"


def market_cap_bucket(value) -> str:
    number = finite_number(value)
    if number is None:
        return "Unknown cap"
    if number >= 200_000_000_000:
        return "Mega cap"
    if number >= 10_000_000_000:
        return "Large cap"
    if number >= 2_000_000_000:
        return "Mid cap"
    return "Small cap"


def live_rows(live_scores: dict | None, scorebook: dict | None) -> list[dict]:
    if not live_scores or live_scores.get("status") != "ready":
        return []
    tactical_by_symbol = {row.get("symbol"): row for row in (scorebook or {}).get("rows", []) if row.get("symbol")}
    rows = []
    for item in live_scores.get("rankings", []):
        symbol = item.get("symbol")
        tactical = tactical_by_symbol.get(symbol, {})
        market_cap_value = finite_number(tactical.get("marketCapValue"))
        rows.append(
            {
                "symbol": symbol,
                "name": item.get("name") or tactical.get("name") or symbol,
                "sector": item.get("sector") or tactical.get("sector"),
                "industry": tactical.get("industry"),
                "marketCap": tactical.get("marketCap"),
                "marketCapValue": market_cap_value,
                "marketCapBucket": market_cap_bucket(market_cap_value),
                "longModelRank": item.get("modelRank"),
                "longModelUniverseCount": item.get("modelUniverseCount"),
                "longModelScore": item.get("modelScore"),
                "longModelPercentile": item.get("modelPercentile"),
                "longModelBucket": item.get("modelBucket"),
                "longModelReasons": item.get("modelReasons", []),
                "longRiskFlags": item.get("riskFlags", []),
                "tacticalModelRank": tactical.get("modelRank"),
                "tacticalModelPercentile": tactical.get("modelPercentile"),
                "tacticalSetupType": tactical.get("setupType"),
                "tacticalSetupTags": tactical.get("setupTags", []),
                "agreementLabel": agreement_label(item, tactical),
                "close": item.get("close"),
                "beta60d": item.get("beta60d"),
                "return7": item.get("return7"),
                "return14": item.get("return14"),
                "return30": item.get("return30"),
                "return60": item.get("return60"),
                "return90": item.get("return90"),
                "ytdReturn": item.get("ytdReturn"),
                "asOfDate": item.get("asOfDate") or live_scores.get("asOfDate"),
            }
        )
    return sorted(rows, key=lambda row: finite_number(row.get("longModelRank")) or 9999)


def summarize_trends(rows: list[dict]) -> dict:
    if not rows:
        return {"topDecileCount": 0, "bothStrongCount": 0, "tacticalWeakCount": 0, "sectorLeadership": [], "marketCapMix": []}
    top_rows = [row for row in rows if (finite_number(row.get("longModelPercentile")) or 0) >= 90]
    if not top_rows:
        top_rows = rows[: max(1, int(len(rows) * 0.1))]
    sectors: dict[str, dict] = {}
    caps: dict[str, dict] = {}
    both_strong = 0
    tactical_weak = 0
    for row in top_rows:
        sector = row.get("sector") or "Unclassified"
        sector_row = sectors.setdefault(sector, {"sector": sector, "count": 0, "avgLongPercentile": 0.0, "avgTacticalPercentile": 0.0, "symbols": []})
        sector_row["count"] += 1
        sector_row["avgLongPercentile"] += finite_number(row.get("longModelPercentile")) or 0
        sector_row["avgTacticalPercentile"] += finite_number(row.get("tacticalModelPercentile")) or 0
        sector_row["symbols"].append(row.get("symbol"))
        bucket = row.get("marketCapBucket") or "Unknown cap"
        cap_row = caps.setdefault(bucket, {"bucket": bucket, "count": 0, "symbols": []})
        cap_row["count"] += 1
        cap_row["symbols"].append(row.get("symbol"))
        if row.get("agreementLabel") in {"Both Models Agree", "1Y Strong + Tactical Momentum"}:
            both_strong += 1
        if row.get("agreementLabel") == "1Y Strong / Tactical Weak":
            tactical_weak += 1
    sector_leadership = []
    for row in sectors.values():
        row["avgLongPercentile"] = round(row["avgLongPercentile"] / row["count"], 1)
        row["avgTacticalPercentile"] = round(row["avgTacticalPercentile"] / row["count"], 1)
        row["symbols"] = row["symbols"][:6]
        sector_leadership.append(row)
    sector_leadership.sort(key=lambda row: (-row["count"], -row["avgLongPercentile"]))
    return {
        "topDecileCount": len(top_rows),
        "bothStrongCount": both_strong,
        "tacticalWeakCount": tactical_weak,
        "sectorLeadership": sector_leadership[:6],
        "marketCapMix": sorted(caps.values(), key=lambda row: -row["count"]),
    }


def main() -> None:
    args = parse_args()
    primary = read_json_if_exists(ROOT / args.primary_artifact)
    if not primary:
        raise FileNotFoundError(args.primary_artifact)
    drawdown = read_json_if_exists(ROOT / args.drawdown_artifact)
    explainability = read_json_if_exists(ROOT / args.explainability) or {}
    metadata = read_json_if_exists(ROOT / args.metadata) or {}
    label_comparison = read_json_if_exists(ROOT / args.label_comparison) or {}
    live_scores = read_json_if_exists(ROOT / args.live_scores)
    scorebook = read_json_if_exists(ROOT / args.scorebook)

    labels = [
        label_summary(
            primary,
            "Sector-neutral 252D return",
            "Ranks stocks by one-year total return versus their sector ETF after a small round-trip cost.",
        )
    ]
    if drawdown:
        labels.append(
            label_summary(
                drawdown,
                "Drawdown-adjusted 252D return",
                "Ranks stocks by one-year sector-neutral return while rewarding smoother paths and penalizing deeper forward drawdowns versus the sector.",
            )
        )

    rows = live_rows(live_scores, scorebook)
    latest_top = rows[:25] if rows else [compact_candidate(row) for row in primary.get("latestXgboostTop", [])[:25]]
    quarterly_rows = strategy_rows(primary, "quarterlySummary")[:8]
    monthly_rows = strategy_rows(primary, "monthlySummary")[:8]
    top_features = explainability.get("topFeatures", [])[:12]

    output = {
        "status": "ready",
        "generatedAt": pd.Timestamp.now("UTC").isoformat(),
        "sourceGeneratedAt": primary.get("generatedAt"),
        "liveGeneratedAt": live_scores.get("generatedAt") if live_scores else None,
        "asOfDate": live_scores.get("asOfDate") if live_scores else primary.get("latestSnapshotDate"),
        "expectedAsOfDate": live_scores.get("technicalTape", {}).get("expectedAsOfDate") if live_scores else None,
        "dataset": primary.get("dataset"),
        "marketDataStatus": {
            "status": "fresh" if live_scores and live_scores.get("status") == "ready" else "research_only",
            "asOfDate": live_scores.get("asOfDate") if live_scores else None,
            "expectedAsOfDate": live_scores.get("technicalTape", {}).get("expectedAsOfDate") if live_scores else None,
            "message": (
                f"Fresh long-horizon model scores were generated through {live_scores.get('asOfDate')}."
                if live_scores and live_scores.get("status") == "ready"
                else "Live long-horizon scores were unavailable; showing research artifact only."
            ),
        },
        "modelMetadata": {
            "targetColumn": metadata.get("target_column") or primary.get("targetColumn"),
            "returnColumn": metadata.get("return_column") or primary.get("returnColumn"),
            "featureCount": metadata.get("feature_count"),
            "trainingRows": metadata.get("training_rows"),
            "trainingSymbols": metadata.get("training_symbols"),
            "trainingStartDate": metadata.get("training_start_date"),
            "trainingEndDate": metadata.get("training_end_date"),
            "numBoostRound": metadata.get("num_boost_round"),
            "trainSampleFrequency": metadata.get("train_sample_frequency"),
            "methodologyRationale": metadata.get("methodology_rationale", []),
            "liveModel": live_scores.get("model") if live_scores else None,
        },
        "promotedMethodology": primary.get("promotedMethodology", {}),
        "labelSummaries": labels,
        "labelComparison": label_comparison.get("rows", []),
        "rowCount": len(rows),
        "rows": rows,
        "topCandidates": latest_top,
        "trends": summarize_trends(rows),
        "baselineComparison": {
            "quarterly": quarterly_rows,
            "monthly": monthly_rows,
            "notes": primary.get("notes"),
        },
        "shapTopFeatures": top_features,
        "methodology": [
            "This is a separate long-horizon research model; it does not replace the 14-day tactical Momentum Book.",
            "Monthly and quarterly medians are emphasized because daily 252D outcomes overlap heavily and averages can be skewed by outliers.",
            "The promoted long-horizon artifact uses 15 years of labeled history, monthly train/validation sampling, and a compact tuned XGBoost rank model.",
            "Baseline agreement labels compare the model with simple sector-relative and 12-1 momentum rules.",
            "The drawdown-adjusted label is experimental until walk-forward results show it improves returns without only selecting low-volatility laggards.",
        ],
    }
    write_json(output, ROOT / args.output)
    print(f"Wrote long-horizon dashboard artifact to {args.output}")


if __name__ == "__main__":
    main()
