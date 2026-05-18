#!/usr/bin/env python3
import json
import os
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    dist_dir = repo_root / "data" / "district_contests"
    out_dir = dist_dir / "state_house_2022_lines"
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = []

    for src in sorted(dist_dir.glob("state_house_*.json")):
        if src.name == "manifest.json":
            continue
        if src.name.endswith("_2022_lines.json"):
            continue

        # state_house_{contest_type}_{year}.json
        base = src.stem
        parts = base.split("_")
        if len(parts) < 4:
            continue
        year = parts[-1]
        contest_type = "_".join(parts[2:-1])
        if not year.isdigit():
            continue

        dst_name = f"state_house_{contest_type}_{year}_2022_lines.json"
        dst = out_dir / dst_name

        with src.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)

        payload.setdefault("meta", {})
        payload["meta"]["district_lines_version"] = "2022_lines"
        payload["meta"]["district_lines_file"] = "data/tileset/sc_state_house_2022_lines_tileset.geojson"
        payload["meta"]["source_file"] = src.name

        with dst.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.write("\n")

        rows = len((payload.get("general") or {}).get("results") or {})
        manifest.append(
            {
                "scope": "state_house",
                "contest_type": contest_type,
                "year": int(year),
                "file": dst_name,
                "rows": rows,
                "district_lines": "2022_lines",
            }
        )

    manifest.sort(key=lambda x: (-x["year"], x["contest_type"]))
    with (out_dir / "manifest_2022_lines.json").open("w", encoding="utf-8") as fh:
        json.dump({"files": manifest}, fh, indent=2)
        fh.write("\n")

    print(f"wrote {len(manifest)} files to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
