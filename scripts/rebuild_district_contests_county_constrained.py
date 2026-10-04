#!/usr/bin/env python3
"""Build NC-style county-constrained district results from SC precinct overlaps.

Whole counties contribute their official county returns. Split counties use
precinct overlap estimates, then reconcile each party to the official county
return before district rounding. A 0.1% county-area tolerance ignores slivers.
The existing overlap builder remains available as a separate method.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from collections import defaultdict
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform

import rebuild_district_contests_from_current_geojson as base
from aggregate_contests_to_vtd20_crosswalks import norm


ROOT = Path(__file__).resolve().parents[1]
FIELDS = base.FIELDS
FULL_COUNTY_THRESHOLD = 0.999
MATERIAL_PARTIAL_THRESHOLD = 0.001
MAX_WHOLE_COUNTY_OUTSIDE_CVAP_SHARE = 0.001
SLIVER_AUDIT_FILE = "data/crosswalk/current_precinct_district_sliver_population_audit.json"
POPULATED_SLIVER_SCOPES = {"congressional", "state_senate_2022"}


def punctuation_key(value: str) -> str:
    """Ignore punctuation, but retain county and every alphanumeric token."""
    return " ".join(re.findall(r"[A-Z0-9]+", norm(value)))


def precinct_areas(geojson: dict) -> dict[str, float]:
    project = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    return {
        norm(feature["properties"].get("precinct_norm")): transform(project.transform, shape(feature["geometry"])).area
        for feature in geojson.get("features", [])
        if feature.get("geometry") and feature.get("properties", {}).get("precinct_norm")
    }


def whole_county_destinations(weights: dict[str, dict[str, float]], areas: dict[str, float],
                              threshold: float = FULL_COUNTY_THRESHOLD) -> dict[str, str]:
    county_district_area: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    county_area: dict[str, float] = defaultdict(float)
    for precinct, area in areas.items():
        if " - " not in precinct:
            continue
        county = precinct.split(" - ", 1)[0]
        shares = weights.get(precinct)
        if not shares:
            raise ValueError(f"Missing district weights for {precinct}")
        county_area[county] += area
        for district, share in shares.items():
            county_district_area[county][district] += area * float(share)
    out = {}
    for county, districts in county_district_area.items():
        district, area = max(districts.items(), key=lambda item: item[1])
        if county_area[county] > 0 and area / county_area[county] >= threshold:
            out[county] = district
    return out


def mixed_county_components(weights: dict[str, dict[str, float]], areas: dict[str, float],
                            whole: dict[str, str], population_split_destinations: dict[str, set[str]] | None = None) -> dict:
    """Catalog exact and material partial counties in each mixed district."""
    county_area: dict[str, float] = defaultdict(float)
    intersections: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for precinct, area in areas.items():
        if " - " not in precinct:
            continue
        county = precinct.split(" - ", 1)[0]
        county_area[county] += area
        for district, share in weights[precinct].items():
            intersections[county][district] += area * float(share)
    exact: dict[str, list[str]] = defaultdict(list)
    allocated: dict[str, list[str]] = defaultdict(list)
    for county, districts in intersections.items():
        for district, area in districts.items():
            share = area / county_area[county]
            if whole.get(county) == district:
                exact[district].append(county)
            elif share > MATERIAL_PARTIAL_THRESHOLD or district in (population_split_destinations or {}).get(county, set()):
                allocated[district].append(county)
    mixed = set(exact) & set(allocated)
    order = base.build_data._district_sort_key
    return {
        "exact_components": {district: sorted(exact[district]) for district in sorted(mixed, key=order)},
        "allocated_components": {district: sorted(allocated[district]) for district in sorted(mixed, key=order)},
    }


def guard_whole_counties_by_population(whole: dict[str, str], scope: str, audit: dict,
                                      county_fips: dict[str, str]) -> tuple[dict[str, str], list[dict]]:
    """Treat a near-whole county as split when its omitted piece has material CVAP."""
    outside = defaultdict(lambda: defaultdict(float))
    for entry in audit.get("entries", []):
        if entry.get("scope") != scope:
            continue
        county = str(entry.get("precinct") or "").split(" - ", 1)[0]
        destination = whole.get(county)
        if not destination:
            continue
        for district, value in entry.get("candidate_cvap", {}).items():
            if district != destination:
                outside[county][district] += float(value)
    guarded = dict(whole)
    exceptions = []
    county_totals = audit.get("county_cvap_totals_by_fips") or {}
    for county, districts in sorted(outside.items()):
        population = sum(districts.values())
        total = float(county_totals.get(county_fips.get(county, "")) or 0)
        if total <= 0:
            raise ValueError(f"No county block population for {county}")
        share = population / total
        if share > MAX_WHOLE_COUNTY_OUTSIDE_CVAP_SHARE:
            exceptions.append({"county": county, "would_be_whole_district": guarded.pop(county),
                               "outside_cvap": round(population, 4),
                               "outside_district_cvap": {district: round(value, 4)
                                                        for district, value in sorted(districts.items()) if value >= 1},
                               "county_cvap": round(total, 4), "outside_cvap_share": round(share, 8)})
    return guarded, exceptions


def rebuild_one(contest: dict, weights: dict[str, dict[str, float]], whole: dict[str, str],
                scope: str, district_file: str, weight_source: str,
                emergency_by_party: bool = False) -> tuple[dict, dict]:
    rows = contest.get("rows") or []
    county_rows = {norm(row["county"]): row for row in rows if " - " not in str(row.get("county") or "")}
    precinct_rows = [row for row in rows if " - " in str(row.get("county") or "")]
    by_county: dict[str, list[dict]] = defaultdict(list)
    for row in precinct_rows:
        by_county[norm(row["county"].split(" - ", 1)[0])].append(row)

    district_votes: dict[str, dict[str, int]] = defaultdict(lambda: {field: 0 for field in FIELDS})
    exact_votes: dict[str, dict[str, int]] = defaultdict(lambda: {field: 0 for field in FIELDS})
    matched = fallback_rows = fallback_votes = reconciled = emergency_rows = emergency_votes = 0
    dem_candidate = rep_candidate = ""
    for county in sorted(set(by_county) | set(county_rows)):
        source = county_rows.get(county)
        precincts = by_county.get(county, [])
        target = {field: int(source.get(field) or 0) if source else sum(int(row.get(field) or 0) for row in precincts)
                  for field in FIELDS}
        dem_candidate = dem_candidate or str((source or (precincts[0] if precincts else {})).get("dem_candidate") or "")
        rep_candidate = rep_candidate or str((source or (precincts[0] if precincts else {})).get("rep_candidate") or "")

        if county in whole:
            destination = whole[county]
            for field in FIELDS:
                district_votes[destination][field] += target[field]
                exact_votes[destination][field] += target[field]
            matched += sum(bool(weights.get(norm(row["county"]))) for row in precincts)
            continue

        estimates: dict[str, dict[str, float]] = {field: defaultdict(float) for field in FIELDS}
        county_geography: dict[str, float] = defaultdict(float)
        unmatched = []
        for row in precincts:
            shares = weights.get(norm(row["county"]))
            if not shares:
                if emergency_by_party and norm(row["county"]).split(" - ", 1)[-1] == "EMERGENCY":
                    emergency_rows += 1
                    emergency_votes += int(row.get("total_votes") or 0)
                    continue
                unmatched.append(row)
                continue
            matched += 1
            for district, share in shares.items():
                county_geography[district] += int(row.get("total_votes") or 0) * float(share)
                for field in FIELDS:
                    estimates[field][district] += int(row.get(field) or 0) * float(share)
        if unmatched:
            if not county_geography or sum(county_geography.values()) <= 0:
                raise ValueError(f"No district geography for unmatched precincts in {county} ({scope})")
            fallback_rows += len(unmatched)
            fallback_votes += sum(int(row.get("total_votes") or 0) for row in unmatched)
            denominator = sum(county_geography.values())
            for row in unmatched:
                for district, weight in county_geography.items():
                    share = weight / denominator
                    for field in FIELDS:
                        estimates[field][district] += int(row.get(field) or 0) * share

        for field in FIELDS:
            if not target[field]:
                continue
            shares = estimates[field] if sum(estimates[field].values()) > 0 else county_geography
            if not shares or sum(shares.values()) <= 0:
                raise ValueError(f"No {field} district allocation for {county} ({scope})")
            for district, votes in base.allocate_integer(target[field], shares).items():
                district_votes[district][field] += votes
        if source:
            reconciled += 1

    results = {district: base.make_result(values, dem_candidate, rep_candidate)
               for district, values in sorted(district_votes.items(), key=lambda item: base.build_data._district_sort_key(item[0]))}
    source_totals = {
        field: sum(int(row.get(field) or 0) for row in county_rows.values()) +
               sum(int(row.get(field) or 0) for county, precincts in by_county.items()
                   if county not in county_rows for row in precincts)
        for field in FIELDS
    }
    deltas = {field: sum(row[field] for row in results.values()) - source_totals[field] for field in FIELDS}
    if any(deltas.values()):
        raise ValueError(f"County vote conservation failure for {contest.get('contest_type')}_{contest.get('year')} {scope}: {deltas}")
    split_votes = {
        district: {field: values[field] - exact_votes[district][field] for field in FIELDS}
        for district, values in district_votes.items()
    }
    if any(value < 0 for values in split_votes.values() for value in values.values()):
        raise ValueError(f"Negative split-county component for {contest.get('contest_type')}_{contest.get('year')} {scope}")
    meta = {
        "match_coverage_pct": round(matched / len(precinct_rows) * 100, 4) if precinct_rows else 0,
        "precinct_rows_total": len(precinct_rows),
        "precinct_rows_overlap_weighted": matched,
        "precinct_rows_county_share_fallback": fallback_rows,
        "precinct_votes_county_share_fallback": fallback_votes,
        "precinct_rows_emergency_party_reconciled": emergency_rows,
        "precinct_votes_emergency_party_reconciled": emergency_votes,
        "emergency_allocation_method": "county_party_proportional" if emergency_by_party else "county_total_vote_share",
        "whole_counties_exact": len(set(by_county) & set(whole)),
        "whole_county_vote_components": {district: values for district, values in exact_votes.items()
                                          if any(values.values())},
        "split_county_vote_components": split_votes,
        "split_counties_reconciled": reconciled,
        "district_lines_file": district_file,
        "precinct_lines_file": "data/Voting_Precincts.geojson",
        "assignment_method": "nc_county_constrained_block_weighted_split_precincts",
        "split_precinct_weight_source": weight_source,
        "canonical_statewide_totals": source_totals,
        "conservation_delta": deltas,
    }
    return {"general": {"results": results}, "meta": meta}, meta


def calibrate_split_components(payload: dict, contest: dict, whole: dict[str, str],
                               targets: dict[str, dict[str, float]], label: str,
                               geometry_key: str, tolerance: float, dynamic: bool) -> tuple[dict, dict]:
    """Calibrate only split-county votes; keep whole-county party totals fixed."""
    exact: dict[str, dict[str, int]] = defaultdict(lambda: {field: 0 for field in FIELDS})
    county_rows = {norm(row["county"]): row for row in contest.get("rows", [])
                   if " - " not in str(row.get("county") or "")}
    for county, district in whole.items():
        row = county_rows.get(county)
        if row:
            for field in FIELDS:
                exact[district][field] += int(row.get(field) or 0)

    split_payload = copy.deepcopy(payload)
    split_results = split_payload["general"]["results"]
    split_targets = {}
    for district, result in split_results.items():
        fixed = exact[district]
        values = {field: int(result[field]) - fixed[field] for field in FIELDS}
        if any(value < 0 for value in values.values()):
            raise ValueError(f"Exact county contribution exceeds district {district}")
        split_results[district] = base.make_result(values, result.get("dem_candidate", ""), result.get("rep_candidate", ""))
        target = targets.get(district)
        if not target:
            continue
        full_total = int(result["total_votes"])
        desired = {field: max(0.0, full_total * target[field] - fixed[field]) for field in FIELDS}
        desired_total = sum(desired.values())
        if desired_total > 0:
            split_targets[district] = {field: desired[field] / desired_total for field in FIELDS}

    calibration = base.calibrate_to_snapshot(split_payload, split_targets, label, tolerance,
                                             geometry_key, dynamic_tolerance=dynamic)
    for district, result in split_results.items():
        fixed = exact[district]
        values = {field: int(result[field]) + fixed[field] for field in FIELDS}
        old = payload["general"]["results"][district]
        payload["general"]["results"][district] = base.make_result(
            values, old.get("dem_candidate", ""), old.get("rep_candidate", "")
        )
    calibration["calibration_method"] = "snapshot_split_county_shares_with_exact_whole_counties"
    return payload, calibration


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contests", default="data/contests_2025_crosswalked")
    parser.add_argument("--contest-overrides-dir", default="",
                        help="Use selected contest files from this staging directory when present")
    parser.add_argument("--skip-reviewed-2022-congress-ballot", action="store_true",
                        help="Disable the reviewed 2022 Richland Oakwood congressional ballot placement")
    parser.add_argument("--reviewed-2022-congress-ballot",
                        default="data/crosswalk/reviewed_2022_congress_ballot_district_overrides.json")
    parser.add_argument("--crosswalk", default="data/crosswalk/current_precinct_to_district_weights.json")
    parser.add_argument("--precincts", default="data/Voting_Precincts.geojson")
    parser.add_argument("--block-weights", default="data/crosswalk/current_precinct_to_district_vap_weights.json")
    parser.add_argument("--whole-county-sliver-audit", default=SLIVER_AUDIT_FILE,
                        help="Block-population audit used to guard near-whole county assignments")
    parser.add_argument("--sliver-population-audit", default="",
                        help="Trial: retain every district piece with positive block CVAP from this audit file")
    parser.add_argument("--skip-populated-slivers", action="store_true",
                        help="Rebuild without the default congressional and Senate populated-sliver allocations")
    parser.add_argument("--skip-legacy-2008-bridge", action="store_true",
                        help="Disable conservative unique-name matches backed by the 2008 VTD overlay")
    parser.add_argument("--legacy-2008-bridge", default="data/crosswalk/legacy_2008_vtd_name_bridge_weights.json")
    parser.add_argument("--skip-reviewed-vtd-bridge", action="store_true",
                        help="Disable reviewed 2010–2024 election-name-to-VTD10 geographic bridge")
    parser.add_argument("--reviewed-vtd-bridge", default="data/crosswalk/reviewed_vtd_name_bridge_2010_2024.json")
    parser.add_argument("--emergency-by-party", action="store_true",
                        help="Allocate non-geographic Emergency votes through county party reconciliation")
    parser.add_argument("--output-dir", default="data/district_contests_county_constrained_staging")
    parser.add_argument("--calibrate-snapshots", action="store_true",
                        help="Experimentally calibrate to legacy snapshots; default is comparison only")
    parser.add_argument("--write", action="store_true", help="Write reviewable staged district slices and QA")
    args = parser.parse_args()
    if args.skip_populated_slivers and args.sliver_population_audit:
        parser.error("--skip-populated-slivers cannot be combined with --sliver-population-audit")
    contest_dir = ROOT / args.contests
    override_dir = ROOT / args.contest_overrides_dir if args.contest_overrides_dir else None
    congress_ballot = (None if args.skip_reviewed_2022_congress_ballot else
                       json.loads((ROOT / args.reviewed_2022_congress_ballot).read_text(encoding="utf-8")))
    if congress_ballot and (congress_ballot.get("_scope"), congress_ballot.get("_year")) != ("congressional", 2022):
        raise ValueError("Reviewed congressional ballot overrides must apply only to 2022 congressional lines")
    manifest = json.loads((contest_dir / "manifest.json").read_text(encoding="utf-8"))
    crosswalk = json.loads((ROOT / args.crosswalk).read_text(encoding="utf-8"))
    block_weights = json.loads((ROOT / args.block_weights).read_text(encoding="utf-8"))
    whole_sliver_audit = json.loads((ROOT / args.whole_county_sliver_audit).read_text(encoding="utf-8"))
    if (whole_sliver_audit.get("weight_source") != block_weights.get("meta", {}).get("weight_source") or
            float(whole_sliver_audit.get("material_split_threshold", -1)) != MATERIAL_PARTIAL_THRESHOLD):
        raise ValueError("Whole-county sliver audit does not match the selected block weights")
    sliver_trial = (json.loads((ROOT / args.sliver_population_audit).read_text(encoding="utf-8"))
                    if args.sliver_population_audit else None)
    if sliver_trial and (sliver_trial.get("weight_source") != whole_sliver_audit.get("weight_source") or
                         float(sliver_trial.get("material_split_threshold", -1)) != MATERIAL_PARTIAL_THRESHOLD):
        raise ValueError("Populated-sliver trial audit does not match the selected block weights")
    sliver_by_scope = defaultdict(dict)
    for entry in (sliver_trial or whole_sliver_audit).get("entries", []):
        scope = entry["scope"]
        if not sliver_trial and (args.skip_populated_slivers or scope not in POPULATED_SLIVER_SCOPES or
                                 float(entry.get("excluded_sliver_cvap") or 0) <= 0):
            continue
        counts = {str(district): float(value) for district, value in entry.get("candidate_cvap", {}).items()
                  if float(value) > 0}
        total = sum(counts.values())
        if total > 0:
            sliver_by_scope[scope][norm(entry["precinct"])] = {
                district: count / total for district, count in counts.items()
            }
    legacy_2008 = (json.loads((ROOT / args.legacy_2008_bridge).read_text(encoding="utf-8"))
                   if not args.skip_legacy_2008_bridge else None)
    reviewed_vtd = (json.loads((ROOT / args.reviewed_vtd_bridge).read_text(encoding="utf-8"))
                    if not args.skip_reviewed_vtd_bridge else None)
    weight_source = str(block_weights.get("meta", {}).get("weight_source") or "unknown")
    precincts = json.loads((ROOT / args.precincts).read_text(encoding="utf-8"))
    areas = precinct_areas(precincts)
    county_fips = {norm(feature["properties"].get("county_nam")): str(
        feature["properties"].get("source_fips") or feature["properties"].get("COUNTYFP20") or "").zfill(3)
        for feature in precincts.get("features", []) if feature.get("properties", {}).get("county_nam")}
    aliases = {}
    for feature in precincts.get("features", []):
        props = feature.get("properties") or {}
        key = norm(props.get("precinct_norm"))
        county = str(props.get("county_nam") or "").strip()
        for value in (key, props.get("precinct_display_name"),
                      f"{county} - {str(props.get('precinct_full_name') or '').strip()}",
                      f"{county} - {str(props.get('prec_id') or '').strip()}"):
            if norm(value):
                aliases[norm(value)] = key

    punctuation_index: dict[str, set[str]] = defaultdict(set)
    for alias, current in aliases.items():
        punctuation_index[punctuation_key(alias)].add(current)
    punctuation_aliases = {}
    for entry in manifest.get("files", []):
        if not 2010 <= int(entry["year"]) <= 2024:
            continue
        contest = json.loads((contest_dir / entry["file"]).read_text(encoding="utf-8"))
        for row in contest.get("rows", []):
            source = norm(row.get("county"))
            if " - " not in source or source in aliases:
                continue
            candidates = punctuation_index.get(punctuation_key(source)) or set()
            if len(candidates) == 1:
                punctuation_aliases[source] = next(iter(candidates))

    qa = []
    outputs = []
    staging_root = ROOT / args.output_dir
    components = {}
    targets = base.load_district_snapshot_targets(ROOT / base.DISTRICT_SNAPSHOT_TARGETS)
    comparisons = calibrations = 0
    for scope, out_rel, prefix, suffix, lines in base.OUTPUTS:
        node = crosswalk["scopes"][scope]
        canonical = {norm(key): shares for key, shares in node["weights"].items()}
        block_overrides = {norm(key): shares for key, shares in block_weights["scopes"][scope]["weights"].items()}
        for key, shares in block_overrides.items():
            if key not in canonical or not shares or abs(sum(float(value) for value in shares.values()) - 1) > 1e-6:
                raise ValueError(f"Invalid block-weighted district shares: {scope} {key}")
        allocation_weights = {}
        for key, shares in canonical.items():
            material = {district: float(share) for district, share in shares.items()
                        if float(share) > 0.001}
            if not material:
                material = {max(shares, key=shares.get): 1.0}
            total = sum(material.values())
            allocation_weights[key] = {district: share / total for district, share in material.items()}
        allocation_weights.update(block_overrides)
        allocation_weights.update(sliver_by_scope.get(scope, {}))
        weights = {alias: allocation_weights[key] for alias, key in aliases.items() if key in allocation_weights}
        punctuation_weights = dict(weights)
        punctuation_weights.update({source: allocation_weights[current]
                                    for source, current in punctuation_aliases.items()})
        bridge_weights = dict(weights)
        bridge_added_keys: set[str] = set()
        if legacy_2008:
            for old_key, current_shares in (legacy_2008.get("weights") or {}).items():
                source = norm(old_key)
                if source in bridge_weights:
                    continue
                district_shares: dict[str, float] = defaultdict(float)
                for current_key, current_share in current_shares.items():
                    target = norm(current_key)
                    if target not in allocation_weights or target.split(" - ", 1)[0] != source.split(" - ", 1)[0]:
                        raise ValueError(f"Invalid 2008 bridge target: {source} -> {target}")
                    for district, district_share in allocation_weights[target].items():
                        district_shares[district] += float(current_share) * float(district_share)
                if abs(sum(district_shares.values()) - 1) > 1e-6:
                    raise ValueError(f"Invalid 2008 bridge share sum: {source}")
                bridge_weights[source] = dict(district_shares)
                bridge_added_keys.add(source)
        reviewed_weights_by_year = {year: dict(punctuation_weights) for year in range(2010, 2025)}
        reviewed_keys_by_year: dict[int, set[str]] = defaultdict(set)
        if reviewed_vtd:
            for old_key, current_shares in (reviewed_vtd.get("weights") or {}).items():
                source = norm(old_key)
                if source in punctuation_weights:
                    continue
                district_shares: dict[str, float] = defaultdict(float)
                for current_key, current_share in current_shares.items():
                    target = norm(current_key)
                    if target not in allocation_weights or target.split(" - ", 1)[0] != source.split(" - ", 1)[0]:
                        raise ValueError(f"Invalid reviewed bridge target: {source} -> {target}")
                    for district, district_share in allocation_weights[target].items():
                        district_shares[district] += float(current_share) * float(district_share)
                if abs(sum(district_shares.values()) - 1) > 1e-6:
                    raise ValueError(f"Invalid reviewed bridge share sum: {source}")
                for year in reviewed_vtd.get("allowed_years", {}).get(old_key, []):
                    if not 2010 <= int(year) <= 2024:
                        raise ValueError(f"Invalid reviewed bridge year: {source} {year}")
                    reviewed_weights_by_year[int(year)][source] = dict(district_shares)
                    reviewed_keys_by_year[int(year)].add(source)
        whole = whole_county_destinations(canonical, areas)
        whole, whole_cvap_exceptions = guard_whole_counties_by_population(
            whole, scope, whole_sliver_audit, county_fips)
        population_split_destinations = {
            row["county"]: set(row["outside_district_cvap"])
            for row in whole_cvap_exceptions
        }
        components[scope] = {
            **mixed_county_components(canonical, areas, whole, population_split_destinations),
            "whole_county_destinations": {county: whole[county] for county in sorted(whole)},
            "whole_county_cvap_exceptions": whole_cvap_exceptions,
        }
        for entry in manifest.get("files", []):
            contest_path = (override_dir / entry["file"] if override_dir and
                            (override_dir / entry["file"]).exists() else contest_dir / entry["file"])
            contest = json.loads(contest_path.read_text(encoding="utf-8"))
            use_bridge = legacy_2008 is not None and int(entry["year"]) == 2008
            year = int(entry["year"])
            use_reviewed_bridge = reviewed_vtd is not None and 2010 <= year <= 2024
            selected_weights = (bridge_weights if use_bridge else reviewed_weights_by_year[year]
                                if use_reviewed_bridge else punctuation_weights if 2010 <= year <= 2024 else weights)
            if scope == "congressional" and year == 2022 and congress_ballot:
                selected_weights = dict(selected_weights)
                for key, shares in congress_ballot.items():
                    if key.startswith("_"):
                        continue
                    source = norm(key)
                    if source not in selected_weights or source.split(" - ", 1)[0] != "RICHLAND":
                        raise ValueError(f"Invalid 2022 congressional ballot source: {source}")
                    if not shares or abs(sum(float(value) for value in shares.values()) - 1) > 1e-6:
                        raise ValueError(f"Invalid 2022 congressional ballot weights: {source}")
                    selected_weights[source] = {str(district): float(share) for district, share in shares.items()}
            payload, meta = rebuild_one(contest, selected_weights,
                                        whole, scope, node["district_file"], weight_source,
                                        emergency_by_party=args.emergency_by_party and 2010 <= year <= 2024)
            if whole_cvap_exceptions:
                payload["meta"]["whole_county_cvap_exceptions"] = whole_cvap_exceptions
                meta["whole_county_cvap_exceptions"] = whole_cvap_exceptions
            if scope == "congressional" and year == 2022 and congress_ballot:
                keys = {norm(key) for key in congress_ballot if not key.startswith("_")}
                ballot_rows = [row for row in contest.get("rows", []) if norm(row.get("county")) in keys]
                if len(ballot_rows) != len(keys):
                    raise ValueError(f"Missing reviewed 2022 congressional ballot row: {entry['file']}")
                payload["meta"]["reviewed_congress_ballot_file"] = args.reviewed_2022_congress_ballot
                payload["meta"]["reviewed_congress_ballot_votes"] = sum(int(row.get("total_votes") or 0)
                                                                        for row in ballot_rows)
                meta["reviewed_congress_ballot_votes"] = payload["meta"]["reviewed_congress_ballot_votes"]
            if 2010 <= year <= 2024:
                punctuation_rows = [row for row in contest.get("rows", [])
                                    if norm(row.get("county")) in punctuation_aliases]
                payload["meta"]["punctuation_alias_rows"] = len(punctuation_rows)
                payload["meta"]["punctuation_alias_votes"] = sum(int(row.get("total_votes") or 0)
                                                                for row in punctuation_rows)
                meta["punctuation_alias_rows"] = payload["meta"]["punctuation_alias_rows"]
                meta["punctuation_alias_votes"] = payload["meta"]["punctuation_alias_votes"]
            if use_bridge:
                meta["historical_name_bridge"] = args.legacy_2008_bridge
                payload["meta"]["historical_name_bridge"] = args.legacy_2008_bridge
                bridged_rows = [row for row in contest.get("rows", []) if norm(row.get("county")) in bridge_added_keys]
                meta["historical_name_bridge_rows"] = len(bridged_rows)
                meta["historical_name_bridge_votes"] = sum(int(row.get("total_votes") or 0) for row in bridged_rows)
                payload["meta"]["historical_name_bridge_rows"] = meta["historical_name_bridge_rows"]
                payload["meta"]["historical_name_bridge_votes"] = meta["historical_name_bridge_votes"]
            if use_reviewed_bridge:
                bridged_rows = [row for row in contest.get("rows", [])
                                if norm(row.get("county")) in reviewed_keys_by_year[year]]
                payload["meta"]["historical_name_bridge"] = args.reviewed_vtd_bridge
                payload["meta"]["historical_name_bridge_rows"] = len(bridged_rows)
                payload["meta"]["historical_name_bridge_votes"] = sum(int(row.get("total_votes") or 0) for row in bridged_rows)
                meta.update({key: payload["meta"][key] for key in
                             ("historical_name_bridge", "historical_name_bridge_rows", "historical_name_bridge_votes")})
            if sliver_trial:
                payload["meta"]["sliver_population_trial_file"] = args.sliver_population_audit
                meta["sliver_population_trial_file"] = args.sliver_population_audit
            elif scope in POPULATED_SLIVER_SCOPES and not args.skip_populated_slivers:
                payload["meta"]["populated_sliver_allocation_file"] = args.whole_county_sliver_audit
                payload["meta"]["populated_sliver_precincts"] = len(sliver_by_scope[scope])
                meta["populated_sliver_allocation_file"] = args.whole_county_sliver_audit
                meta["populated_sliver_precincts"] = len(sliver_by_scope[scope])
            calibration_key = "state_house_root" if prefix == "state_house" and lines is None else scope
            contest_key = f"{entry['contest_type']}_{entry['year']}"
            target = (targets.get(calibration_key) or {}).get(contest_key)
            target_label = f"{base.DISTRICT_SNAPSHOT_TARGETS.as_posix()}#{calibration_key}/{contest_key}"
            if entry["contest_type"] == "president" and int(entry["year"]) == 2024 and calibration_key in base.PRESIDENT_2024_STATE_HOUSE_TARGETS:
                target_path = base.PRESIDENT_2024_STATE_HOUSE_TARGETS[calibration_key]
                target = base.load_share_targets(ROOT / target_path)
                target_label = target_path.as_posix()
            if target:
                comparison = base.compare_to_snapshot(payload, target, target_label, calibration_key)
                meta.update(comparison)
                payload["meta"].update(comparison)
                status = "comparison_only_incompatible_baseline"
                if not args.calibrate_snapshots:
                    status = "comparison_only_calibration_disabled"
                elif comparison["snapshot_comparison_max_abs_share_delta_pp"] <= base.MAX_GENERAL_SNAPSHOT_INPUT_DRIFT_PP:
                    candidate = copy.deepcopy(payload)
                    strict = target_label.endswith(".csv")
                    tolerance = base.SNAPSHOT_TOLERANCES_PP.get(calibration_key, 1.0) if strict else 1.0
                    try:
                        candidate, calibration = calibrate_split_components(
                            candidate, contest, whole, target, target_label, calibration_key,
                            tolerance, not strict
                        )
                        if (calibration["calibration_districts"] == calibration["calibration_expected_districts"]
                                and calibration["calibration_max_abs_share_delta_pp"] <= calibration["calibration_tolerance_pp"]):
                            payload = candidate
                            meta.update(calibration)
                            payload["meta"].update(calibration)
                            calibrations += 1
                            status = "calibrated_split_components"
                        else:
                            status = "comparison_only_post_balance_drift"
                    except (ValueError, SystemExit):
                        status = "comparison_only_incompatible_exact_component"
                meta["snapshot_calibration_status"] = status
                payload["meta"]["snapshot_calibration_status"] = status
                comparisons += 1
            final_results = payload["general"]["results"]
            exact_components = meta["whole_county_vote_components"]
            final_split_components = {
                district: {field: int(result[field]) - int(exact_components.get(district, {}).get(field, 0))
                           for field in FIELDS}
                for district, result in final_results.items()
            }
            if any(value < 0 for values in final_split_components.values() for value in values.values()):
                raise ValueError(f"Negative split component after calibration: {contest_key} {scope}")
            meta["split_county_vote_components"] = final_split_components
            payload["meta"]["split_county_vote_components"] = final_split_components
            statewide = {field: sum(int(row.get(field) or 0) for row in final_results.values()) for field in FIELDS}
            canonical = meta["canonical_statewide_totals"]
            final_delta = {field: statewide[field] - canonical[field] for field in FIELDS}
            if any(final_delta.values()):
                raise ValueError(f"Statewide totals changed after district calibration: {contest_key} {scope}: {final_delta}")
            meta["district_statewide_totals"] = statewide
            meta["conservation_delta"] = final_delta
            payload["meta"].update({"district_statewide_totals": statewide, "conservation_delta": final_delta})
            name = f"{prefix}_{entry['contest_type']}_{entry['year']}{suffix}.json"
            relative_output = out_rel.relative_to("data/district_contests")
            outputs.append((staging_root / relative_output / name, payload))
            qa.append({"scope": scope, "contest_type": entry["contest_type"], "year": entry["year"], "file": name, **meta})

    component_catalog = {
        "schema": "mixed_county_components.v1",
        "full_county_threshold": FULL_COUNTY_THRESHOLD,
        "max_whole_county_outside_cvap_share": MAX_WHOLE_COUNTY_OUTSIDE_CVAP_SHARE,
        "material_partial_county_threshold": MATERIAL_PARTIAL_THRESHOLD,
        "method": "Canonical county returns are inserted directly for whole counties only when both area and block population pass the 0.1% sliver test; split-county components use precinct/block weights.",
        "plans": components,
    }
    report = {"assignment_method": "nc_county_constrained_block_weighted_split_precincts",
              "split_precinct_weight_source": weight_source,
              "punctuation_aliases_2010_2024": punctuation_aliases,
              "snapshot_comparisons_expected": comparisons, "snapshot_calibrations_expected": calibrations,
              "mixed_county_component_catalog": "mixed_county_components.json", "files": qa}
    drift_rows = []
    for staged_path, payload in outputs:
        relative = staged_path.relative_to(staging_root)
        live_path = ROOT / "data/district_contests" / relative
        if not live_path.exists():
            continue
        live_results = (json.loads(live_path.read_text(encoding="utf-8")).get("general") or {}).get("results") or {}
        proposed = payload["general"]["results"]
        compared = set(live_results) & set(proposed)
        if not compared:
            continue
        margin_deltas = {district: abs(float(proposed[district].get("margin_pct") or 0) -
                                       float(live_results[district].get("margin_pct") or 0))
                         for district in compared}
        worst = max(margin_deltas, key=margin_deltas.get)
        drift_rows.append({"file": relative.as_posix(), "district_rows_compared": len(compared),
                           "max_margin_delta_pp": round(margin_deltas[worst], 4),
                           "worst_district": worst,
                           "winner_changes": sum(proposed[d].get("winner") != live_results[d].get("winner") for d in compared)})
    drift = {"summary": {"files_compared": len(drift_rows),
                         "district_rows_compared": sum(row["district_rows_compared"] for row in drift_rows),
                         "max_margin_delta_pp": max((row["max_margin_delta_pp"] for row in drift_rows), default=0),
                         "winner_changes": sum(row["winner_changes"] for row in drift_rows),
                         "files_with_over_1pp_margin_delta": sum(row["max_margin_delta_pp"] > 1 for row in drift_rows)},
             "files": drift_rows}
    print(json.dumps({"mode": "write" if args.write else "audit", "district_files": len(outputs),
                      "mixed_districts": {scope: len(plan["exact_components"]) for scope, plan in components.items()},
                      "conservation_failures": sum(any(row["conservation_delta"].values()) for row in qa),
                      "snapshot_comparisons": comparisons, "snapshot_calibrations": calibrations}, indent=2))
    if args.write:
        for path, payload in outputs:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        for directory, suffix, lines in {(staging_root / path_rel.relative_to("data/district_contests"), suffix, lines)
                                         for _, path_rel, _, suffix, lines in base.OUTPUTS}:
            base.rebuild_manifest(directory, suffix, lines)
        (staging_root / "current_geojson_qa.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        (staging_root / "mixed_county_components.json").write_text(json.dumps(component_catalog, indent=2) + "\n", encoding="utf-8")
        (staging_root / "drift_vs_served.json").write_text(json.dumps(drift, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
