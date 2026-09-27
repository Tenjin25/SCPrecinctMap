#!/usr/bin/env python3
"""Review and promote population-backed congressional and Senate slivers."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("dem_votes", "rep_votes", "other_votes", "total_votes")
SELECTED = {"congressional", "state_senate"}
MANIFESTS = ("manifest.json", "state_house_2022_lines/manifest_2022_lines.json",
             "state_house_2024_lines/manifest_2024_lines.json")


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def totals(results: dict) -> dict[str, int]:
    return {field: sum(int(row[field]) for row in results.values()) for field in FIELDS}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-root", default="work/split_sliver_baseline")
    parser.add_argument("--staging-root", default="data/district_contests_county_constrained_staging")
    parser.add_argument("--output", default="data/district_contests_county_constrained_staging/"
                        "congress_senate_populated_sliver_promotion_2010_2024.json")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    live_root = ROOT / "data/district_contests"
    baseline_root = ROOT / args.baseline_root
    staging_root = ROOT / args.staging_root
    baseline_plans = read(baseline_root / "mixed_county_components.json")["plans"]
    staged_plans = read(staging_root / "mixed_county_components.json")["plans"]
    whole_counties = {}
    for scope in ("congressional", "state_senate_2022"):
        before = baseline_plans[scope]["whole_county_destinations"]
        after = staged_plans[scope]["whole_county_destinations"]
        if before != after:
            raise ValueError(f"Whole-county destinations changed: {scope}")
        whole_counties[scope] = len(after)

    selected = []
    house_checked = 0
    changed_rows = []
    already_promoted = 0
    by_scope = {scope: {"files": 0, "changed_district_rows": 0, "winner_changes": 0,
                        "max_abs_party_vote_change": 0, "max_abs_margin_change_pp": 0.0}
                for scope in sorted(SELECTED)}
    for manifest_rel in MANIFESTS:
        for entry in read(live_root / manifest_rel)["files"]:
            if not 2010 <= int(entry["year"]) <= 2024 or entry["contest_type"] in (
                    "us_house", "state_house", "state_senate"):
                continue
            relative = Path(manifest_rel).parent / entry["file"]
            live_path = live_root / relative
            staged_path = staging_root / relative
            before = read(baseline_root / relative)
            after = read(staged_path)
            current = read(live_path)
            before_results = before["general"]["results"]
            after_results = after["general"]["results"]
            current_results = current["general"]["results"]
            if set(before_results) != set(after_results) or totals(before_results) != totals(after_results):
                raise ValueError(f"District coverage or statewide totals changed: {relative}")
            if entry["scope"] not in SELECTED:
                house_checked += 1
                if before_results != after_results or current_results != before_results:
                    raise ValueError(f"House result changed: {relative}")
                continue
            if before["meta"].get("whole_county_vote_components") != after["meta"].get(
                    "whole_county_vote_components"):
                raise ValueError(f"Exact whole-county votes changed: {relative}")
            if current_results not in (before_results, after_results):
                raise ValueError(f"Live result differs from baseline and proposal: {relative}")
            already_promoted += current_results == after_results
            selected.append((relative, live_path, staged_path))
            summary = by_scope[entry["scope"]]
            summary["files"] += 1
            for district, old in before_results.items():
                new = after_results[district]
                deltas = {field: int(new[field]) - int(old[field]) for field in FIELDS[:3]}
                if not any(deltas.values()):
                    continue
                margin_delta = round(float(new["margin_pct"]) - float(old["margin_pct"]), 4)
                changed_rows.append({"file": relative.as_posix(), "district": district,
                                     "vote_deltas": deltas, "margin_change_pp": margin_delta,
                                     "winner_before": old["winner"], "winner_after": new["winner"]})
                summary["changed_district_rows"] += 1
                summary["winner_changes"] += old["winner"] != new["winner"]
                summary["max_abs_party_vote_change"] = max(
                    summary["max_abs_party_vote_change"], *(abs(value) for value in deltas.values()))
                summary["max_abs_margin_change_pp"] = max(
                    summary["max_abs_margin_change_pp"], abs(margin_delta))

    if len(selected) != 74 or house_checked != 111 or any(
            node["files"] != 37 or node["winner_changes"] for node in by_scope.values()):
        raise ValueError("Expected 37 congressional and 37 Senate files, 111 unchanged House files, and no winner changes")
    report = {"method": "Retain positive block-CVAP pieces below the 0.1% precinct-area cutoff "
              "in congressional and State Senate split allocations; preserve exact whole-county returns.",
              "whole_counties_unchanged": whole_counties,
              "house_files_unchanged": house_checked,
              "already_promoted_files": already_promoted,
              "by_scope": by_scope,
              "largest_changes": sorted(changed_rows, key=lambda row: abs(row["margin_change_pp"]),
                                        reverse=True)[:50]}
    if args.write:
        for _, live_path, staged_path in selected:
            shutil.copy2(staged_path, live_path)
        output = ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"mode": "write" if args.write else "audit", "whole_counties": whole_counties,
                      "house_files_unchanged": house_checked, "already_promoted_files": already_promoted,
                      "by_scope": by_scope}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
