#!/usr/bin/env python3
"""Promote remaining county-constrained statewide legislative projections."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("dem_votes", "rep_votes", "other_votes", "total_votes")
MANIFESTS = ("manifest.json", "state_house_2022_lines/manifest_2022_lines.json",
             "state_house_2024_lines/manifest_2024_lines.json")


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
    pending = []
    flips = 0
    worst_fallback_pct = 0.0
    lowest_match_pct = 100.0
    for relative_manifest in MANIFESTS:
        manifest_path = stage / relative_manifest
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest["files"]:
            year = int(entry["year"])
            if not 2010 <= year <= 2024 or entry["scope"] == "congressional" or int(entry.get("rows") or 0) == 0:
                continue
            relative = Path(relative_manifest).parent / entry["file"]
            staged_path = stage / relative
            live_path = live / relative
            source = json.loads((sources / f"{entry['contest_type']}_{year}.json").read_text(encoding="utf-8"))
            staged = json.loads(staged_path.read_text(encoding="utf-8"))
            served = json.loads(live_path.read_text(encoding="utf-8"))
            official = totals([row for row in source["rows"] if " - " not in row["county"]])
            current = served["general"]["results"]
            proposed = staged["general"]["results"]
            if current.keys() != proposed.keys():
                raise ValueError(f"District set changed: {relative}")
            if totals(list(proposed.values())) != official:
                raise ValueError(f"Staged statewide totals differ from county rows: {relative}")
            if any(int(value) != 0 for value in staged["meta"]["conservation_delta"].values()):
                raise ValueError(f"County reconciliation failed: {relative}")
            if totals(list(current.values())) == official:
                continue
            fallback = int(staged["meta"]["precinct_votes_county_share_fallback"])
            fallback_pct = fallback / max(1, official["total_votes"]) * 100
            match_pct = float(staged["meta"]["match_coverage_pct"])
            if fallback_pct > 0.35 or match_pct < 97.5:
                raise ValueError(f"Geographic coverage gate failed: {relative}")
            pending.append((staged_path, live_path))
            flips += sum(current[district]["winner"] != proposed[district]["winner"] for district in proposed)
            worst_fallback_pct = max(worst_fallback_pct, fallback_pct)
            lowest_match_pct = min(lowest_match_pct, match_pct)
    if len(pending) != 85 or flips != 296:
        raise ValueError(f"Expected 85 files and 296 winner changes; found {len(pending)} and {flips}")
    if args.write:
        for staged_path, live_path in pending:
            shutil.copyfile(staged_path, live_path)
    print(json.dumps({"mode": "write" if args.write else "dry-run", "files": len(pending),
                      "winner_changes": flips, "lowest_match_coverage_pct": lowest_match_pct,
                      "highest_fallback_vote_pct": round(worst_fallback_pct, 4),
                      "held_years_excluded": [2006, 2008]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
