#!/usr/bin/env python3
"""Convert the SC Election Commission county-summary workbook to OpenElections CSV."""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

from openpyxl import load_workbook


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = REPO_ROOT / "data/2026-11-03-12AM-General-Election-Results.xlsx"
DEFAULT_OUTPUT = REPO_ROOT / "data/20261103__sc__general__precinct.csv"

OUTPUT_FIELDS = (
    "county",
    "precinct",
    "office",
    "district",
    "candidate",
    "party",
    "votes",
    "early_voting",
    "failsafe_provisional",
    "provisional",
    "failsafe",
    "absentee_by_mail",
    "election_day",
)

METHOD_FIELDS = {
    "Election Day": "election_day",
    "Early Voting": "early_voting",
    "Absentee By Mail": "absentee_by_mail",
    "Failsafe": "failsafe",
    "Provisional": "provisional",
    "Failsafe Provisional": "failsafe_provisional",
    "Total Votes": "votes",
}

PARTY_CODES = {
    "CON",
    "DEM",
    "FOR",
    "GRN",
    "LIB",
    "REP",
    "UNC",
    "WRK",
}


def clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_contest(title: str) -> tuple[str, str]:
    normalized = clean_text(re.sub(r"\s*\(Vote For \d+\)\s*$", "", title, flags=re.I))
    district_patterns = (
        (r"^U\.S\. House of Representatives, District (\d+)$", "U.S. House"),
        (r"^State House of Representatives, District (\d+)$", "State House"),
        (r"^State Senate District (\d+)(?: \(Unexpired Term\))?$", "State Senate"),
    )
    for pattern, office in district_patterns:
        match = re.match(pattern, normalized, flags=re.I)
        if match:
            return office, str(int(match.group(1)))

    office_aliases = {
        "Governor and Lieutenant Governor": "Governor and Lieutenant Governor",
        "Secretary of State": "Secretary of State",
        "State Treasurer": "State Treasurer",
        "Attorney General": "Attorney General",
        "Comptroller General": "Comptroller General",
        "State Superintendent of Education": "State Superintendent of Education",
        "Commissioner of Agriculture": "Commissioner of Agriculture",
        "U.S. Senate": "U.S. Senate",
    }
    return office_aliases.get(normalized, normalized), ""


def parse_candidate(label: object) -> tuple[str, str]:
    text = clean_text(label)
    match = re.match(r"^([A-Z]{3})\s+(.+)$", text)
    if match and match.group(1) in PARTY_CODES:
        return clean_text(match.group(2)), match.group(1)
    return text, ""


def integer(value: object) -> int:
    if value in (None, ""):
        return 0
    return int(value)


def candidate_groups(sheet) -> list[tuple[str, str, dict[str, int]]]:
    groups = []
    for column in range(3, sheet.max_column + 1):
        label = sheet.cell(2, column).value
        if label in (None, ""):
            continue
        candidate, party = parse_candidate(label)
        methods = {}
        for method_column in range(column, min(column + 7, sheet.max_column + 1)):
            header = clean_text(sheet.cell(3, method_column).value)
            output_field = METHOD_FIELDS.get(header)
            if output_field:
                methods[output_field] = method_column
        if "votes" not in methods:
            raise ValueError(f"{sheet.title}: missing Total Votes column for {label!r}")
        groups.append((candidate, party, methods))
    return groups


def convert(input_path: Path, output_path: Path) -> dict[str, int]:
    # Normal mode is substantially faster here than repeatedly seeking through
    # 170+ worksheets in openpyxl's streaming reader.
    workbook = load_workbook(input_path, read_only=False, data_only=True)
    rows = []
    contest_count = 0
    reconciliation_rows = 0

    for sheet_name in workbook.sheetnames:
        if not sheet_name.isdigit():
            continue
        sheet = workbook[sheet_name]
        title = clean_text(sheet.cell(1, 1).value)
        if not title:
            continue
        office, district = parse_contest(title)
        groups = candidate_groups(sheet)
        if not groups:
            continue
        contest_count += 1

        final_total_column = None
        for column in range(1, sheet.max_column + 1):
            if clean_text(sheet.cell(3, column).value) == "Total":
                final_total_column = column

        for row_number in range(4, sheet.max_row + 1):
            county = clean_text(sheet.cell(row_number, 1).value)
            if not county or county.casefold() == "total:":
                continue
            candidate_total = 0
            for candidate, party, methods in groups:
                row = {field: 0 for field in OUTPUT_FIELDS}
                row.update(
                    county=county,
                    precinct="",
                    office=office,
                    district=district,
                    candidate=candidate,
                    party=party,
                )
                for output_field, column in methods.items():
                    row[output_field] = integer(sheet.cell(row_number, column).value)
                candidate_total += row["votes"]
                rows.append(row)

            if final_total_column is not None:
                reported_total = integer(sheet.cell(row_number, final_total_column).value)
                if candidate_total != reported_total:
                    raise ValueError(
                        f"{sheet_name} {county}: candidate total {candidate_total} "
                        f"does not match reported total {reported_total}"
                    )
                reconciliation_rows += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    return {
        "contests": contest_count,
        "rows": len(rows),
        "reconciled_county_contest_rows": reconciliation_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    stats = convert(args.input.resolve(), args.output.resolve())
    print(
        f"wrote {stats['rows']} rows across {stats['contests']} contests to "
        f"{args.output.resolve()}"
    )
    print(f"reconciled {stats['reconciled_county_contest_rows']} county/contest totals")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
