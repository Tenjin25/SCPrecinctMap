#!/usr/bin/env python3
"""Promote statewide-total fixes on 2022 House lines with DRA-supported flips."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("dem_votes", "rep_votes", "other_votes", "total_votes")
EXPECTED = {
    "state_house_president_2012_2022_lines.json",
    "state_house_president_2016_2022_lines.json",
    "state_house_president_2020_2022_lines.json",
    "state_house_president_2024_2022_lines.json",
    "state_house_us_senate_2020_2022_lines.json",
    "state_house_us_senate_2022_2022_lines.json",
}


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
    promotion = json.loads((stage / "promotion_review_2010_2024.json").read_text(encoding="utf-8"))
    qa = {row["file"]: row for row in promotion["files"]}
    manifest = json.loads((stage / "state_house_2022_lines/manifest_2022_lines.json").read_text(encoding="utf-8"))
    entries = {entry["file"]: entry for entry in manifest["files"]}
    pending = []
    flips = 0
    for name in sorted(EXPECTED):
        entry = entries[name]
        relative = f"state_house_2022_lines/{name}"
        review = qa[relative]
        if (review["benchmark_rows"] != 124 or review["winner_changes"] <= 0 or
                review["benchmark_supported_flips"] != review["winner_changes"] or
                review["benchmark_opposed_flips"] or review["benchmark_neither_flips"]):
            raise ValueError(f"DRA winner support gate failed: {relative}")
        source = json.loads((sources / f"{entry['contest_type']}_{entry['year']}.json").read_text(encoding="utf-8"))
        staged_path = stage / relative
        live_path = live / relative
        staged = json.loads(staged_path.read_text(encoding="utf-8"))
        official = totals([row for row in source["rows"] if " - " not in row["county"]])
        if totals(list(staged["general"]["results"].values())) != official:
            raise ValueError(f"Staged statewide totals differ from official county rows: {relative}")
        pending.append((staged_path, live_path))
        flips += review["winner_changes"]
    if flips != 36:
        raise ValueError(f"Expected 36 DRA-supported winner changes, found {flips}")
    if args.write:
        for staged_path, live_path in pending:
            shutil.copyfile(staged_path, live_path)
    print(json.dumps({"mode": "write" if args.write else "dry-run", "files": len(pending),
                      "DRA_supported_winner_changes": flips, "held_2010_benchmark_files": 2}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
