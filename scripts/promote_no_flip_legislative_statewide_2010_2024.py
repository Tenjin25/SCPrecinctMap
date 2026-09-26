#!/usr/bin/env python3
"""Promote statewide-total fixes with unchanged legislative district winners."""

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
    held = []
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
            source_path = sources / f"{entry['contest_type']}_{year}.json"
            source = json.loads(source_path.read_text(encoding="utf-8"))
            staged = json.loads(staged_path.read_text(encoding="utf-8"))
            served = json.loads(live_path.read_text(encoding="utf-8"))
            official = totals([row for row in source["rows"] if " - " not in row["county"]])
            current = served["general"]["results"]
            proposed = staged["general"]["results"]
            if totals(list(proposed.values())) != official:
                raise ValueError(f"Staged statewide totals differ from official county rows: {relative}")
            if current.keys() != proposed.keys():
                raise ValueError(f"District set changed: {relative}")
            flips = [district for district in proposed if current[district]["winner"] != proposed[district]["winner"]]
            if flips:
                held.append(relative.as_posix())
                continue
            fallback = int(staged["meta"]["precinct_votes_county_share_fallback"])
            fallback_pct = fallback / max(1, official["total_votes"]) * 100
            if fallback_pct > 0.25:
                held.append(relative.as_posix())
                continue
            pending.append((staged_path, live_path))
    if len(pending) != 57 or len(held) != 91:
        raise ValueError(f"Expected 57 no-flip files and 91 held files; found {len(pending)} and {len(held)}")
    if args.write:
        for staged_path, live_path in pending:
            shutil.copyfile(staged_path, live_path)
    print(json.dumps({"mode": "write" if args.write else "dry-run", "promoted_files": len(pending),
                      "held_files_with_winner_changes": len(held), "held_years_excluded": [2006, 2008]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
