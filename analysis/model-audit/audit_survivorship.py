"""Quantify missing former-member data without claiming a clean PIT universe."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/modeling"))
from common import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True)
    args = parser.parse_args()
    folder = ROOT / args.experiment
    manifest = json.loads((folder / "manifest.json").read_text())
    changes = pd.read_csv(folder / "membership_candidates.csv", keep_default_na=False)
    reference = pd.read_csv(folder / "reference.csv")
    window_start = (pd.Timestamp(manifest["as_of"]) - pd.DateOffset(years=15)).date().isoformat()
    changes = changes[changes.change_date.ge(window_start) & changes.change_date.le(manifest["as_of"])]
    removals = changes[changes.removed_symbol.ne("")].copy()
    rows = []
    for row in removals.to_dict("records"):
        evidence = manifest["histories"].get(row["removed_symbol"], {})
        rows.append({**row, "price_status": evidence.get("status", "not_cached"), "provider_rows": evidence.get("rows", 0),
                     "provider_end": evidence.get("end"), "has_archived_segment": bool(evidence.get("archived_segment")),
                     "membership_verified": False, "terminal_outcome_verified": False,
                     "tracking_policy": "retain_history_and_finish_existing_labels; no_new_sp500_entries_after_verified_removal"})
    pd.DataFrame(rows).to_csv(folder / "former_member_review_queue.csv", index=False)
    removed_since_original = reference[reference.in_original_snapshot & ~reference.in_current_snapshot].symbol.tolist()
    missing = sorted(set(removals.removed_symbol) - set(manifest["histories"]))
    payload = {"as_of": manifest["as_of"], "window_start": window_start, "historical_removal_events": len(rows), "unique_removed_tickers": int(removals.removed_symbol.nunique()),
        "removed_since_original_snapshot": removed_since_original, "removed_tickers_without_experiment_prices": missing,
        "original_names_missing_removal_event": sorted(set(removed_since_original) - set(removals.removed_symbol)),
        "terminal_or_alias_review": {s: r for s, r in manifest["histories"].items() if r.get("end") != manifest["as_of"]},
        "promotion_gate": "BLOCKED: membership intervals, stable security IDs, historical sectors and terminal outcomes require verification",
        "scope": "Discovery from free historical change table; a list of ever-members is not a point-in-time universe and not survivorship-free"}
    write_json(payload, folder / "survivorship_audit.json")
    print(json.dumps({k:v for k,v in payload.items() if k not in {"removed_tickers_without_experiment_prices", "terminal_or_alias_review"}}, indent=2))
    print(f"Removed tickers without experiment prices: {len(missing)}")


if __name__ == "__main__":
    main()
