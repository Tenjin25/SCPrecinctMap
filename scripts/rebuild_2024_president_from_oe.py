#!/usr/bin/env python3
"""Rebuild exact statewide 2024 presidential results from OpenElections."""

import argparse
import csv
import json
import os
from collections import OrderedDict

from aggregate_contests_to_vtd20_crosswalks import finalize_row, norm


NON_GEOGRAPHIC_PREFIXES = ("FAILSAFE", "PROVISIONAL")
VOTE_FIELDS = ("dem_votes", "rep_votes", "other_votes")


def vote_bucket() -> dict:
    return {field: 0 for field in VOTE_FIELDS}


def add_vote(bucket: dict, party: str, votes: int) -> None:
    if party == "DEM":
        bucket["dem_votes"] += votes
    elif party == "REP":
        bucket["rep_votes"] += votes
    else:
        bucket["other_votes"] += votes


def make_row(key: str, votes: dict) -> dict:
    return finalize_row({
        "county": key,
        **votes,
        "dem_candidate": "Kamala D. Harris and Tim Walz",
        "rep_candidate": "Donald J. Trump and JD Vance",
    })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=".", help="Repository root")
    parser.add_argument("--input", default="data/20241105__sc__general__precinct_complete.csv")
    parser.add_argument("--contest", default="data/contests/president_2024.json")
    args = parser.parse_args()

    base = os.path.abspath(args.base)
    input_path = os.path.join(base, args.input)
    contest_path = os.path.join(base, args.contest)
    with open(contest_path, encoding="utf-8") as fh:
        payload = json.load(fh) or {}

    existing_counties = {
        norm(row.get("county")): str(row.get("county") or "").strip()
        for row in payload.get("rows") or []
        if " - " not in str(row.get("county") or "")
    }
    county_votes: OrderedDict[str, dict] = OrderedDict()
    precinct_votes: OrderedDict[tuple[str, str], dict] = OrderedDict()
    nongeo_votes: OrderedDict[str, dict] = OrderedDict()

    with open(input_path, encoding="utf-8-sig", newline="") as fh:
        for source in csv.DictReader(fh):
            if norm(source.get("office")) != "PRESIDENT":
                continue
            source_county = str(source.get("county") or "").strip()
            county = existing_counties.get(norm(source_county), source_county.title())
            precinct = str(source.get("precinct") or "").strip()
            party = norm(source.get("party"))
            votes = int(source.get("votes") or 0)
            add_vote(county_votes.setdefault(county, vote_bucket()), party, votes)
            if norm(precinct).startswith(NON_GEOGRAPHIC_PREFIXES):
                add_vote(nongeo_votes.setdefault(county, vote_bucket()), party, votes)
                continue
            add_vote(precinct_votes.setdefault((county, precinct), vote_bucket()), party, votes)

    if len(county_votes) != 46:
        raise SystemExit(f"Expected 46 counties, found {len(county_votes)}")

    county_rows = [make_row(county, votes) for county, votes in sorted(county_votes.items())]
    precinct_rows = [
        make_row(f"{county} - {precinct}", votes)
        for (county, precinct), votes in sorted(precinct_votes.items())
    ]
    payload["rows"] = county_rows + precinct_rows
    with open(contest_path, "w", encoding="utf-8", newline="") as fh:
        json.dump(payload, fh, separators=(",", ":"))

    county_total = sum(sum(v.values()) for v in county_votes.values())
    geographic_total = sum(sum(v.values()) for v in precinct_votes.values())
    nongeo_total = sum(sum(v.values()) for v in nongeo_votes.values())
    if geographic_total + nongeo_total != county_total:
        raise SystemExit("Geographic and non-geographic votes do not reconcile to county totals")
    print(f"rebuilt {len(precinct_rows):,} geographic precinct rows across {len(county_rows)} counties")
    print(f"geographic votes={geographic_total:,}; non-geographic votes={nongeo_total:,}; statewide total={county_total:,}")


if __name__ == "__main__":
    main()
