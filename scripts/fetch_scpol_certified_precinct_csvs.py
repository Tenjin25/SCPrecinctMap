#!/usr/bin/env python3
"""Fetch SCPOL's normalized 2026 ENR feed and write reproducible wide CSVs.

The output matches the CSV download shape used by elections.scpol.info.  By
default the script refuses to publish while any county feed is uncertified.
State Senate is intentionally excluded from the 2026 refresh.
"""

from __future__ import annotations

import argparse
import csv
import json
import urllib.request
from collections import OrderedDict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = "https://elections.scpol.info/2026/normalized-enr.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data/2026_precinct_results"

SINGLE_RACES = OrderedDict(
    (
        ("Governor and Lieutenant Governor", "sc-2026-governor-and-lieutenant-governor-precincts.csv"),
        ("Secretary of State", "sc-2026-secretary-of-state-precincts.csv"),
        ("State Treasurer", "sc-2026-state-treasurer-precincts.csv"),
        ("Attorney General", "sc-2026-attorney-general-precincts.csv"),
        ("Comptroller General", "sc-2026-comptroller-general-precincts.csv"),
        (
            "State Superintendent of Education",
            "sc-2026-state-superintendent-of-education-precincts.csv",
        ),
        ("Commissioner of Agriculture", "sc-2026-commissioner-of-agriculture-precincts.csv"),
        ("U.S. Senate", "sc-2026-u-s-senate-precincts.csv"),
    )
)

GROUP_RACES = (
    ("us_house", "sc-2026-all-u-s-house-precincts.csv"),
    ("sc_house", "sc-2026-all-state-house-precincts.csv"),
)


def fetch_json(url: str) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        headers={"Cache-Control": "no-cache", "User-Agent": "SCPrecinctMap data refresh"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def candidate_label(candidate: dict[str, object]) -> str:
    name = str(candidate.get("name") or "").strip()
    party = str(candidate.get("party") or "").strip()
    if name.casefold() == "write-in":
        return "Write-In (NON)"
    return f"{name} ({party})"


def select_races(feed: dict[str, object]) -> list[tuple[str, list[dict[str, object]]]]:
    races = list(dict(feed.get("races") or {}).values())
    by_title = {str(race.get("title") or ""): race for race in races}
    selected: list[tuple[str, list[dict[str, object]]]] = []
    for title, filename in SINGLE_RACES.items():
        if title not in by_title:
            raise ValueError(f"feed is missing required race {title!r}")
        selected.append((filename, [by_title[title]]))
    for race_type, filename in GROUP_RACES:
        group = sorted(
            (race for race in races if race.get("type") == race_type),
            key=lambda race: int(str(race.get("id") or 0)),
        )
        if not group:
            raise ValueError(f"feed is missing required race type {race_type!r}")
        selected.append((filename, group))
    return selected


def write_wide_csv(
    output_path: Path,
    race_group: list[dict[str, object]],
    precincts: list[dict[str, object]],
) -> dict[str, int]:
    candidate_fields: list[str] = []
    for race in race_group:
        for candidate in list(race.get("candidates") or []):
            label = candidate_label(candidate)
            if label not in candidate_fields:
                candidate_fields.append(label)

    fieldnames = [
        "County",
        "Precinct",
        "Contest ID",
        "Contest",
        "Race Type",
        *candidate_fields,
        "Contest Total Votes",
    ]
    rows: list[dict[str, object]] = []
    votes_total = 0
    for precinct in sorted(
        precincts,
        key=lambda item: (str(item.get("county") or ""), str(item.get("name") or "")),
    ):
        precinct_votes = dict(precinct.get("votes") or {})
        for race in race_group:
            race_id = str(race.get("id") or "")
            if race_id not in precinct_votes:
                continue
            vote_values = list(precinct_votes[race_id] or [])
            candidates = list(race.get("candidates") or [])
            if len(vote_values) != len(candidates):
                raise ValueError(
                    f"{precinct.get('county')} / {precinct.get('name')} race {race_id}: "
                    f"{len(vote_values)} vote values for {len(candidates)} candidates"
                )
            row: dict[str, object] = {
                "County": precinct.get("county") or "",
                "Precinct": precinct.get("name") or "",
                "Contest ID": race_id,
                "Contest": race.get("title") or "",
                "Race Type": race.get("type") or "",
            }
            for candidate, votes in zip(candidates, vote_values):
                row[candidate_label(candidate)] = int(votes)
            row_total = sum(int(value) for value in vote_values)
            row["Contest Total Votes"] = row_total
            votes_total += row_total
            rows.append(row)

    if not rows:
        raise ValueError(f"no precinct rows found for {output_path.name}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            extrasaction="raise",
            lineterminator="\n",
            quoting=csv.QUOTE_ALL,
        )
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(output_path)
    return {"rows": len(rows), "votes": votes_total, "contests": len(race_group)}


def publish(feed: dict[str, object], output_dir: Path, *, allow_uncertified: bool = False) -> dict[str, object]:
    source_metadata = dict(feed.get("sourceMetadata") or {})
    county_metadata = dict(source_metadata.get("counties") or {})
    uncertified = sorted(
        county for county, metadata in county_metadata.items() if not dict(metadata or {}).get("certified")
    )
    if len(county_metadata) != 46:
        raise ValueError(f"expected 46 county metadata records, found {len(county_metadata)}")
    if uncertified and not allow_uncertified:
        raise ValueError(f"uncertified county feeds: {', '.join(uncertified)}")

    precincts = list(dict(feed.get("precincts") or {}).values())
    stats: dict[str, dict[str, int]] = {}
    for filename, race_group in select_races(feed):
        stats[filename] = write_wide_csv(output_dir / filename, race_group, precincts)

    metadata = {
        "source_url": DEFAULT_URL,
        "format_version": feed.get("formatVersion"),
        "election_id": source_metadata.get("electionId"),
        "statewide_results_version": source_metadata.get("statewideResultsVersion"),
        "fetched_at_utc": source_metadata.get("fetchedAtUtc"),
        "last_updated_utc": source_metadata.get("lastUpdatedUtc"),
        "source": source_metadata.get("source"),
        "county_count": len(county_metadata),
        "certified_county_count": len(county_metadata) - len(uncertified),
        "uncertified_counties": uncertified,
        "outputs": stats,
        "counties": county_metadata,
    }
    metadata_path = output_dir / "certified_feed_metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--input-json", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--allow-uncertified", action="store_true")
    args = parser.parse_args()
    feed = json.loads(args.input_json.read_text(encoding="utf-8")) if args.input_json else fetch_json(args.url)
    metadata = publish(feed, args.output_dir.resolve(), allow_uncertified=args.allow_uncertified)
    print(
        f"published {len(metadata['outputs'])} CSVs from "
        f"{metadata['certified_county_count']}/{metadata['county_count']} certified counties"
    )
    for filename, stats in metadata["outputs"].items():
        print(
            f"  {filename}: {stats['contests']} contest(s), "
            f"{stats['rows']} rows, {stats['votes']} votes"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
