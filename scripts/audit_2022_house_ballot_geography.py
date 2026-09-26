#!/usr/bin/env python3
"""Compare 2022 House ballot districts with staged precinct geography.

The House ballot district field is an independent placement clue. House votes
are not a turnout-normalized estimate for allocating other contests.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

from aggregate_contests_to_vtd20_crosswalks import norm


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT.parent / "Data/_tmpdata/openelections-data-sc/2022/20221108__sc__general__precinct.csv"
NON_GEOGRAPHIC = ("EMERGENCY", "FAILSAFE", "PROVISIONAL", "ABSENTEE", "EARLY VOTING")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=RAW)
    parser.add_argument("--staging-root", default="data/district_contests_county_constrained_staging")
    parser.add_argument("--mapping", choices=("vtd20-reviewed", "vtd20", "current"), default="vtd20-reviewed",
                        help="Use the actual 2022 VTD20 source crosswalk or direct current-name matching for comparison")
    parser.add_argument("--office", choices=("State House", "U.S. House"), default="State House")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    if not args.raw.exists():
        raise FileNotFoundError(f"Local 2022 precinct returns not found: {args.raw}")
    staging = ROOT / args.staging_root
    geojson = json.loads((ROOT / "data/Voting_Precincts.geojson").read_text(encoding="utf-8"))
    alias_to_current = {}
    for feature in geojson["features"]:
        props = feature["properties"]
        current = norm(props.get("precinct_norm"))
        county = str(props.get("county_nam") or "").strip()
        for value in (current, props.get("precinct_display_name"),
                      f"{county} - {str(props.get('precinct_full_name') or '').strip()}",
                      f"{county} - {str(props.get('prec_id') or '').strip()}"):
            if norm(value):
                alias_to_current[norm(value)] = current
    qa = json.loads((staging / "current_geojson_qa.json").read_text(encoding="utf-8"))
    alias_to_current.update(qa["punctuation_aliases_2010_2024"])
    bridge = json.loads((ROOT / "data/crosswalk/reviewed_vtd_name_bridge_2010_2024.json").read_text(encoding="utf-8"))
    vtd20 = json.loads((ROOT / "data/crosswalk/vtd20_to_2025_vote_weight_splits.json").read_text(encoding="utf-8"))
    vtd20 = {norm(source): shares for source, shares in vtd20.items() if not source.startswith("_")}
    reviewed = json.loads((ROOT / "data/crosswalk/reviewed_2022_house_ballot_source_overrides.json").read_text(encoding="utf-8"))
    reviewed = {norm(source): shares for source, shares in reviewed.items() if not source.startswith("_")}
    aliases = json.loads((ROOT / "precinct_aliases.json").read_text(encoding="utf-8"))
    aliases = {norm(source): norm(target) for source, target in aliases.items() if not source.startswith("_")}
    area = json.loads((ROOT / "data/crosswalk/current_precinct_to_district_weights.json").read_text(encoding="utf-8"))
    block = json.loads((ROOT / "data/crosswalk/current_precinct_to_district_vap_weights.json").read_text(encoding="utf-8"))
    scope = "state_house_2022" if args.office == "State House" else "congressional"
    weights = {norm(key): shares for key, shares in area["scopes"][scope]["weights"].items()}
    weights.update({norm(key): shares for key, shares in block["scopes"][scope]["weights"].items()})
    ballots = defaultdict(lambda: defaultdict(int))
    with args.raw.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["office"] != args.office or not row["district"].strip().isdigit():
                continue
            source = norm(f"{row['county']} - {row['precinct']}")
            ballots[source][str(int(row["district"]))] += int(row["votes"] or 0)
    records = []
    unmatched = []
    for source, by_district in sorted(ballots.items()):
        if any(label in source.split(" - ", 1)[-1] for label in NON_GEOGRAPHIC):
            continue
        current = alias_to_current.get(source)
        direct_shares = ({current: 1.0} if current else
                         bridge["weights"].get(source) if 2022 in bridge.get("allowed_years", {}).get(source, []) else None)
        alias_key = aliases.get(source)
        alias_shares = (({alias_to_current[alias_key]: 1.0} if alias_key in alias_to_current else
                         vtd20.get(alias_key)) if alias_key else None)
        current_shares = (reviewed.get(source) if args.mapping == "vtd20-reviewed" else None)
        if not current_shares:
            current_shares = ((vtd20.get(source) or alias_shares or direct_shares)
                              if args.mapping != "current" else direct_shares)
        if not current_shares:
            unmatched.append({"source_precinct": source, "house_votes": sum(by_district.values())})
            continue
        expected = defaultdict(float)
        for current_key, share in current_shares.items():
            for district, district_share in weights[norm(current_key)].items():
                expected[district] += float(share) * float(district_share)
        observed = {district: votes for district, votes in by_district.items() if votes > 0}
        total = sum(observed.values())
        if not total:
            continue
        ballot_primary = max(observed, key=observed.get)
        expected_primary = max(expected, key=expected.get)
        unsupported_votes = sum(votes for district, votes in observed.items()
                                if expected.get(district, 0.0) < 0.01)
        records.append({"source_precinct": source, "house_votes": total,
                        "ballot_district_votes": observed,
                        "expected_district_weights": {key: round(value, 6) for key, value in sorted(expected.items())},
                        "ballot_primary": ballot_primary, "expected_primary": expected_primary,
                        "primary_matches": ballot_primary == expected_primary,
                        "unsupported_ballot_votes": unsupported_votes,
                        "unsupported_ballot_share": round(unsupported_votes / total, 4)})
    meaningful = [row for row in records if row["house_votes"] >= 100]
    report = {"method": f"Compare 2022 {args.office} ballot district labels with source precincts mapped through the selected crosswalk, then current precinct-to-{scope} block/area weights; non-geographic labels excluded. Ballot vote shares are not direct crosswalk weights.",
              "office": args.office,
              "mapping": args.mapping,
              "source": str(args.raw),
              "summary": {"ballot_precincts": len(ballots), "matched_geographic_precincts": len(records),
                          "unmatched_geographic_precincts": len(unmatched),
                          "meaningful_precincts_100_votes": len(meaningful),
                          "primary_matches_100_votes": sum(row["primary_matches"] for row in meaningful),
                          "matched_house_votes": sum(row["house_votes"] for row in records),
                          "unmatched_house_votes": sum(row["house_votes"] for row in unmatched),
                          "unsupported_house_votes_100_votes": sum(row["unsupported_ballot_votes"] for row in meaningful),
                          "high_unsupported_precincts_100_votes": sum(row["unsupported_ballot_share"] >= 0.1
                                                                    for row in meaningful)},
              "largest_unsupported": sorted(meaningful,
                                            key=lambda row: (row["unsupported_ballot_votes"], row["house_votes"]),
                                            reverse=True)[:50],
              "primary_mismatches": [row for row in meaningful if not row["primary_matches"]],
              "unmatched": sorted(unmatched, key=lambda row: -row["house_votes"])[:100]}
    if args.write:
        name = "house_ballot_geography_2022.json" if args.office == "State House" else "congress_ballot_geography_2022.json"
        (staging / name).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
