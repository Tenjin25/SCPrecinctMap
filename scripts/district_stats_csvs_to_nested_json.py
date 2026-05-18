#!/usr/bin/env python3
import csv
import glob
import json
import os
import re


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")


def main() -> int:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src_dir = os.path.join(repo_root, "Data", "2022 state house files")
    out_path = os.path.join(repo_root, "data", "district_stats_state_house_nested.json")
    geojson_path = os.path.join(repo_root, "data", "tileset", "sc_state_house_2022_lines_tileset.geojson")

    payload = {
        "scope": "state_house",
        "district_lines_geojson": geojson_path,
        "files": {},
    }

    for path in sorted(glob.glob(os.path.join(src_dir, "district-statistics *.csv"))):
        base = os.path.basename(path)
        m = re.match(r"district-statistics\s+(\d{4})\s+(.+)\.csv$", base, re.IGNORECASE)
        if not m:
            continue
        year = m.group(1)
        contest = _slug(m.group(2))
        districts = {}
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                rid = (row.get("ID") or "").strip().strip('"')
                if not rid:
                    continue
                districts[rid] = {
                    "metrics": {
                        k: v
                        for k, v in row.items()
                        if k and k != "ID"
                    },
                }
        payload["files"].setdefault(year, {})[contest] = {
            "source_csv": path,
            "districts": districts,
        }

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
