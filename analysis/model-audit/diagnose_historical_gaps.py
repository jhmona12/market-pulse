"""Explain missing outcomes and vendor disagreements without survivor deletion.

Run after build_historical_matrix.py with --matrix-root to audit a separate
rebuild. The frozen initial matrix remains the before/after comparison baseline.
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from experiment_data import ROOT
from common import write_json
from historical_universe import normalize_bars, apply_reviewed_price_corrections, reviewed_dividend_value, member_mask
from outcome_diagnostics import no_trade_calendar, unknown_reason, disputed_price_exposure, spinoff_return, missingness_summary
from fetch_historical_archive import sha


def price_review(folder, groups, calendar, no_trades, reference):
    flagged = pd.read_csv(folder / "archive/full_check_flagged.csv", parse_dates=["date"])
    flagged = flagged[flagged.date.ge("2011-11-16")].copy()
    spin = pd.read_csv(folder / "archive/spinoffs.csv", parse_dates=["ex_date"])
    adjudicated = pd.read_csv(folder / "archive/full_check_adjudicated.csv", parse_dates=["date"])
    flagged = flagged.merge(adjudicated[["label", "date", "verdict"]], on=["label", "date"], how="left", validate="one_to_one")
    output = []
    reviewed = json.loads((ROOT / "config/research-price-reviews.json").read_text())["reviews"]
    for row in flagged.itertuples():
        bars = groups[row.label].set_index("date").reindex(calendar)
        returns = bars.adj_close.pct_change(fill_method=None)
        index = calendar.get_loc(row.date)
        status = "unresolved_vendor_disagreement"
        reason = "Neither publisher verdict nor the alternate return is independent proof of the correct close."
        if row.date in no_trades.get(row.label, ()):
            status, reason = "halt_masked", "Not a valid regular-session execution close."
        elif index and calendar[index - 1] in no_trades.get(row.label, ()):
            status, reason = "post_halt_comparison", "Previous close was halted; two vendors allocate the reopening move differently."
        action = spin[spin.ticker.eq(row.label) & spin.ex_date.eq(row.date)]
        formula = np.nan
        if len(action) == 1:
            formula = spinoff_return(next(action.itertuples()))
            if abs(formula - returns.loc[row.date]) < .00005:
                status = "corporate_action_formula_reconciled"
                reason = "Archive matches holder return including distributed stock; price-only comparator is not equivalent. Quotes still share upstream provenance."
        primary = next((r for r in reviewed if r["security_id"] == row.label and pd.Timestamp(r["date"]) == row.date), None)
        corrected_return = np.nan
        if primary and primary["action"] == "correct_dividend":
            corrected = apply_reviewed_price_corrections(groups[row.label], [primary]).set_index("date").reindex(calendar)
            corrected_return = corrected.adj_close.pct_change(fill_method=None).loc[row.date]
            status, reason = "primary_sourced_dividend_corrected", primary["note"]
        elif primary and primary["action"] == "confirm_dividend":
            dividend = reviewed_dividend_value(groups[row.label], primary)
            actual_dividend = bars.loc[row.date, "dividends"]
            formula = (bars.close.iloc[index] + dividend) / bars.close.iloc[index - 1] - 1
            if abs(actual_dividend - dividend) > 1e-6 or abs(formula - returns.loc[row.date]) > 1e-6:
                raise ValueError("Primary dividend confirmation does not reconcile")
            status, reason = "primary_sourced_dividend_confirmed", primary["note"]
        elif primary:
            status, reason = "unresolved_source_conflict", primary["note"]
        paired = flagged[flagged.label.eq(row.label) & flagged.date.eq(calendar[min(index + 1, len(calendar) - 1)])]
        pair_difference = np.nan
        if len(paired) == 1:
            nxt = paired.iloc[0]
            pair_difference = (1 + row.ours) * (1 + nxt.ours) - (1 + row.ret) * (1 + nxt.ret)
        cached_return = np.nan
        path = reference / "raw/prices" / f"{row.label}.csv"
        if path.exists():
            cached = pd.read_csv(path, parse_dates=["date"]).set_index("date").reindex(calendar)
            cached_return = cached.close.pct_change(fill_method=None).loc[row.date]
        output.append({**row._asdict(), "status": status, "reason": reason, "archive_return": returns.loc[row.date],
                       "action_formula_return": formula, "cached_yahoo_return": cached_return,
                       "corrected_return": corrected_return, "primary_source_url": primary["source_url"] if primary else None,
                       "paired_two_session_difference": pair_difference})
    return pd.DataFrame(output).drop(columns="Index")


def diagnose(folder, matrix_root, reference, output):
    output.mkdir(parents=True, exist_ok=True)
    prices = pd.read_parquet(folder / "archive/prices.parquet", filters=[("date", ">=", pd.Timestamp("2010-01-01"))])
    groups = {symbol: group.sort_values("date") for symbol, group in prices.groupby("ticker")}
    calendar = pd.DatetimeIndex(groups["SPY"].date)
    halt_path = ROOT / "config/research-no-trade-periods.json"
    no_trades = no_trade_calendar(calendar, pd.read_csv(folder / "archive/no_trade_days.csv"),
        pd.read_csv(folder / "archive/exit_gaps.csv"), json.loads(halt_path.read_text())["periods"])
    events = {e["security_id"]: e for e in json.loads((ROOT / "config/research-terminal-events.json").read_text())["events"]}
    spans = pd.read_parquet(folder / "archive/membership_intervals.parquet")
    current = set(spans.loc[spans.end.isna() | spans.end.gt(calendar[-1]), "ticker"])
    reviews = price_review(folder, groups, calendar, no_trades, reference)
    reviews.to_csv(output / "price-disagreements.csv", index=False)
    result = {"as_of": str(calendar[-1].date()), "matrix_root": str(matrix_root.relative_to(ROOT)),
              "price_disagreements": reviews.status.value_counts().to_dict(),
              "publisher_verdicts": reviews.verdict.fillna("not_adjudicated").value_counts().to_dict(),
              "price_caveat": "Agreement with a cached Yahoo series is a transformation check, not independent vendor validation.",
              "source_hashes": {"no_trade_periods": sha(halt_path), "events": sha(ROOT / "config/research-terminal-events.json"), "price_reviews": sha(ROOT / "config/research-price-reviews.json")},
              "horizons": {}}
    for horizon in (14, 252):
        prefix = matrix_root / "price_only" / f"matrix_{horizon}_v2"
        meta = json.loads(prefix.with_suffix(".json").read_text())
        rows = pd.read_csv(prefix.with_suffix(".csv"), parse_dates=["date", "label_available_at"])
        matrix = np.load(prefix.with_suffix(".npy"), mmap_mode="r")
        if matrix.shape != (len(rows), len(meta["features"])) or not np.isfinite(matrix).all():
            raise ValueError("Invalid rebuilt feature matrix")
        if rows.duplicated(["symbol", "date"]).any() or rows.outcome.lt(-1.00000001).any():
            raise ValueError("Duplicate observations or impossible total return")
        if rows.loc[rows.excess.notna(), "label_available_at"].gt(pd.Timestamp(meta["as_of"])).any():
            raise ValueError("Unmatured label")
        if matrix_root != folder:
            for key, path in (("events", "research-terminal-events.json"), ("no_trade_periods", "research-no-trade-periods.json"), ("price_reviews", "research-price-reviews.json")):
                if meta["source_hashes"].get(key) != sha(ROOT / "config" / path):
                    raise ValueError(f"Matrix predates {path}; rebuild before auditing")
        for symbol, group in rows.groupby("symbol"):
            if not member_mask(group.date, spans[spans.ticker.eq(symbol)]).all():
                raise ValueError("Ineligible historical observation")
        unknown = rows[rows.excess.isna()]
        records = []
        for symbol, group in unknown.groupby("symbol"):
            raw = groups[symbol]
            masked = no_trades.get(symbol, set())
            bars = normalize_bars(raw, masked).set_index("date").reindex(calendar)
            last = bars.close.last_valid_index()
            for row in group.itertuples():
                i = calendar.get_loc(row.date)
                entry, end = calendar[i + 1], calendar[i + horizon + 1]
                reason = unknown_reason(entry_date=entry, exit_date=end, last_trade=last,
                    entry_valid=pd.notna(bars.close.loc[entry]), exit_valid=pd.notna(bars.close.loc[end]),
                    no_trade_dates=masked, event=events.get(symbol), stock_outcome=row.outcome)
                records.append({"symbol": symbol, "date": row.date, "sector": row.sector, "reason": reason,
                    "entry_date": entry, "exit_date": end, "last_observed_trade": last,
                    "last_source": raw.iloc[-1].source, "prior_60d_return": row.ret_60d,
                    "rights_bounded": pd.notna(row.excess_lower) and pd.notna(row.excess_upper),
                    "current_member": symbol in current})
        detail = pd.DataFrame(records)
        detail.to_csv(output / f"unknown-{horizon}d.csv", index=False)
        detail.groupby(["symbol", "reason"]).size().rename("rows").to_csv(output / f"unknown-{horizon}d-by-security.csv")
        exposure = rows[["symbol", "date", "sector", "excess", "ret_60d", "volatility_60d"]].copy()
        exposure["feature_exposure"] = False
        exposure["label_exposure"] = False
        for symbol, flags in reviews[reviews.status.str.startswith("unresolved_")].groupby("label"):
            selected = exposure.symbol.eq(symbol)
            past, future = disputed_price_exposure(exposure.loc[selected, "date"], flags.date, calendar, horizon)
            exposure.loc[selected, "feature_exposure"] = past
            exposure.loc[selected, "label_exposure"] = future
        exposure.loc[exposure.feature_exposure | exposure.label_exposure].to_csv(output / f"quarantine-exposure-{horizon}d.csv", index=False)
        by_sector = rows.assign(unknown=rows.excess.isna()).groupby("sector").agg(rows=("symbol", "size"), unknown=("unknown", "sum"))
        by_sector["unknown_rate"] = by_sector.unknown / by_sector.rows
        by_sector.to_csv(output / f"missingness-{horizon}d-by-sector.csv")
        shared_context_dates = set(exposure.loc[exposure.feature_exposure, "date"])
        before = pd.read_csv(folder / "price_only" / f"matrix_{horizon}_v2.csv", parse_dates=["date"])
        compared = before[["symbol", "date", "excess"]].merge(rows[["symbol", "date", "excess"]], on=["symbol", "date"], how="outer", suffixes=("_before", "_after"), indicator=True, validate="one_to_one")
        fixed = compared._merge.eq("both") & compared.excess_before.isna() & compared.excess_after.notna()
        removed = compared._merge.eq("left_only")
        compared.loc[fixed | removed].to_csv(output / f"changed-rows-{horizon}d.csv", index=False)
        summary = {"rows_before": len(before), "rows_after": len(rows), "unknown_before": int(before.excess.isna().sum()),
            "unknown_after": len(unknown), "repaired_labels": int(fixed.sum()), "removed_invalid_feature_rows": int(removed.sum()),
            "formerly_known_now_unknown": int((compared._merge.eq("both") & compared.excess_before.notna() & compared.excess_after.isna()).sum()),
            "unknown_reasons": detail.reason.value_counts().to_dict(), "unknown_former_member_rows": int((~detail.current_member).sum()),
            "distributions": missingness_summary(rows),
            "quarantine_feature_rows": int(exposure.feature_exposure.sum()), "quarantine_label_rows": int(exposure.label_exposure.sum()),
            "quarantine_either_rows": int((exposure.feature_exposure | exposure.label_exposure).sum()),
            "rows_on_shared_context_exposure_dates": int(exposure.date.isin(shared_context_dates).sum()),
            "exposure_caveat": "Direct security exposure is not the full footprint: breadth and cross-sectional ranks can affect other names. A quarantine must rebuild features/context, not merely delete selected rows from the existing matrix.",
            "failure_examples_retained": rows[rows.symbol.isin(["SIVB", "SBNY", "FRC", "BTUUQ-201704"]) & rows.outcome.notna()].sort_values("outcome").groupby("symbol").head(1)[["symbol", "date", "outcome"]].astype({"date": str}).to_dict("records"),
            "matrix_hashes": {suffix: sha(prefix.with_suffix(suffix)) for suffix in (".csv", ".json", ".npy")},
            "deletion_policy": "Do not remove unknown outcomes before portfolio selection. No label is invented; unknown selected holdings retain their weight and invalidate point performance unless all returns have defensible bounds."}
        result["horizons"][str(horizon)] = summary
    write_json(result, output / "summary.json")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--matrix-root", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    diagnose(ROOT / args.experiment, ROOT / args.matrix_root, ROOT / args.reference, ROOT / args.output)
