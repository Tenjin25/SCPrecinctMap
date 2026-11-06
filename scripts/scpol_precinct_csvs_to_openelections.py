#!/usr/bin/env python3
"""Merge SC Political Data precinct exports into the 2026 OpenElections CSV.

The source files are wide (one candidate per column).  This converter writes
one row per candidate and precinct while preserving contests that are present
only in the existing SC Election Commission county-summary conversion.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = REPO_ROOT / "data/2026_precinct_results"
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

SOURCE_FIELDS = {"County", "Precinct", "Contest ID", "Contest", "Race Type"}
TOTAL_FIELD = "Contest Total Votes"
PARTY_CANDIDATE = re.compile(r"^([A-Z]{3})\s+(.+?)\s+\(([A-Z]{3})\)$")
WRITE_IN = re.compile(r"^Write-In\s+\(NON\)$", re.IGNORECASE)
STATE_HOUSE_CONTEST = re.compile(
    r"^State House of Representatives, District\s+(\d+)$", re.IGNORECASE
)


def integer(value: object, *, context: str) -> int:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return 0
    try:
        return int(text)
    except ValueError as exc:
        raise ValueError(f"{context}: expected an integer, got {value!r}") from exc


def parse_candidate_header(header: str) -> tuple[str, str]:
    label = str(header or "").strip()
    if WRITE_IN.fullmatch(label):
        return "Write-In", ""
    match = PARTY_CANDIDATE.fullmatch(label)
    if not match:
        raise ValueError(f"unrecognized candidate column {label!r}")
    prefix, candidate, suffix = match.groups()
    if prefix != suffix:
        raise ValueError(f"party codes disagree in candidate column {label!r}")
    return candidate.strip(), prefix


def parse_contest(label: str) -> tuple[str, str]:
    contest = str(label or "").strip()
    state_house = STATE_HOUSE_CONTEST.fullmatch(contest)
    if state_house:
        return "State House", str(int(state_house.group(1)))
    return contest, ""


def read_source(path: Path) -> tuple[set[str], list[dict[str, object]], dict[str, int]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        missing = sorted((SOURCE_FIELDS | {TOTAL_FIELD}) - set(fieldnames))
        if missing:
            raise ValueError(f"{path.name}: missing columns {', '.join(missing)}")
        candidate_fields = [
            field for field in fieldnames if field not in SOURCE_FIELDS and field != TOTAL_FIELD
        ]
        parsed_candidates = [(field, *parse_candidate_header(field)) for field in candidate_fields]

        output_rows: list[dict[str, object]] = []
        offices: set[str] = set()
        contests: set[tuple[str, str]] = set()
        county_totals: dict[str, int] = defaultdict(int)
        source_rows = 0
        reconciled_rows = 0
        for source_rows, row in enumerate(reader, start=1):
            county = str(row.get("County") or "").strip()
            precinct = str(row.get("Precinct") or "").strip()
            contest_label = str(row.get("Contest") or "").strip()
            if not county or not precinct or not contest_label:
                raise ValueError(f"{path.name} row {source_rows + 1}: blank county, precinct, or contest")
            office, district = parse_contest(contest_label)
            offices.add(office)
            contests.add((office, district))

            candidate_total = 0
            for field, candidate, party in parsed_candidates:
                raw_votes = row.get(field)
                # The all-State-House export has one column for every candidate
                # statewide. Blank cells belong to a different district; a
                # populated zero still belongs to this row's contest.
                if raw_votes is None or str(raw_votes).strip() == "":
                    continue
                votes = integer(row.get(field), context=f"{path.name} row {source_rows + 1} {field}")
                candidate_total += votes
                output_rows.append(
                    {
                        "county": county,
                        "precinct": precinct,
                        "office": office,
                        "district": district,
                        "candidate": candidate,
                        "party": party,
                        "votes": votes,
                        # Voting-method detail is unavailable in the precinct exports.
                        "early_voting": "",
                        "failsafe_provisional": "",
                        "provisional": "",
                        "failsafe": "",
                        "absentee_by_mail": "",
                        "election_day": "",
                    }
                )

            reported_total = integer(
                row.get(TOTAL_FIELD), context=f"{path.name} row {source_rows + 1} {TOTAL_FIELD}"
            )
            if candidate_total != reported_total:
                raise ValueError(
                    f"{path.name} row {source_rows + 1}: candidate total {candidate_total} "
                    f"does not match reported total {reported_total}"
                )
            county_totals[county] += reported_total
            reconciled_rows += 1

    if not offices or not output_rows:
        raise ValueError(f"{path.name}: no precinct results found")
    return offices, output_rows, {
        "source_rows": source_rows,
        "output_rows": len(output_rows),
        "reconciled_rows": reconciled_rows,
        "contests": len(contests),
        "counties": len(county_totals),
        "votes": sum(county_totals.values()),
    }


def read_existing(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = sorted(set(OUTPUT_FIELDS) - set(reader.fieldnames or []))
        if missing:
            raise ValueError(f"{path.name}: existing output is missing columns {', '.join(missing)}")
        return [{field: str(row.get(field) or "") for field in OUTPUT_FIELDS} for row in reader]


def convert(input_dir: Path, output_path: Path) -> dict[str, object]:
    source_paths = sorted(input_dir.glob("sc-2026-*-precincts.csv"))
    if not source_paths:
        raise FileNotFoundError(f"no SC Political Data precinct exports found in {input_dir}")

    replacement_rows: list[dict[str, object]] = []
    replaced_offices: set[str] = set()
    source_stats: dict[str, dict[str, int]] = {}
    expected_precinct_keys: set[tuple[str, str]] | None = None

    for path in source_paths:
        source_offices, rows, stats = read_source(path)
        duplicate_offices = source_offices & replaced_offices
        if duplicate_offices:
            raise ValueError(f"duplicate source office(s): {', '.join(sorted(duplicate_offices))}")
        keys = {(str(row["county"]), str(row["precinct"])) for row in rows}
        if expected_precinct_keys is None:
            expected_precinct_keys = keys
        elif keys != expected_precinct_keys:
            missing = len(expected_precinct_keys - keys)
            extra = len(keys - expected_precinct_keys)
            raise ValueError(
                f"{path.name}: precinct coverage differs from the other exports "
                f"({missing} missing, {extra} extra)"
            )
        replaced_offices.update(source_offices)
        replacement_rows.extend(rows)
        for office in source_offices:
            source_stats[office] = stats

    existing_rows = read_existing(output_path)
    preserved_rows = [row for row in existing_rows if row.get("office") not in replaced_offices]
    combined_rows = [*preserved_rows, *replacement_rows]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(combined_rows)

    return {
        "sources": len(source_paths),
        "replaced_offices": sorted(replaced_offices),
        "precinct_reporting_units": len(expected_precinct_keys or set()),
        "preserved_rows": len(preserved_rows),
        "replacement_rows": len(replacement_rows),
        "output_rows": len(combined_rows),
        "source_stats": source_stats,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    stats = convert(args.input_dir.resolve(), args.output.resolve())
    print(
        f"replaced {stats['sources']} statewide contests with "
        f"{stats['precinct_reporting_units']} precinct/reporting units each"
    )
    print(
        f"preserved {stats['preserved_rows']} existing rows; wrote "
        f"{stats['output_rows']} OpenElections rows to {args.output.resolve()}"
    )
    for office in stats["replaced_offices"]:
        contest = stats["source_stats"][office]
        print(
            f"  {office}: {contest['contests']} contest(s), {contest['source_rows']} source rows, "
            f"{contest['votes']} votes, {contest['counties']} counties"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
