#!/usr/bin/env python3
"""Promote validated 2010–2024 statewide contests on congressional lines."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("dem_votes", "rep_votes", "other_votes", "total_votes")


def sums(rows: list[dict]) -> dict[str, int]:
    return {field: sum(int(row.get(field) or 0) for row in rows) for field in FIELDS}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-root", default="data/district_contests_county_constrained_staging")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    stage = ROOT / args.staging_root
    live = ROOT / "data/district_contests"
    sources = ROOT / "data/contests_2025_crosswalked"
    manifest = json.loads((sources / "manifest.json").read_text(encoding="utf-8"))
    eligible_sources = {entry["file"] for entry in manifest["files"]}
    pending = []
    for staged_path in sorted(stage.glob("congressional_*.json")):
        contest, year_text = staged_path.stem.removeprefix("congressional_").rsplit("_", 1)
        year = int(year_text)
        if not 2010 <= year <= 2024:
            continue
        if f"{contest}_{year}.json" not in eligible_sources:
            continue
        live_path = live / staged_path.name
        source_path = sources / f"{contest}_{year}.json"
        if not live_path.exists() or not source_path.exists():
            raise FileNotFoundError(f"Missing live or source file for {staged_path.name}")
        source = json.loads(source_path.read_text(encoding="utf-8"))
        staged = json.loads(staged_path.read_text(encoding="utf-8"))
        served = json.loads(live_path.read_text(encoding="utf-8"))
        official = sums([row for row in source["rows"] if " - " not in row["county"]])
        staged_results = staged["general"]["results"]
        served_results = served["general"]["results"]
        if sums(list(staged_results.values())) != official:
            raise ValueError(f"Staged statewide totals differ from official county rows: {staged_path.name}")
        if staged_results.keys() != served_results.keys():
            raise ValueError(f"District set changed: {staged_path.name}")
        if any(staged_results[district]["winner"] != served_results[district]["winner"]
               for district in staged_results):
            raise ValueError(f"District winner changed: {staged_path.name}")
        pending.append((staged_path, live_path))
    if len(pending) != 37:
        raise ValueError(f"Expected 37 eligible congressional contest files, found {len(pending)}")
    if args.write:
        for staged_path, live_path in pending:
            shutil.copyfile(staged_path, live_path)
    print(json.dumps({"mode": "write" if args.write else "dry-run", "files": len(pending),
                      "years": sorted({int(path.stem.rsplit("_", 1)[-1]) for path, _ in pending}),
                      "held_years_excluded": [2006, 2008]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
