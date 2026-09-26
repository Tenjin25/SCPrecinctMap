#!/usr/bin/env python3
"""Audit statewide vote conservation for legislative district projections."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("dem_votes", "rep_votes", "other_votes", "total_votes")


def totals(rows: list[dict]) -> dict[str, int]:
    return {field: sum(int(row.get(field) or 0) for row in rows) for field in FIELDS}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-root", default="data/district_contests_county_constrained_staging")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    stage = ROOT / args.staging_root
    live = ROOT / "data/district_contests"
    sources = ROOT / "data/contests_2025_crosswalked"
    manifests = [stage / "manifest.json", stage / "state_house_2022_lines/manifest_2022_lines.json",
                 stage / "state_house_2024_lines/manifest_2024_lines.json"]
    files = []
    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest["files"]:
            year = int(entry["year"])
            scope = (manifest_path.parent.name if manifest_path.parent != stage else entry["scope"])
            if not 2010 <= year <= 2024 or scope == "congressional" or int(entry.get("rows") or 0) == 0:
                continue
            relative = manifest_path.parent.relative_to(stage) / entry["file"]
            if not (live / relative).exists():
                raise FileNotFoundError(live / relative)
            source = json.loads((sources / f"{entry['contest_type']}_{year}.json").read_text(encoding="utf-8"))
            canonical = totals([row for row in source["rows"] if " - " not in row["county"]])
            staged = json.loads((stage / relative).read_text(encoding="utf-8"))
            served = json.loads((live / relative).read_text(encoding="utf-8"))
            current = served["general"]["results"]
            proposed = staged["general"]["results"]
            if current.keys() != proposed.keys():
                raise ValueError(f"District set changed: {relative}")
            old_totals = totals(list(current.values()))
            new_totals = totals(list(proposed.values()))
            changes = {district: round(float(proposed[district]["margin_pct"]) -
                                       float(current[district]["margin_pct"]), 4) for district in proposed}
            flips = [district for district in proposed if current[district]["winner"] != proposed[district]["winner"]]
            files.append({"file": relative.as_posix(), "year": year, "scope": scope,
                          "contest": entry["contest_type"], "districts": len(proposed),
                          "canonical_statewide_totals": canonical,
                          "served_statewide_delta": {field: old_totals[field] - canonical[field] for field in FIELDS},
                          "staged_statewide_delta": {field: new_totals[field] - canonical[field] for field in FIELDS},
                          "winner_changes": len(flips), "changed_winner_districts": flips,
                          "largest_margin_change_pp": max(abs(value) for value in changes.values()),
                          "precinct_votes_county_share_fallback": staged["meta"]["precinct_votes_county_share_fallback"]})
    groups = defaultdict(list)
    for row in files:
        groups[(row["year"], row["scope"])].append(row)
    by_year_scope = [{"year": year, "scope": scope, "files": len(rows),
                      "served_statewide_failures": sum(any(row["served_statewide_delta"].values()) for row in rows),
                      "staged_statewide_failures": sum(any(row["staged_statewide_delta"].values()) for row in rows),
                      "no_winner_change_files": sum(row["winner_changes"] == 0 for row in rows),
                      "winner_changes": sum(row["winner_changes"] for row in rows),
                      "largest_served_vote_shortfall": max(max(0, -row["served_statewide_delta"]["total_votes"])
                                                           for row in rows),
                      "largest_margin_change_pp": max(row["largest_margin_change_pp"] for row in rows)}
                     for (year, scope), rows in sorted(groups.items())]
    report = {"method": "Compare served and county-constrained staged district sums with official county-row totals for 2010–2024 statewide contests on State House and State Senate lines.",
              "summary": {"files": len(files),
                          "served_statewide_failures": sum(any(row["served_statewide_delta"].values()) for row in files),
                          "staged_statewide_failures": sum(any(row["staged_statewide_delta"].values()) for row in files),
                          "no_winner_change_files": sum(row["winner_changes"] == 0 for row in files),
                          "winner_changes": sum(row["winner_changes"] for row in files)},
              "by_year_scope": by_year_scope,
              "files": sorted(files, key=lambda row: (-max(0, -row["served_statewide_delta"]["total_votes"]),
                                                     -row["largest_margin_change_pp"]))}
    if args.write:
        (stage / "statewide_district_undercount_2010_2024.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"summary": report["summary"], "by_year_scope": by_year_scope}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
