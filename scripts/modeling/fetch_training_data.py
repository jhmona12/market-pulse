from __future__ import annotations

import argparse

from common import (
    MACRO_DIR,
    MACRO_SERIES,
    PRICES_DIR,
    REFERENCE_DIR,
    ROOT,
    SECTOR_ETFS,
    ensure_directories,
    fetch_fred_series,
    fetch_sp500_constituents,
    run_config_for_years,
    safe_fetch_symbols,
    write_csv,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch and cache historical training data for the Market Pulse model.")
    parser.add_argument("--years", type=int, default=10, help="Number of trailing years to cache.")
    parser.add_argument("--refresh-reference", action="store_true", help="Force refresh of the S&P 500 reference file.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ensure_directories()
    config = run_config_for_years(args.years)

    constituents_path = REFERENCE_DIR / "sp500_constituents.csv"
    if args.refresh_reference or not constituents_path.exists():
        constituents = fetch_sp500_constituents()
        write_csv(constituents, constituents_path)
    else:
        import pandas as pd

        constituents = pd.read_csv(constituents_path)

    symbols = constituents["symbol"].dropna().astype(str).tolist()
    context_symbols = ["SPY", *SECTOR_ETFS]
    all_symbols = list(dict.fromkeys([*symbols, *context_symbols]))

    price_failures = safe_fetch_symbols(all_symbols, config.start_date, config.end_date)

    macro_failures: dict[str, str] = {}
    for series_id in MACRO_SERIES:
        try:
            frame = fetch_fred_series(series_id)
            write_csv(frame, MACRO_DIR / f"{series_id}.csv")
        except Exception as error:  # noqa: BLE001
            macro_failures[series_id] = str(error)

    manifest = {
        "generated_at": config.end_date.isoformat(),
        "years_requested": args.years,
        "start_date": config.start_date.date().isoformat(),
        "end_date": config.end_date.date().isoformat(),
        "constituent_count": len(symbols),
        "context_symbol_count": len(context_symbols),
        "total_symbol_count": len(all_symbols),
        "price_cache_dir": str(PRICES_DIR.relative_to(ROOT)),
        "macro_cache_dir": str(MACRO_DIR.relative_to(ROOT)),
        "price_failures": price_failures,
        "macro_failures": macro_failures,
    }
    write_json(manifest, ROOT / "data" / "modeling" / "raw_manifest.json")

    print(
        f"Cached {len(all_symbols) - len(price_failures)}/{len(all_symbols)} price histories and "
        f"{len(MACRO_SERIES) - len(macro_failures)}/{len(MACRO_SERIES)} macro series."
    )
    if price_failures:
        print(f"Price failures: {len(price_failures)}")
    if macro_failures:
        print(f"Macro failures: {len(macro_failures)}")


if __name__ == "__main__":
    main()
