#!/usr/bin/env python3
"""Record major-party competition in statewide contest dropdown manifests."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def major_party_contested(path: Path) -> bool:
    rows = json.loads(path.read_text(encoding="utf-8")).get("rows") or []
    county_rows = [row for row in rows if " - " not in str(row.get("county") or "")]
    if not county_rows:
        raise ValueError(f"No county summary rows in {path}")
    return (sum(int(row.get("dem_votes") or 0) for row in county_rows) > 0 and
            sum(int(row.get("rep_votes") or 0) for row in county_rows) > 0)


def main() -> int:
    contests = ROOT / "data/contests_2025_crosswalked"
    manifest_path = contests / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        entry["major_party_contested"] = major_party_contested(contests / entry["file"])
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    districts = ROOT / "data/district_contests"
    manifest_paths = (districts / "manifest.json",
                      districts / "state_house_2022_lines/manifest_2022_lines.json",
                      districts / "state_house_2024_lines/manifest_2024_lines.json")
    contest_flags = {(entry["contest_type"], int(entry["year"])): entry["major_party_contested"]
                     for entry in manifest["files"]}
    marked = 0
    for path in manifest_paths:
        district_manifest = json.loads(path.read_text(encoding="utf-8"))
        for entry in district_manifest["files"]:
            flag = contest_flags.get((entry["contest_type"], int(entry["year"])))
            if flag is not None:
                entry["major_party_contested"] = flag
                marked += 1
        path.write_text(json.dumps(district_manifest, indent=2) + "\n", encoding="utf-8")
    print(f"marked {len(manifest['files'])} statewide contests and {marked} district manifest entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
