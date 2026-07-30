#!/usr/bin/env python3
"""Audit source, current-precinct, and district contest integrity invariants."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from aggregate_contests_to_vtd20_crosswalks import POST_2024_PLAN_COUNTIES, norm  # noqa: E402


FIELDS = ("dem_votes", "rep_votes", "other_votes", "total_votes")


def totals(rows: list[dict], precinct: bool) -> dict[str, int]:
    selected = [row for row in rows if (" - " in str(row.get("county") or "")) == precinct]
    return {field: sum(int(row.get(field) or 0) for row in selected) for field in FIELDS}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data/contest_integrity_report.json")
    args = parser.parse_args()
    base_dir = REPO_ROOT / "data/contests"
    current_dir = REPO_ROOT / "data/contests_2025_crosswalked"
    base_manifest = json.loads((base_dir / "manifest.json").read_text(encoding="utf-8"))
    current_manifest = json.loads((current_dir / "manifest.json").read_text(encoding="utf-8"))
    source_report = json.loads((base_dir / "source_integrity.json").read_text(encoding="utf-8"))
    district_report = json.loads((REPO_ROOT / "data/district_contests/current_geojson_qa.json").read_text(encoding="utf-8"))
    district_crosswalk = json.loads((REPO_ROOT / "data/crosswalk/current_precinct_to_district_weights.json").read_text(encoding="utf-8"))
    precincts = json.loads((REPO_ROOT / "data/Voting_Precincts.geojson").read_text(encoding="utf-8"))

    current_keys = set()
    for feature in precincts.get("features") or []:
        props = (feature or {}).get("properties") or {}
        county = str(props.get("county_nam") or "").strip()
        for value in (
            props.get("precinct_norm"),
            props.get("precinct_display_name"),
            f"{county} - {str(props.get('precinct_full_name') or '').strip()}",
            f"{county} - {str(props.get('prec_id') or '').strip()}",
        ):
            key = norm(value)
            if key:
                current_keys.add(key)

    source_by_contest = {
        (int(row["year"]), str(row["contest_type"])): row
        for row in source_report.get("contests") or []
    }
    current_entries = {entry["file"]: entry for entry in current_manifest.get("files") or []}
    errors = []
    warnings = []
    contests = []
    for entry in base_manifest.get("files") or []:
        file_name = entry["file"]
        base = json.loads((base_dir / file_name).read_text(encoding="utf-8"))
        current = json.loads((current_dir / file_name).read_text(encoding="utf-8"))
        base_rows = base.get("rows") or []
        current_rows = current.get("rows") or []
        duplicate_base = len(base_rows) - len({str(row.get("county") or "") for row in base_rows})
        duplicate_current = len(current_rows) - len({str(row.get("county") or "") for row in current_rows})
        calculation_errors = sum(
            int(row.get("total_votes") or 0)
            != int(row.get("dem_votes") or 0) + int(row.get("rep_votes") or 0) + int(row.get("other_votes") or 0)
            for row in base_rows + current_rows
        )
        base_county = totals(base_rows, False)
        base_precinct = totals(base_rows, True)
        current_county = totals(current_rows, False)
        current_precinct = totals(current_rows, True)
        conservation = {
            field: current_precinct[field] - base_precinct[field]
            for field in FIELDS
        }
        source = source_by_contest.get((int(entry["year"]), str(entry["contest_type"]))) or {}
        source_delta = {
            "county_votes": base_county["total_votes"] - int(source.get("county_votes") or 0),
            "geographic_precinct_votes": base_precinct["total_votes"] - int(source.get("geographic_precinct_votes") or 0),
        }
        unmatched = [
            row for row in current_rows
            if " - " in str(row.get("county") or "") and norm(row.get("county")) not in current_keys
        ]
        exact_2024_drifts = []
        if int(entry["year"]) == 2024:
            current_by_norm = {
                norm(row.get("county")): row
                for row in current_rows
                if " - " in str(row.get("county") or "")
            }
            for row in base_rows:
                source_key = str(row.get("county") or "")
                source_norm = norm(source_key)
                county_norm = norm(source_key.split(" - ", 1)[0])
                if (
                    " - " not in source_key
                    or county_norm in POST_2024_PLAN_COUNTIES
                    or source_norm not in current_keys
                    or source_norm not in current_by_norm
                ):
                    continue
                target = current_by_norm[source_norm]
                if any(int(row.get(field) or 0) != int(target.get(field) or 0) for field in FIELDS):
                    exact_2024_drifts.append(source_key)
        record = {
            "year": entry["year"],
            "contest_type": entry["contest_type"],
            "file": file_name,
            "base_rows": len(base_rows),
            "current_rows": len(current_rows),
            "duplicate_base_keys": duplicate_base,
            "duplicate_current_keys": duplicate_current,
            "calculation_errors": calculation_errors,
            "source_delta": source_delta,
            "current_crosswalk_conservation_delta": conservation,
            "unmatched_current_precinct_rows": len(unmatched),
            "unmatched_current_precinct_votes": sum(int(row.get("total_votes") or 0) for row in unmatched),
            "source_exact_2024_vote_drifts": len(exact_2024_drifts),
        }
        contests.append(record)
        if file_name not in current_entries:
            errors.append(f"missing current manifest entry: {file_name}")
        if (
            duplicate_base
            or duplicate_current
            or calculation_errors
            or any(source_delta.values())
            or any(conservation.values())
            or exact_2024_drifts
        ):
            errors.append(f"contest integrity failure: {file_name}")
        if unmatched:
            warnings.append(f"{file_name}: {len(unmatched)} unmatched current rows / {record['unmatched_current_precinct_votes']} votes")

    crosswalk_scopes = []
    precinct_count = int(district_crosswalk.get("meta", {}).get("precinct_count") or 0)
    for scope, data in (district_crosswalk.get("scopes") or {}).items():
        bad_weight_sums = sum(abs(sum(float(v) for v in shares.values()) - 1.0) > 1e-9 for shares in data.get("weights", {}).values())
        record = {
            "scope": scope,
            "precincts_mapped": int(data.get("precincts_mapped") or 0),
            "precincts_unmapped": int(data.get("precincts_unmapped") or 0),
            "bad_weight_sums": bad_weight_sums,
        }
        crosswalk_scopes.append(record)
        if record["precincts_mapped"] != precinct_count or record["precincts_unmapped"] or bad_weight_sums:
            errors.append(f"district crosswalk integrity failure: {scope}")

    district_files = district_report.get("files") or []
    district_conservation_failures = sum(any(int(v) for v in row.get("conservation_delta", {}).values()) for row in district_files)
    if district_conservation_failures:
        errors.append(f"district conservation failures: {district_conservation_failures}")
    snapshot_calibrations = [row for row in district_files if row.get("calibration_target_file")]
    snapshot_comparisons = [row for row in district_files if row.get("snapshot_comparison_target_file")]
    skipped_snapshot_calibrations = [
        row for row in snapshot_comparisons
        if str(row.get("snapshot_calibration_status") or "").startswith("comparison_only")
    ]
    expected_snapshot_comparisons = int(district_report.get("snapshot_comparisons_expected") or 0)
    expected_snapshot_calibrations = int(district_report.get("snapshot_calibrations_expected") or 0)
    snapshot_calibration_failures = sum(
        int(row.get("calibration_districts") or 0) != int(row.get("calibration_expected_districts") or 0)
        or float(row.get("calibration_max_abs_share_delta_pp") or 0) > float(row.get("calibration_tolerance_pp") or 0)
        or any(int(v) for v in (row.get("calibration_conservation_delta") or {}).values())
        for row in snapshot_calibrations
    )
    if len(snapshot_calibrations) != expected_snapshot_calibrations:
        errors.append(
            f"expected {expected_snapshot_calibrations} district snapshot calibrations, found {len(snapshot_calibrations)}"
        )
    if len(snapshot_comparisons) != expected_snapshot_comparisons:
        errors.append(
            f"expected {expected_snapshot_comparisons} district snapshot comparisons, found {len(snapshot_comparisons)}"
        )
    if snapshot_calibration_failures:
        errors.append(f"snapshot calibration failures: {snapshot_calibration_failures}")
    report = {
        "summary": {
            "source_files": len(source_report.get("sources") or []),
            "contest_files": len(contests),
            "district_files": len(district_files),
            "district_crosswalk_scopes": len(crosswalk_scopes),
            "errors": len(errors),
            "warnings": len(warnings),
            "district_conservation_failures": district_conservation_failures,
            "snapshot_calibrations": len(snapshot_calibrations),
            "snapshot_calibration_failures": snapshot_calibration_failures,
            "snapshot_comparisons": len(snapshot_comparisons),
            "snapshot_calibrations_skipped": len(skipped_snapshot_calibrations),
        },
        "errors": errors,
        "warnings": warnings,
        "district_crosswalks": crosswalk_scopes,
        "contests": contests,
    }
    (REPO_ROOT / args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
