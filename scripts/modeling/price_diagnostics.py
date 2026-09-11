"""Small per-attempt records, separate from model caches and published snapshots."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time


def record_price_readiness(readiness, frames, failures, *, attempt, directory: Path | None = None):
    symbols = sorted(set(frames) | set(failures))
    rows = []
    for symbol in symbols:
        frame = frames.get(symbol)
        latest = None if frame is None or frame.empty else frame["date"].max().date().isoformat()
        rows.append({"symbol": symbol, "latestAcceptedSession": latest,
                     "error": failures.get(symbol),
                     "response": frame.attrs.get("priceHistory") if frame is not None else None})
    record = {"generatedAt": datetime.now(timezone.utc).isoformat(),
              "runId": os.environ.get("GITHUB_RUN_ID"), "runAttempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
              "readinessAttempt": attempt, "readiness": readiness,
              "acceptedSessionCounts": dict(Counter(row["latestAcceptedSession"] or "missing" for row in rows)),
              "symbols": rows}
    failing = set(readiness["missingContext"]) | set(readiness["staleReference"])
    samples = [row for row in rows if row["symbol"] in failing][:6]
    if not readiness["ready"]:
        print("Price response diagnostics: " + json.dumps({"acceptedSessionCounts": record["acceptedSessionCounts"],
                                                         "samples": samples}, allow_nan=False), flush=True)
    if directory is not None:
        # Failure evidence must survive restoring the last verified dashboard.
        try:
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / f"price-readiness-{os.getpid()}-{time.time_ns()}.json"
            target.write_text(json.dumps(record, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        except OSError as error:
            print(f"Unable to save price diagnostics (console evidence retained): {error}", flush=True)
    return record
