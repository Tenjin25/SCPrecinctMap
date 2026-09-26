#!/usr/bin/env python3
"""Trace 2010 House benchmark exceptions to precinct and county allocations."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

from aggregate_contests_to_vtd20_crosswalks import norm
from rebuild_district_contests_from_current_geojson import FIELDS, allocate_integer


ROOT = Path(__file__).resolve().parents[1]
STAGING = ROOT / "data/district_contests_county_constrained_staging"
CASES = (("governor", "15"), ("superintendent", "15"),
         ("superintendent", "78"), ("governor", "74"),
         ("superintendent", "74"))


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def selected_weights() -> tuple[dict[str, dict[str, float]], dict[str, str], dict]:
    scope = "state_house_2022"
    canonical = read_json(ROOT / "data/crosswalk/current_precinct_to_district_weights.json")["scopes"][scope]["weights"]
    block = read_json(ROOT / "data/crosswalk/current_precinct_to_district_vap_weights.json")["scopes"][scope]["weights"]
    by_current = {}
    for key, shares in canonical.items():
        material = {district: float(share) for district, share in shares.items() if float(share) > .001}
        if not material:
            material = {max(shares, key=shares.get): 1.0}
        total = sum(material.values())
        by_current[norm(key)] = {district: share / total for district, share in material.items()}
    by_current.update({norm(key): shares for key, shares in block.items()})

    weights = {}
    provenance = {}
    for feature in read_json(ROOT / "data/Voting_Precincts.geojson")["features"]:
        props = feature["properties"]
        current = norm(props.get("precinct_norm"))
        county = str(props.get("county_nam") or "").strip()
        for alias in (current, props.get("precinct_display_name"),
                      f"{county} - {str(props.get('precinct_full_name') or '').strip()}",
                      f"{county} - {str(props.get('prec_id') or '').strip()}"):
            if norm(alias) and current in by_current:
                weights[norm(alias)] = by_current[current]
                provenance[norm(alias)] = current
    qa = read_json(STAGING / "current_geojson_qa.json")
    for alias, current in qa["punctuation_aliases_2010_2024"].items():
        weights[norm(alias)] = by_current[norm(current)]
        provenance[norm(alias)] = f"punctuation: {current}"
    bridge = read_json(ROOT / "data/crosswalk/reviewed_vtd_name_bridge_2010_2024.json")
    for alias, current_shares in bridge["weights"].items():
        if 2010 not in bridge["allowed_years"].get(alias, []):
            continue
        key = norm(alias)
        if key in weights:
            continue
        district_shares = defaultdict(float)
        for current, share in current_shares.items():
            for district, district_share in by_current[norm(current)].items():
                district_shares[district] += float(share) * float(district_share)
        weights[key] = dict(district_shares)
        provenance[key] = "reviewed VTD10 bridge: " + ", ".join(current_shares)
    return weights, provenance, by_current


def dra_margin(contest: str, district: str) -> float:
    label = {"governor": "gov", "superintendent": "supt"}[contest]
    path = ROOT / "data/2022 state house files" / f"district-statistics 2010 {label}.csv"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["ID"].strip() == district:
                return round((float(row["Rep"]) - float(row["Dem"])) * 100, 4)
    raise ValueError(f"Missing DRA district {district}: {path}")


def trace_case(contest_type: str, district: str, weights: dict, provenance: dict,
               whole: dict, by_current: dict, vtd10: dict) -> dict:
    contest = read_json(ROOT / "data/contests_2025_crosswalked" / f"{contest_type}_2010.json")
    live = read_json(ROOT / "data/district_contests/state_house_2022_lines" /
                     f"state_house_{contest_type}_2010_2022_lines.json")
    county_rows = {norm(row["county"]): row for row in contest["rows"] if " - " not in row["county"]}
    by_county = defaultdict(list)
    for row in contest["rows"]:
        if " - " in row["county"]:
            by_county[norm(row["county"].split(" - ", 1)[0])].append(row)
    components = []
    allocated_total = {field: 0 for field in FIELDS}
    for county in sorted(set(by_county) | set(county_rows)):
        rows = by_county[county]
        official = county_rows.get(county)
        target = {field: int(official.get(field) or 0) if official else
                  sum(int(row.get(field) or 0) for row in rows) for field in FIELDS}
        if county in whole:
            if whole[county] == district:
                allocated_total = {field: allocated_total[field] + target[field] for field in FIELDS}
                components.append({"county": county, "kind": "whole", "official": target})
            continue
        estimates = {field: defaultdict(float) for field in FIELDS}
        county_geo = defaultdict(float)
        unmatched = []
        source_rows = []
        for row in rows:
            source = norm(row["county"])
            shares = weights.get(source)
            if shares:
                for dest, share in shares.items():
                    county_geo[dest] += int(row.get("total_votes") or 0) * float(share)
            else:
                unmatched.append(row)
        denominator = sum(county_geo.values())
        fallback_share = {dest: amount / denominator for dest, amount in county_geo.items()} if denominator else {}
        for row in rows:
            source = norm(row["county"])
            shares = weights.get(source, fallback_share)
            if not shares:
                continue
            share = float(shares.get(district, 0))
            vtd = vtd10.get(row["county"])
            vtd_share = (sum(float(current_share) * float(by_current.get(norm(current), {}).get(district, 0))
                             for current, current_share in vtd.items())
                         if isinstance(vtd, dict) else None)
            if share > 0 or (vtd_share is not None and vtd_share > 0):
                source_rows.append({"source_precinct": source, "mapping": provenance.get(source, "county fallback"),
                                    "district_share": round(share, 6),
                                    "vtd10_district_share": round(vtd_share, 6) if vtd_share is not None else None,
                                    "vtd10_minus_current_vote_shift": round((vtd_share - share) * int(row.get("total_votes") or 0), 3)
                                    if vtd_share is not None else None,
                                    "source_votes": {field: int(row.get(field) or 0) for field in FIELDS},
                                    "estimated_district_votes": {field: round(int(row.get(field) or 0) * share, 3)
                                                                 for field in FIELDS}})
            for field in FIELDS:
                for dest, value in shares.items():
                    estimates[field][dest] += int(row.get(field) or 0) * float(value)
        allocation = {}
        for field in FIELDS:
            if target[field]:
                shares = estimates[field] if sum(estimates[field].values()) > 0 else county_geo
                allocation[field] = allocate_integer(target[field], shares).get(district, 0)
            else:
                allocation[field] = 0
            allocated_total[field] += allocation[field]
        if not any(allocation.values()):
            continue
            source_rows.sort(key=lambda row: sum(row["estimated_district_votes"].values()), reverse=True)
        estimated = {field: sum(row["estimated_district_votes"][field] for row in source_rows) for field in FIELDS}
        components.append({"county": county, "kind": "split", "official": target,
                           "source_precinct_sum": {field: sum(int(row.get(field) or 0) for row in rows)
                                                   for field in FIELDS},
                           "unmatched_precincts": [{"name": norm(row["county"]),
                                                     "votes": int(row.get("total_votes") or 0)} for row in unmatched],
                           "fallback_district_share": round(fallback_share.get(district, 0), 6),
                           "estimated_district_votes": {field: round(estimated[field], 3) for field in FIELDS},
                           "allocated_district_votes": allocation,
                           "reconciliation_change": {field: round(allocation[field] - estimated[field], 3)
                                                     for field in FIELDS},
                           "source_rows": source_rows})
    actual = live["general"]["results"][district]
    if any(allocated_total[field] != int(actual[field]) for field in FIELDS):
        raise ValueError(f"Trace does not reproduce live result: {contest_type} {district}: {allocated_total}")
    diagnostic = {
        "source_precinct_votes_allocated_to_district_before_county_reconciliation": round(
            sum(sum(component.get("estimated_district_votes", {}).values()) for component in components), 3),
        "county_reconciliation_votes_added_to_district": {
            field: round(sum(component.get("reconciliation_change", {}).get(field, 0) for component in components), 3)
            for field in FIELDS},
        "source_rows_with_vtd10_footprint": sum(row["vtd10_district_share"] is not None
                                                for component in components for row in component.get("source_rows", [])),
        "source_rows_without_vtd10_footprint": sum(row["vtd10_district_share"] is None
                                                   for component in components for row in component.get("source_rows", [])),
        "absolute_vtd10_vote_shift_where_available": round(
            sum(abs(row["vtd10_minus_current_vote_shift"] or 0)
                for component in components for row in component.get("source_rows", [])), 3),
    }
    return {"contest": contest_type, "district": district,
            "dra_republican_margin_pp": dra_margin(contest_type, district),
            "live_republican_margin_pp": actual["margin_pct"],
            "live_votes": allocated_total, "diagnostic": diagnostic, "components": components}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    weights, provenance, by_current = selected_weights()
    vtd10 = read_json(ROOT / "data/crosswalk/vtd10_to_2025_vote_weight_splits.json")
    whole = read_json(STAGING / "mixed_county_components.json")["plans"]["state_house_2022"]["whole_county_destinations"]
    cases = [trace_case(contest, district, weights, provenance, whole, by_current, vtd10)
             for contest, district in CASES]
    report = {"method": "Reproduce the served 2010 2022-line House allocations from source precinct weights, county fallback and official county-row party reconciliation. Compare available VTD10 footprint weights as a diagnostic only. DRA margins are independent estimates, not official returns.",
              "cases": cases}
    if args.write:
        path = STAGING / "house_source_exceptions_2010.json"
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for case in cases:
        print(f"{case['contest']} HD{case['district']}: live R margin {case['live_republican_margin_pp']:+.3f}pp; DRA {case['dra_republican_margin_pp']:+.3f}pp")
        for component in case["components"]:
            print(f"  {component['county']}: {component['kind']}, {component.get('allocated_district_votes', component.get('official'))}, unmatched {component.get('unmatched_precincts', [])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
