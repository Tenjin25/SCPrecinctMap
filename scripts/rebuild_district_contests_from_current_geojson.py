#!/usr/bin/env python3
"""Rebuild statewide-by-district contest files from current precinct overlap weights."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
import build_data  # noqa: E402
from scripts.aggregate_contests_to_vtd20_crosswalks import norm  # noqa: E402


OUTPUTS = (
    ("congressional", Path("data/district_contests"), "congressional", "", None),
    ("state_house_2022", Path("data/district_contests"), "state_house", "", None),
    ("state_senate_2022", Path("data/district_contests"), "state_senate", "", None),
    ("state_house_2022", Path("data/district_contests/state_house_2022_lines"), "state_house", "_2022_lines", "2022_lines"),
    ("state_house_2024", Path("data/district_contests/state_house_2024_lines"), "state_house", "_2024_lines", "2024_lines"),
)
FIELDS = ("dem_votes", "rep_votes", "other_votes")
PRESIDENT_2024_STATE_HOUSE_TARGETS = {
    "state_house_root": Path("data/district-statistics state house 2024 pres.csv"),
    "state_house_2022": Path("data/district-statistics 2024 pres state house.csv"),
    "state_house_2024": Path("data/district-statistics state house 2024 pres.csv"),
}
DISTRICT_SNAPSHOT_TARGETS = Path("data/district_contests/district_snapshot_targets.json")
SNAPSHOT_TOLERANCES_PP = {
    "state_house_root": 0.25,
    "state_house_2022": 1.0,
    "state_house_2024": 0.25,
}
MAX_GENERAL_SNAPSHOT_INPUT_DRIFT_PP = 35.0


def allocate_integer(votes: int, shares: dict[str, float]) -> dict[str, int]:
    if votes <= 0 or not shares:
        return {}
    normalized_total = sum(float(value) for value in shares.values())
    exact = {key: votes * float(value) / normalized_total for key, value in shares.items()}
    allocated = {key: int(value) for key, value in exact.items()}
    remainder = votes - sum(allocated.values())
    order = sorted(exact, key=lambda key: (exact[key] - allocated[key], key), reverse=True)
    for key in order[:remainder]:
        allocated[key] += 1
    return allocated


def make_result(values: dict, dem_candidate: str, rep_candidate: str) -> dict:
    dem = int(values.get("dem_votes") or 0)
    rep = int(values.get("rep_votes") or 0)
    other = int(values.get("other_votes") or 0)
    total = dem + rep + other
    margin = rep - dem
    margin_pct = round(margin / total * 100, 4) if total else 0
    return {
        "dem_votes": dem,
        "rep_votes": rep,
        "other_votes": other,
        "total_votes": total,
        "dem_candidate": dem_candidate,
        "rep_candidate": rep_candidate,
        "margin": margin,
        "margin_pct": margin_pct,
        "winner": "R" if margin > 0 else ("D" if margin < 0 else "T"),
        "color": build_data.margin_color(margin_pct),
    }


def load_share_targets(path: Path) -> dict[str, dict[str, float]]:
    targets = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            district = str(row.get("ID") or "").strip().strip('"')
            try:
                district = str(int(district))
                shares = {field: float(row.get(column) or 0) for field, column in (
                    ("dem_votes", "Dem"), ("rep_votes", "Rep"), ("other_votes", "Oth")
                )}
            except ValueError:
                continue
            total = sum(shares.values())
            if total <= 0:
                continue
            targets[district] = {field: value / total for field, value in shares.items()}
    return targets


def load_district_snapshot_targets(path: Path) -> dict[str, dict[str, dict[str, float]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    output = {}
    for geometry, contests in (payload.get("geometries") or {}).items():
        output[geometry] = {}
        for contest_key, target in contests.items():
            output[geometry][contest_key] = {
                district: dict(zip(FIELDS, shares))
                for district, shares in (target.get("shares") or {}).items()
            }
    return output


def allocate_capped_total(total: int, desired: dict[str, float], caps: dict[str, int]) -> dict[str, int]:
    desired_total = sum(max(0.0, value) for value in desired.values())
    if total == 0:
        return {key: 0 for key in caps}
    if desired_total <= 0:
        desired = {key: float(cap) for key, cap in caps.items()}
        desired_total = sum(desired.values())
    if total < 0 or total > sum(caps.values()) or desired_total <= 0:
        raise SystemExit(f"Invalid calibrated allocation total={total} capacity={sum(caps.values())}")
    exact = {key: total * max(0.0, desired.get(key, 0.0)) / desired_total for key in caps}
    allocated = {key: min(caps[key], int(exact[key])) for key in caps}
    remaining = total - sum(allocated.values())
    order = sorted(caps, key=lambda key: (exact[key] - allocated[key], key), reverse=True)
    while remaining:
        progressed = False
        for key in order:
            if allocated[key] >= caps[key]:
                continue
            allocated[key] += 1
            remaining -= 1
            progressed = True
            if not remaining:
                break
        if not progressed:
            raise SystemExit("Unable to satisfy calibrated allocation caps")
    return allocated


def compare_to_snapshot(payload: dict, targets: dict[str, dict[str, float]], target_label: str, geometry_key: str) -> dict:
    results = (payload.get("general") or {}).get("results") or {}
    deltas = []
    for district in set(results) & set(targets):
        row = results[district]
        total = sum(int(row.get(field) or 0) for field in FIELDS)
        if not total:
            continue
        for field in FIELDS:
            deltas.append(abs(int(row.get(field) or 0) / total - targets[district][field]) * 100)
    return {
        "snapshot_comparison_geometry": geometry_key,
        "snapshot_comparison_target_file": target_label,
        "snapshot_comparison_districts": len(set(results) & set(targets)),
        "snapshot_comparison_mean_abs_share_delta_pp": round(sum(deltas) / len(deltas), 6) if deltas else 0,
        "snapshot_comparison_max_abs_share_delta_pp": round(max(deltas, default=0), 6),
    }


def calibrate_to_snapshot(
    payload: dict,
    targets: dict[str, dict[str, float]],
    target_label: str,
    tolerance_pp: float,
    geometry_key: str,
    dynamic_tolerance: bool = True,
    frozen_districts: set[str] | None = None,
) -> dict:
    results = (payload.get("general") or {}).get("results") or {}
    frozen = {str(int(d)) if str(d).isdigit() else str(d) for d in (frozen_districts or set())}
    frozen |= {str(d) for d in (frozen_districts or set())}
    def _norm_district(key: str) -> str:
        return str(int(key)) if str(key).isdigit() else str(key)

    frozen_keys = {key for key in results if _norm_district(key) in frozen or str(key) in frozen}
    active_results = {key: row for key, row in results.items() if key not in frozen_keys}
    snapshot_districts = set(active_results) & set(targets)
    missing = sorted(set(active_results) - set(targets), key=build_data._district_sort_key)
    targets = {district: dict(shares) for district, shares in targets.items() if district in active_results}
    for district in missing:
        row = active_results[district]
        total = sum(int(row.get(field) or 0) for field in FIELDS)
        targets[district] = {
            field: (int(row.get(field) or 0) / total if total else 0)
            for field in FIELDS
        }

    source_totals_all = {field: sum(int(row.get(field) or 0) for row in results.values()) for field in FIELDS}
    frozen_party = {
        field: sum(int(results[key].get(field) or 0) for key in frozen_keys)
        for field in FIELDS
    }
    source_totals = {field: source_totals_all[field] - frozen_party[field] for field in FIELDS}
    row_totals = {district: int(row.get("total_votes") or 0) for district, row in active_results.items()}
    grand_total = sum(row_totals.values()) or 1
    target_party_totals = {
        field: sum(row_totals[district] * targets[district][field] for district in active_results)
        for field in FIELDS
    }
    statewide_mix_gap_pp = max(
        (abs(target_party_totals[field] - source_totals[field]) / grand_total * 100 for field in FIELDS),
        default=0.0,
    )
    effective_tolerance_pp = max(tolerance_pp, statewide_mix_gap_pp + 2.0) if dynamic_tolerance else tolerance_pp
    if not active_results:
        return {
            "calibration_method": "snapshot_shares_with_exact_statewide_party_and_district_totals",
            "calibration_geometry": geometry_key,
            "calibration_target_file": target_label,
            "calibration_districts": 0,
            "calibration_expected_districts": len(results),
            "calibration_snapshot_districts": 0,
            "calibration_source_fallback_districts": 0,
            "calibration_frozen_districts": sorted(frozen_keys, key=build_data._district_sort_key),
            "calibration_mean_abs_share_delta_pp": 0.0,
            "calibration_max_abs_share_delta_pp": 0.0,
            "calibration_statewide_mix_gap_pp": 0.0,
            "calibration_tolerance_pp": round(effective_tolerance_pp, 6),
            "calibration_conservation_delta": {field: 0 for field in FIELDS},
        }
    other_desired = {district: row_totals[district] * targets[district]["other_votes"] for district in active_results}
    other_alloc = allocate_capped_total(source_totals["other_votes"], other_desired, row_totals)
    two_party_caps = {district: row_totals[district] - other_alloc[district] for district in active_results}
    dem_desired = {
        district: two_party_caps[district]
        * targets[district]["dem_votes"]
        / (targets[district]["dem_votes"] + targets[district]["rep_votes"] or 1)
        for district in active_results
    }
    dem_alloc = allocate_capped_total(source_totals["dem_votes"], dem_desired, two_party_caps)

    share_deltas = []
    for district, previous in active_results.items():
        values = {
            "dem_votes": dem_alloc[district],
            "other_votes": other_alloc[district],
            "rep_votes": two_party_caps[district] - dem_alloc[district],
        }
        results[district] = make_result(values, previous.get("dem_candidate", ""), previous.get("rep_candidate", ""))
        total = row_totals[district]
        for field in FIELDS:
            actual = results[district][field] / total if total else 0
            share_deltas.append(abs(actual - targets[district][field]) * 100)

    calibrated_totals = {field: sum(int(row.get(field) or 0) for row in results.values()) for field in FIELDS}
    conservation_delta = {field: calibrated_totals[field] - source_totals_all[field] for field in FIELDS}
    if any(conservation_delta.values()):
        raise SystemExit(f"Snapshot calibration conservation failure for {target_label}: {conservation_delta}")
    max_delta = max(share_deltas, default=0.0)
    return {
        "calibration_method": "snapshot_shares_with_exact_statewide_party_and_district_totals",
        "calibration_geometry": geometry_key,
        "calibration_target_file": target_label,
        "calibration_districts": len(targets),
        "calibration_expected_districts": len(results),
        "calibration_snapshot_districts": len(snapshot_districts),
        "calibration_source_fallback_districts": len(missing),
        "calibration_frozen_districts": sorted(frozen_keys, key=build_data._district_sort_key),
        "calibration_mean_abs_share_delta_pp": round(sum(share_deltas) / len(share_deltas), 6),
        "calibration_max_abs_share_delta_pp": round(max_delta, 6),
        "calibration_statewide_mix_gap_pp": round(statewide_mix_gap_pp, 6),
        "calibration_tolerance_pp": round(effective_tolerance_pp, 6),
        "calibration_conservation_delta": conservation_delta,
    }


def rebuild_one(contest: dict, weights: dict[str, dict[str, float]], scope: str, district_file: str) -> tuple[dict, dict]:
    precinct_rows = [row for row in contest.get("rows") or [] if " - " in str((row or {}).get("county") or "")]
    mapped = []
    fallback = []
    county_district_votes: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in precinct_rows:
        key = str(row.get("county") or "")
        shares = weights.get(norm(key))
        if shares:
            mapped.append((row, shares))
            county = norm(key.split(" - ", 1)[0])
            total = int(row.get("total_votes") or 0)
            for district, share in shares.items():
                county_district_votes[county][district] += total * float(share)
        else:
            fallback.append(row)

    assignments = list(mapped)
    fallback_votes = 0
    for row in fallback:
        county = norm(str(row.get("county") or "").split(" - ", 1)[0])
        district_totals = county_district_votes.get(county) or {}
        total = sum(district_totals.values())
        if total <= 0:
            raise SystemExit(f"No district fallback shares for {row.get('county')} in {scope}")
        shares = {district: value / total for district, value in district_totals.items()}
        assignments.append((row, shares))
        fallback_votes += int(row.get("total_votes") or 0)

    by_district: dict[str, dict] = defaultdict(lambda: {field: 0 for field in FIELDS})
    dem_candidate = rep_candidate = ""
    for row, shares in assignments:
        dem_candidate = dem_candidate or str(row.get("dem_candidate") or "")
        rep_candidate = rep_candidate or str(row.get("rep_candidate") or "")
        for field in FIELDS:
            for district, votes in allocate_integer(int(row.get(field) or 0), shares).items():
                by_district[district][field] += votes

    results = {
        district: make_result(values, dem_candidate, rep_candidate)
        for district, values in sorted(by_district.items(), key=lambda item: build_data._district_sort_key(item[0]))
    }
    source_totals = {field: sum(int(row.get(field) or 0) for row in precinct_rows) for field in FIELDS}
    output_totals = {field: sum(result[field] for result in results.values()) for field in FIELDS}
    deltas = {field: output_totals[field] - source_totals[field] for field in FIELDS}
    if any(deltas.values()):
        raise SystemExit(f"District vote conservation failure for {contest.get('contest_type')}_{contest.get('year')} {scope}: {deltas}")
    meta = {
        "match_coverage_pct": round(len(mapped) / len(precinct_rows) * 100, 4) if precinct_rows else 0,
        "precinct_rows_total": len(precinct_rows),
        "precinct_rows_overlap_weighted": len(mapped),
        "precinct_rows_county_share_fallback": len(fallback),
        "precinct_votes_county_share_fallback": fallback_votes,
        "district_lines_file": district_file,
        "precinct_lines_file": "data/Voting_Precincts.geojson",
        "assignment_method": "current_precinct_polygon_overlap_with_county_share_fallback",
        "conservation_delta": deltas,
    }
    return {"general": {"results": results}, "meta": meta}, meta


def rebuild_manifest(directory: Path, line_suffix: str, district_lines: str | None) -> None:
    entries = []
    for path in directory.glob("*.json"):
        if path.name.startswith(("manifest", "qa_", "current_geojson_qa")):
            continue
        stem = path.stem
        if line_suffix and stem.endswith(line_suffix):
            stem = stem[: -len(line_suffix)]
        parts = stem.split("_")
        if parts[:2] in (["state", "house"], ["state", "senate"]):
            scope = "_".join(parts[:2]); contest = "_".join(parts[2:-1])
        else:
            scope = parts[0]; contest = "_".join(parts[1:-1])
        try:
            year = int(parts[-1])
        except ValueError:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = len(((payload.get("general") or {}).get("results") or {}))
        entry = {"scope": scope, "contest_type": contest, "year": year, "file": path.name, "rows": rows}
        if district_lines:
            entry["district_lines"] = district_lines
        entries.append(entry)
    entries.sort(key=lambda item: (-item["year"], item["scope"], item["contest_type"]))
    name = f"manifest{line_suffix}.json" if line_suffix else "manifest.json"
    (directory / name).write_text(json.dumps({"files": entries}, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contests", default="data/contests_2025_crosswalked")
    parser.add_argument("--crosswalk", default="data/crosswalk/current_precinct_to_district_weights.json")
    parser.add_argument("--precincts", default="data/Voting_Precincts.geojson")
    args = parser.parse_args()
    contest_dir = REPO_ROOT / args.contests
    manifest = json.loads((contest_dir / "manifest.json").read_text(encoding="utf-8"))
    crosswalk = json.loads((REPO_ROOT / args.crosswalk).read_text(encoding="utf-8"))
    precincts = json.loads((REPO_ROOT / args.precincts).read_text(encoding="utf-8"))
    key_aliases = {}
    for feature in precincts.get("features") or []:
        props = (feature or {}).get("properties") or {}
        precinct_norm = norm(props.get("precinct_norm") or "")
        county = str(props.get("county_nam") or "").strip()
        if not precinct_norm:
            continue
        for value in (
            props.get("precinct_norm"),
            props.get("precinct_display_name"),
            f"{county} - {str(props.get('precinct_full_name') or '').strip()}",
            f"{county} - {str(props.get('prec_id') or '').strip()}",
        ):
            alias = norm(value)
            if alias:
                key_aliases[alias] = precinct_norm
    qa = []
    president_targets = {
        scope: load_share_targets(REPO_ROOT / path)
        for scope, path in PRESIDENT_2024_STATE_HOUSE_TARGETS.items()
    }
    snapshot_targets = load_district_snapshot_targets(REPO_ROOT / DISTRICT_SNAPSHOT_TARGETS)
    calibration_count = 0
    comparison_count = 0
    rebuilt_dirs = set()
    for scope_key, out_rel, prefix, suffix, district_lines in OUTPUTS:
        scope_data = crosswalk["scopes"][scope_key]
        # Contest rows use friendly display labels while the geometry crosswalk
        # stores Fiscal Affairs precinct_norm keys. Normalize both sides with
        # the same election-key routine before joining them.
        canonical_weights = {norm(key): value for key, value in scope_data["weights"].items()}
        weights = {
            alias: canonical_weights[precinct_norm]
            for alias, precinct_norm in key_aliases.items()
            if precinct_norm in canonical_weights
        }
        out_dir = REPO_ROOT / out_rel
        out_dir.mkdir(parents=True, exist_ok=True)
        rebuilt_dirs.add((out_dir, suffix, district_lines))
        for entry in manifest.get("files") or []:
            contest = json.loads((contest_dir / entry["file"]).read_text(encoding="utf-8"))
            payload, meta = rebuild_one(contest, weights, scope_key, scope_data["district_file"])
            calibration_key = "state_house_root" if prefix == "state_house" and district_lines is None else scope_key
            contest_key = f"{entry['contest_type']}_{entry['year']}"
            targets = (snapshot_targets.get(calibration_key) or {}).get(contest_key)
            target_label = f"{DISTRICT_SNAPSHOT_TARGETS.as_posix()}#{calibration_key}/{contest_key}"
            tolerance_pp = 1.0
            dynamic_tolerance = True
            if entry["contest_type"] == "president" and int(entry["year"]) == 2024 and calibration_key in president_targets:
                targets = president_targets[calibration_key]
                target_label = PRESIDENT_2024_STATE_HOUSE_TARGETS[calibration_key].as_posix()
                tolerance_pp = SNAPSHOT_TOLERANCES_PP[calibration_key]
                dynamic_tolerance = False
            if targets:
                comparison = compare_to_snapshot(payload, targets, target_label, calibration_key)
                comparison_count += 1
                payload["meta"].update(comparison)
                meta.update(comparison)
                if dynamic_tolerance and comparison["snapshot_comparison_max_abs_share_delta_pp"] > MAX_GENERAL_SNAPSHOT_INPUT_DRIFT_PP:
                    payload["meta"]["snapshot_calibration_status"] = "comparison_only_incompatible_baseline"
                    meta["snapshot_calibration_status"] = "comparison_only_incompatible_baseline"
                    targets = None
            if targets:
                candidate_payload = copy.deepcopy(payload)
                calibration = calibrate_to_snapshot(
                    candidate_payload,
                    targets,
                    target_label,
                    tolerance_pp,
                    calibration_key,
                    dynamic_tolerance,
                )
                if calibration["calibration_max_abs_share_delta_pp"] > calibration["calibration_tolerance_pp"]:
                    if not dynamic_tolerance:
                        raise SystemExit(
                            f"Snapshot calibration exceeds tolerance for {target_label}: "
                            f"{calibration['calibration_max_abs_share_delta_pp']}pp"
                        )
                    payload["meta"]["snapshot_calibration_status"] = "comparison_only_post_balance_drift"
                    meta["snapshot_calibration_status"] = "comparison_only_post_balance_drift"
                else:
                    payload = candidate_payload
                    payload["meta"].update(calibration)
                    meta.update(calibration)
                    calibration_count += 1
            name = f"{prefix}_{entry['contest_type']}_{entry['year']}{suffix}.json"
            (out_dir / name).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            qa.append({"scope": scope_key, "contest_type": entry["contest_type"], "year": entry["year"], "file": name, **meta})
    for directory, suffix, district_lines in rebuilt_dirs:
        if district_lines:
            for stale in directory.glob("state_house_*.json"):
                if not stale.name.endswith(f"{suffix}.json"):
                    stale.unlink()
            desired_manifest = f"manifest{suffix}.json"
            for stale in directory.glob("manifest*.json"):
                if stale.name != desired_manifest:
                    stale.unlink()
        rebuild_manifest(directory, suffix, district_lines)
    qa_path = REPO_ROOT / "data/district_contests/current_geojson_qa.json"
    qa_path.write_text(
        json.dumps(
            {
                "snapshot_comparisons_expected": comparison_count,
                "snapshot_calibrations_expected": calibration_count,
                "files": qa,
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    print(f"rebuilt {len(qa)} statewide-by-district files from current precinct geometry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
