#!/usr/bin/env python3
"""
Build SC State House superintendent district results on 2024 lines.

This script aggregates statewide superintendent precinct slices onto the
`sc_state_house_2024_lines_tileset.geojson` district geometry using precinct
centroid point-in-polygon assignment.

Outputs:
  data/district_contests/state_house_2024_lines/state_house_superintendent_<year>_2024_lines.json
  data/district_contests/state_house_2024_lines/manifest_2024_lines.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
OUT_DIR = DATA_DIR / "district_contests" / "state_house_2024_lines"
CONTEST_YEARS = (2010, 2014, 2018, 2022)
CONTEST_TYPE = "superintendent"


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=False)
        fh.write("\n")
    tmp.replace(path)


def _build_one_year(year: int) -> tuple[dict[str, Any], dict[str, Any]] | None:
    sys.path.insert(0, str(REPO_ROOT))
    import build_data  # type: ignore

    contest_path = DATA_DIR / "contests" / f"{CONTEST_TYPE}_{year}.json"
    centroids_path = DATA_DIR / "precinct_centroids.geojson"
    districts_path = DATA_DIR / "tileset" / "sc_state_house_2024_lines_tileset.geojson"
    if not (contest_path.exists() and centroids_path.exists() and districts_path.exists()):
        return None

    contest = _load_json(contest_path) or {}
    rows = contest.get("rows") or []
    precinct_rows = [
        r for r in rows
        if isinstance(r, dict) and " - " in str(r.get("county") or "")
    ]
    if not precinct_rows:
        return None

    centroids = _load_json(centroids_path) or {}
    districts = _load_json(districts_path) or {}

    polys: list[tuple[tuple[float, float, float, float], dict[str, Any], str]] = []
    for feat in districts.get("features", []) or []:
        geom = (feat or {}).get("geometry") or {}
        props = (feat or {}).get("properties") or {}
        dnum = build_data._parse_district_num(props.get("SLDLST"))
        if not dnum:
            continue
        bbox = build_data._geom_bbox(geom.get("coordinates"))
        polys.append((bbox, geom, dnum))
    if not polys:
        return None

    precinct_to_dist: dict[str, str] = {}
    for feat in centroids.get("features", []) or []:
        geom = (feat or {}).get("geometry") or {}
        if geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        props = (feat or {}).get("properties") or {}
        pn = (props.get("precinct_norm") or "").strip().upper()
        if not pn:
            continue
        x, y = float(coords[0]), float(coords[1])
        chosen = ""
        for (minx, miny, maxx, maxy), district_geom, dnum in polys:
            if x < minx or x > maxx or y < miny or y > maxy:
                continue
            if build_data._point_in_geometry(x, y, district_geom):
                chosen = dnum
                break
        if chosen:
            precinct_to_dist[pn] = chosen

    by_dist: dict[str, dict[str, Any]] = {}
    matched = 0
    dem_name = ""
    rep_name = ""
    for row in precinct_rows:
        key = (row.get("county") or "").strip()
        pn = build_data.normalize(key)
        dnum = precinct_to_dist.get(pn)
        if not dnum:
            continue
        matched += 1
        node = by_dist.setdefault(dnum, {"dem": 0, "rep": 0, "other": 0})
        node["dem"] += int(row.get("dem_votes") or 0)
        node["rep"] += int(row.get("rep_votes") or 0)
        node["other"] += int(row.get("other_votes") or 0)
        if not dem_name:
            dem_name = str(row.get("dem_candidate") or "").strip()
        if not rep_name:
            rep_name = str(row.get("rep_candidate") or "").strip()

    results: dict[str, Any] = {}
    for dnum, totals in sorted(by_dist.items(), key=lambda kv: build_data._district_sort_key(kv[0])):
        dem_votes = int(totals["dem"])
        rep_votes = int(totals["rep"])
        other_votes = int(totals["other"])
        total_votes = dem_votes + rep_votes + other_votes
        margin = rep_votes - dem_votes
        margin_pct = round((margin / total_votes) * 100, 4) if total_votes else 0
        winner = "R" if margin > 0 else ("D" if margin < 0 else "T")
        results[str(dnum)] = {
            "dem_votes": dem_votes,
            "rep_votes": rep_votes,
            "other_votes": other_votes,
            "total_votes": total_votes,
            "dem_candidate": dem_name,
            "rep_candidate": rep_name,
            "margin": margin,
            "margin_pct": margin_pct,
            "winner": winner,
            "color": build_data.margin_color(margin_pct),
        }

    payload = {
        "general": {"results": results},
        "meta": {
            "match_coverage_pct": round((matched / len(precinct_rows)) * 100, 4) if precinct_rows else 0,
            "precinct_rows_total": len(precinct_rows),
            "precinct_rows_matched": matched,
            "precinct_rows_block_weighted": 0,
            "district_lines_version": "2024_lines",
            "district_lines_file": "data/tileset/sc_state_house_2024_lines_tileset.geojson",
            "source_file": contest_path.name,
            "assignment_method": "precinct_centroid_2024_lines",
        },
    }
    manifest_entry = {
        "scope": "state_house",
        "contest_type": CONTEST_TYPE,
        "year": int(year),
        "file": f"state_house_{CONTEST_TYPE}_{year}_2024_lines.json",
        "rows": len(results),
        "district_lines": "2024_lines",
    }
    return payload, manifest_entry


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    written = 0
    for year in CONTEST_YEARS:
        built = _build_one_year(year)
        if not built:
            continue
        payload, manifest_entry = built
        out_path = OUT_DIR / manifest_entry["file"]
        _write_json(out_path, payload)
        manifest.append(manifest_entry)
        written += 1

    manifest.sort(key=lambda x: (-x["year"], x["contest_type"]))
    _write_json(OUT_DIR / "manifest_2024_lines.json", {"files": manifest})
    print(f"wrote {written} superintendent 2024-line state house file(s) to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
