from __future__ import annotations

import argparse
import json
import os
import time
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd

from common import REFERENCE_DIR, REPORTS_DIR, ROOT, write_json


DEFAULT_TAGS = (
    "Assets",
    "Liabilities",
    "StockholdersEquity",
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "NetIncomeLoss",
    "OperatingIncomeLoss",
    "NetCashProvidedByUsedInOperatingActivities",
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "CommonStocksIncludingAdditionalPaidInCapital",
    "CommonStockSharesOutstanding",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit whether official SEC Company Facts can support reliable point-in-time fundamental features."
    )
    parser.add_argument("--limit", type=int, default=0, help="Limit symbols for a quick smoke test. Zero means all current constituents.")
    parser.add_argument("--sleep-seconds", type=float, default=0.15, help="Delay between SEC companyfacts requests.")
    parser.add_argument(
        "--tags",
        default=",".join(DEFAULT_TAGS),
        help="Comma-separated SEC us-gaap tags to audit.",
    )
    parser.add_argument(
        "--output-name",
        default="sec_fundamentals_coverage",
        help="Base name for report outputs inside data/modeling/reports.",
    )
    return parser.parse_args()


def sec_user_agent() -> str:
    return os.environ.get(
        "MARKET_PULSE_SEC_USER_AGENT",
        "MarketPulse personal research dashboard harrisonmona@example.com",
    )


def fetch_json(url: str, timeout: int = 60) -> dict:
    request = Request(
        url,
        headers={
            "User-Agent": sec_user_agent(),
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize_ticker(symbol: str) -> str:
    return symbol.upper().replace(".", "-").strip()


def ticker_map() -> dict[str, int]:
    payload = fetch_json("https://www.sec.gov/files/company_tickers.json")
    rows = payload.values() if isinstance(payload, dict) else payload
    mapping = {}
    for row in rows:
        ticker = normalize_ticker(str(row.get("ticker", "")))
        cik = row.get("cik_str")
        if ticker and cik:
            mapping[ticker] = int(cik)
    return mapping


def tag_units(companyfacts: dict, tag: str) -> list[dict]:
    fact = companyfacts.get("facts", {}).get("us-gaap", {}).get(tag, {})
    units = fact.get("units", {})
    rows = []
    for unit_name, unit_rows in units.items():
        for row in unit_rows:
            if row.get("filed") and row.get("val") is not None:
                rows.append({**row, "unit": unit_name})
    return rows


def main() -> None:
    args = parse_args()
    tags = [tag.strip() for tag in args.tags.split(",") if tag.strip()]
    constituents = pd.read_csv(REFERENCE_DIR / "sp500_constituents.csv")
    constituents["symbol"] = constituents["symbol"].astype(str)
    if args.limit > 0:
        constituents = constituents.head(args.limit).copy()

    sec_map = ticker_map()
    symbol_rows = []
    tag_rows = []
    failures = {}
    for row in constituents.to_dict(orient="records"):
        symbol = row["symbol"]
        cik = sec_map.get(normalize_ticker(symbol))
        symbol_record = {
            "symbol": symbol,
            "name": row.get("name", ""),
            "sector": row.get("sector", ""),
            "cik": cik,
            "matched_sec": bool(cik),
        }
        if not cik:
            symbol_rows.append({**symbol_record, "available_tag_count": 0, "latest_filing_date": ""})
            continue

        try:
            companyfacts = fetch_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
        except Exception as error:  # noqa: BLE001
            failures[symbol] = str(error)
            symbol_rows.append({**symbol_record, "available_tag_count": 0, "latest_filing_date": ""})
            time.sleep(args.sleep_seconds)
            continue

        available_tag_count = 0
        latest_filing_dates = []
        for tag in tags:
            rows = tag_units(companyfacts, tag)
            if rows:
                available_tag_count += 1
                filed_dates = [item.get("filed") for item in rows if item.get("filed")]
                latest = max(filed_dates) if filed_dates else ""
                latest_filing_dates.extend(filed_dates)
                tag_rows.append(
                    {
                        "symbol": symbol,
                        "cik": cik,
                        "tag": tag,
                        "observation_count": len(rows),
                        "latest_filing_date": latest,
                        "units": ",".join(sorted({str(item.get("unit")) for item in rows if item.get("unit")})),
                    }
                )
            else:
                tag_rows.append(
                    {
                        "symbol": symbol,
                        "cik": cik,
                        "tag": tag,
                        "observation_count": 0,
                        "latest_filing_date": "",
                        "units": "",
                    }
                )
        symbol_rows.append(
            {
                **symbol_record,
                "available_tag_count": available_tag_count,
                "latest_filing_date": max(latest_filing_dates) if latest_filing_dates else "",
            }
        )
        time.sleep(args.sleep_seconds)

    symbol_coverage = pd.DataFrame(symbol_rows)
    tag_coverage = pd.DataFrame(tag_rows)
    symbol_path = REPORTS_DIR / f"{args.output_name}_symbols.csv"
    tag_path = REPORTS_DIR / f"{args.output_name}_tags.csv"
    symbol_coverage.to_csv(symbol_path, index=False)
    tag_coverage.to_csv(tag_path, index=False)

    tag_summary = []
    if not tag_coverage.empty:
        tag_summary = (
            tag_coverage.assign(has_tag=tag_coverage["observation_count"] > 0)
            .groupby("tag")
            .agg(
                covered_symbols=("has_tag", "sum"),
                audited_symbols=("symbol", "nunique"),
                median_observations=("observation_count", "median"),
                latest_filing_date=("latest_filing_date", "max"),
            )
            .reset_index()
            .sort_values(["covered_symbols", "tag"], ascending=[False, True])
            .to_dict(orient="records")
        )

    report = {
        "generated_at": pd.Timestamp.now("UTC").isoformat(),
        "source": "SEC Company Facts API",
        "source_urls": [
            "https://www.sec.gov/files/company_tickers.json",
            "https://data.sec.gov/api/xbrl/companyfacts/",
        ],
        "audited_symbol_count": int(len(symbol_coverage)),
        "matched_sec_symbol_count": int(symbol_coverage["matched_sec"].sum()) if not symbol_coverage.empty else 0,
        "failures": failures,
        "tag_summary": tag_summary,
        "outputs": {
            "symbol_coverage": str(symbol_path.relative_to(ROOT)),
            "tag_coverage": str(tag_path.relative_to(ROOT)),
        },
        "notes": [
            "This script audits official, free SEC data before any fundamentals are allowed into the long-horizon model.",
            "SEC fundamentals must be aligned by filed date, not fiscal period end date, to avoid look-ahead bias.",
            "Do not wire these features into the daily model refresh until coverage, tag definitions, and filing-date lag behavior are reviewed.",
        ],
    }
    write_json(report, REPORTS_DIR / f"{args.output_name}.json")
    print(
        f"Audited {report['audited_symbol_count']} symbols; SEC matched {report['matched_sec_symbol_count']}; "
        f"failures {len(failures)}."
    )


if __name__ == "__main__":
    main()
