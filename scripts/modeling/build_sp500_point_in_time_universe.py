from __future__ import annotations

import argparse
from datetime import date
from html.parser import HTMLParser

import pandas as pd

from common import REFERENCE_DIR, ROOT, fetch_text, fetch_sp500_constituents, write_json


class TableParser(HTMLParser):
    def __init__(self, target_id: str):
        super().__init__()
        self.target_id = target_id
        self.in_target_table = False
        self.table_depth = 0
        self.in_row = False
        self.in_cell = False
        self.current_cell: list[str] = []
        self.current_row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value for key, value in attrs}
        if tag == "table" and attrs_dict.get("id") == self.target_id:
            self.in_target_table = True
            self.table_depth = 1
            return
        if self.in_target_table and tag == "table":
            self.table_depth += 1
        if not self.in_target_table:
            return
        if tag == "tr":
            self.in_row = True
            self.current_row = []
        elif tag in {"td", "th"} and self.in_row:
            self.in_cell = True
            self.current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if not self.in_target_table:
            return
        if tag in {"td", "th"} and self.in_cell:
            text = " ".join("".join(self.current_cell).split())
            self.current_row.append(text)
            self.in_cell = False
            self.current_cell = []
        elif tag == "tr" and self.in_row:
            if any(cell for cell in self.current_row):
                self.rows.append(self.current_row)
            self.in_row = False
            self.current_row = []
        elif tag == "table":
            self.table_depth -= 1
            if self.table_depth <= 0:
                self.in_target_table = False

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.current_cell.append(data)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a best-effort free point-in-time S&P 500 membership reference from Wikipedia changes."
    )
    parser.add_argument(
        "--output-name",
        default="sp500_point_in_time_membership.csv",
        help="CSV output name inside data/modeling/raw/reference.",
    )
    return parser.parse_args()


def table_rows(html: str, target_id: str) -> list[list[str]]:
    parser = TableParser(target_id)
    parser.feed(html)
    return parser.rows


def parse_changes(html: str) -> pd.DataFrame:
    rows = table_rows(html, "changes")
    records = []
    current_date = None
    for row in rows:
        clean = [cell.strip() for cell in row if cell.strip()]
        if not clean or clean[0].lower() in {"date", "effective date", "ticker"}:
            continue
        parsed_date = pd.to_datetime(clean[0], errors="coerce")
        if not pd.isna(parsed_date):
            current_date = clean[0]
            cells = clean
        elif current_date:
            cells = [current_date, *clean]
        else:
            continue
        if len(cells) < 6:
            continue
        records.append(
            {
                "change_date": pd.to_datetime(cells[0], errors="coerce"),
                "added_symbol": cells[1] if cells[1] != "-" else "",
                "added_name": cells[2] if cells[2] != "-" else "",
                "removed_symbol": cells[3] if cells[3] != "-" else "",
                "removed_name": cells[4] if cells[4] != "-" else "",
                "reason": cells[5],
            }
        )
    frame = pd.DataFrame(records)
    if frame.empty:
        return frame
    frame = frame.dropna(subset=["change_date"]).sort_values("change_date")
    frame["change_date"] = frame["change_date"].dt.date.astype(str)
    return frame


def build_membership(current: pd.DataFrame, changes: pd.DataFrame) -> pd.DataFrame:
    current = current.copy()
    current["symbol"] = current["symbol"].astype(str)
    today = date.today().isoformat()
    current_records = []
    change_records = []

    added_dates = {}
    if not changes.empty:
        added_dates = (
            changes[changes["added_symbol"].astype(bool)]
            .sort_values("change_date")
            .drop_duplicates("added_symbol", keep="last")
            .set_index("added_symbol")["change_date"]
            .to_dict()
        )

    for row in current.to_dict(orient="records"):
        symbol = row["symbol"]
        current_records.append(
            {
                "symbol": symbol,
                "name": row.get("name", ""),
                "sector": row.get("sector", ""),
                "sub_industry": row.get("sub_industry", ""),
                "start_date": added_dates.get(symbol, ""),
                "end_date": "",
                "membership_status": "current",
                "source": "wikipedia_current_constituents_and_changes",
                "quality_flag": "start_date_from_changes" if added_dates.get(symbol) else "start_date_unknown_current_member",
                "change_reason": "",
                "as_of_date": today,
            }
        )

    if not changes.empty:
        for row in changes.to_dict(orient="records"):
            if row.get("removed_symbol"):
                change_records.append(
                    {
                        "symbol": row["removed_symbol"],
                        "name": row.get("removed_name", ""),
                        "sector": "",
                        "sub_industry": "",
                        "start_date": "",
                        "end_date": row["change_date"],
                        "membership_status": "removed_from_index",
                        "source": "wikipedia_changes_table",
                        "quality_flag": "removed_symbol_no_complete_interval",
                        "change_reason": row.get("reason", ""),
                        "as_of_date": today,
                    }
                )
    membership = pd.DataFrame([*current_records, *change_records])
    return membership.sort_values(["membership_status", "symbol"]).reset_index(drop=True)


def main() -> None:
    args = parse_args()
    html = fetch_text("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
    current = fetch_sp500_constituents()
    changes = parse_changes(html)
    membership = build_membership(current, changes)

    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    output_path = REFERENCE_DIR / args.output_name
    changes_path = REFERENCE_DIR / "sp500_index_changes.csv"
    metadata_path = REFERENCE_DIR / "sp500_point_in_time_membership_metadata.json"
    membership.to_csv(output_path, index=False)
    changes.to_csv(changes_path, index=False)
    write_json(
        {
            "generated_at": pd.Timestamp.now("UTC").isoformat(),
            "source_url": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            "membership_path": str(output_path.relative_to(ROOT)),
            "changes_path": str(changes_path.relative_to(ROOT)),
            "current_member_count": int((membership["membership_status"] == "current").sum()),
            "removed_change_count": int((membership["membership_status"] == "removed_from_index").sum()),
            "current_members_with_known_start_date": int(
                ((membership["membership_status"] == "current") & membership["start_date"].astype(bool)).sum()
            ),
            "notes": [
                "This is a best-effort free reference, not a commercial-grade point-in-time index file.",
                "Current S&P 500 members are reliable, but many historical removed members do not have complete start/end intervals or sector metadata.",
                "Use this artifact for bias audits and future experiments; do not force removed names into model training until coverage and price data pass validation.",
            ],
        },
        metadata_path,
    )
    print(
        f"Wrote {len(membership)} membership rows and {len(changes)} change rows to {output_path.relative_to(ROOT)}"
    )


if __name__ == "__main__":
    main()
