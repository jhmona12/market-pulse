"""Reconcile frozen PIT matrices, archive prices and candidate artifacts."""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd
import xgboost as xgb

from experiment_data import ROOT
from common import write_json
from historical_universe import member_mask
from artifact_contract import validate_model_artifact
from fetch_historical_archive import sha


def audit(folder, reference):
    archive = folder / "archive"
    spans = pd.read_parquet(archive / "membership_intervals.parquet")
    prices = pd.read_parquet(archive / "prices.parquet", columns=["ticker", "date", "adj_close"])
    membership_snapshots = pd.read_parquet(archive / "members.parquet", filters=[("date", ">=", pd.Timestamp("2011-11-16"))])
    sampled_dates = membership_snapshots.date.drop_duplicates().sort_values()
    for date in sampled_dates:
        expected = set(spans.loc[spans.start.le(date) & (spans.end.isna() | spans.end.gt(date)), "ticker"])
        actual = set(membership_snapshots.loc[membership_snapshots.date.eq(date), "ticker"])
        if expected != actual or not 450 <= len(actual) <= 550:
            raise ValueError(f"Membership interval/snapshot mismatch: {date}")
    events = json.loads((ROOT / "config/research-terminal-events.json").read_text())["events"]
    checks = []
    for symbol in ("SPY", "AAPL", "MSFT", "GOOGL", "NVDA", "XOM", "CVX", "BAC", "JPM", "XLF", "XLK"):
        cached = pd.read_csv(reference / "raw/prices" / f"{symbol}.csv", parse_dates=["date"])
        joined = prices[prices.ticker.eq(symbol)].merge(cached[["date", "close"]], on="date", validate="one_to_one").sort_values("date")
        difference = (joined.adj_close.pct_change(fill_method=None) - joined.close.pct_change(fill_method=None)).abs()
        if len(joined) < 3000 or difference.max() > .01:
            raise ValueError(f"Cross-cache return discrepancy needs review: {symbol}")
        checks.append({"symbol": symbol, "rows": len(joined), "max_daily_return_difference": float(difference.max())})
    result = {"status": "research_only_not_promoted", "cross_cache_check": checks,
              "membership_snapshot_interval_check_dates": len(sampled_dates),
              "cross_cache_caveat": "Transformation/revision check, not independent vendor verification: both archives contain Yahoo-derived data.",
              "registered_events": len(events), "events_with_unpriced_rights": sum(bool(e.get("unresolved_rights")) for e in events), "horizons": {}}
    for horizon in (14, 252):
        root = folder / "price_only"
        prefix = root / f"matrix_{horizon}_v2"
        meta = json.loads(prefix.with_suffix(".json").read_text())
        for key, name in (("events", "research-terminal-events.json"), ("identities", "research-security-identities.json"),
                          ("no_trade_periods", "research-no-trade-periods.json"), ("price_reviews", "research-price-reviews.json")):
            expected = meta["source_hashes"].get(key)
            if expected and expected != sha(ROOT / "config" / name):
                raise ValueError(f"Matrix predates {name}; rebuild and refit in a separate directory before claiming updated results")
        rows = pd.read_csv(prefix.with_suffix(".csv"), parse_dates=["date", "label_available_at"])
        as_of = pd.Timestamp(meta["as_of"])
        values = np.load(prefix.with_suffix(".npy"), mmap_mode="r")
        if values.shape != (len(rows), len(meta["features"])) or not np.isfinite(values).all():
            raise ValueError("Invalid feature matrix")
        if rows.duplicated(["date", "symbol"]).any() or rows.outcome.lt(-1.000000001).any():
            raise ValueError("Invalid observation identity or stock return")
        if rows.loc[rows.excess.notna(), "label_available_at"].gt(as_of).any():
            raise ValueError("Unmatured label admitted")
        former = set(spans.loc[spans.end.le(as_of), "ticker"]) - set(spans.loc[spans.end.isna() | spans.end.gt(as_of), "ticker"])
        for symbol, group in rows.groupby("symbol"):
            if not member_mask(group.date, spans[spans.ticker.eq(symbol)]).all():
                raise ValueError(f"Ineligible historical signal: {symbol}")
        unknown = rows[rows.excess.isna()].groupby("symbol").agg(rows=("date", "size"), first=("date", "min"), last=("date", "max"))
        unknown["reason"] = "requires_event_price_or_entry_review"
        for event in events:
            if event["security_id"] in unknown.index and event.get("unresolved_rights"):
                unknown.loc[event["security_id"], "reason"] = "unpriced_contingent_rights_bounds_only"
        unknown.to_csv(root / f"unresolved_{horizon}d_review.csv")
        tuning = root / f"tuning_{horizon}d"
        outcome = json.loads((tuning / "results.json").read_text())
        for suffix, expected in outcome["source_matrix_hashes"].items():
            if sha(prefix.with_suffix(suffix)) != expected:
                raise ValueError("Candidate used an earlier matrix; rerun tuning")
        model = ROOT / outcome["candidate_path"]
        metadata = outcome["candidate_metadata"]
        validate_model_artifact(model, metadata)
        booster = xgb.Booster(); booster.load_model(model)
        probe = np.unique(np.linspace(0, len(rows) - 1, min(2048, len(rows)), dtype=int))
        scores = booster.predict(xgb.DMatrix(values[probe], feature_names=meta["features"]))
        if not np.isfinite(scores).all():
            raise ValueError("Nonfinite artifact replay")
        cohorts = pd.read_csv(tuning / "test_cohorts.csv")
        bounds = []
        for stage, group in cohorts.groupby("stage"):
            valid = group.top_excess_lower.notna() & group.top_excess_upper.notna()
            bounds.append({"stage": stage, "cohorts": int(valid.sum()), "total_cohorts": len(group),
                "mean_top_excess_lower": float(group.loc[valid, "top_excess_lower"].mean()),
                "mean_top_excess_upper": float(group.loc[valid, "top_excess_upper"].mean())})
        examples = rows[rows.symbol.isin(["SIVB", "SBNY", "FRC", "BTUUQ-201704"]) & rows.outcome.notna()].sort_values("outcome").groupby("symbol").head(1)
        result["horizons"][str(horizon)] = {"quality": meta["quality"], "features": len(meta["features"]),
            "training_rows": metadata["training_rows"], "training_dates": metadata["training_date_count"], "training_symbols": metadata["training_symbols"],
            "training_start": metadata["training_start_date"], "training_end": metadata["training_end_date"],
            "former_symbols_in_matrix": int(rows[rows.symbol.isin(former)].symbol.nunique()), "former_rows_in_matrix": int(rows.symbol.isin(former).sum()),
            "unresolved_securities": len(unknown), "failure_examples": examples[["symbol", "date", "outcome"]].astype({"date": str}).to_dict("records"),
            "selected_recipe": outcome["selected_recipe"], "rounds": outcome["rounds"], "test_summary": outcome["test_summary"],
            "test_start": outcome["test_start"], "test_end": outcome["test_end"], "test_cohorts": outcome["test_cohorts"],
            "common_complete_test_cohorts": outcome["common_complete_test_cohorts"], "bounds_not_point_estimates": bounds,
            "model_hash": metadata["model_sha256"], "as_of": meta["as_of"]}
    flagged = pd.read_csv(archive / "full_check_flagged.csv", parse_dates=["date"])
    flagged = flagged[flagged.date.ge("2011-11-16")]
    flagged.to_csv(folder / "archive-price-discrepancy-review.csv", index=False)
    result["publisher_flagged_return_discrepancies_in_window"] = len(flagged)
    result["limitations"] = ["Community-sourced reconstruction, not a licensed/reconciled security master.",
        "Historical-sector gaps, upstream return discrepancies and unresolved terminal claims still require review.",
        "Unknown outcomes are excluded from fitting but not from pre-outcome portfolio selection; complete-basket comparisons remain selection-biased.",
        "CVR bounds cover an uncertain claim, not a realizable quoted return or a point label.",
        "Observed test windows are development evidence; overlapping yearly labels and few independent regimes limit inference."]
    write_json(result, folder / "historical_rebuild_audit.json")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--reference", required=True)
    args = parser.parse_args()
    audit(ROOT / args.experiment, ROOT / args.reference)
