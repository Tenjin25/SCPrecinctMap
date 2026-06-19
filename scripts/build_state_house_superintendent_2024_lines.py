#!/usr/bin/env python3
"""
Build SC State House superintendent district results on 2024 lines.

This script aggregates statewide superintendent precinct slices onto the
`sc_state_house_2024_lines_tileset.geojson` district geometry using precinct
polygon overlap shares when available, falling back to centroid
point-in-polygon assignment for any unmatched precincts.

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


def _alloc_int(votes: int, shares: list[tuple[str, float]]) -> dict[str, int]:
    if votes <= 0 or not shares:
        return {}
    floors: dict[str, int] = {}
    fracs: list[tuple[float, str]] = []
    used = 0
    for dnum, share in shares:
        s = float(share or 0)
        if s <= 0:
            continue
        exact = votes * s
        base = int(exact // 1)
        floors[dnum] = floors.get(dnum, 0) + base
        used += base
        fracs.append((exact - base, dnum))
    remain = votes - used
    if remain > 0 and fracs:
        fracs.sort(reverse=True, key=lambda x: x[0])
        for _, dnum in fracs[:remain]:
            floors[dnum] = floors.get(dnum, 0) + 1
    return floors


def _load_precinct_to_dist_shares() -> dict[str, dict[str, float]]:
    sys.path.insert(0, str(REPO_ROOT))
    import build_data  # type: ignore
    import geopandas as gpd  # type: ignore

    precincts_path = DATA_DIR / "Voting_Precincts.geojson"
    districts_path = DATA_DIR / "tileset" / "sc_state_house_2024_lines_tileset.geojson"
    if not (precincts_path.exists() and districts_path.exists()):
        return {}

    precincts = gpd.read_file(precincts_path)
    districts = gpd.read_file(districts_path)
    if precincts.empty or districts.empty:
        return {}
    if "precinct_norm" not in precincts.columns or "geometry" not in precincts.columns:
        return {}
    if "SLDLST" not in districts.columns or "geometry" not in districts.columns:
        return {}

    precincts = precincts[["precinct_norm", "geometry"]].copy()
    precincts["precinct_norm"] = precincts["precinct_norm"].astype(str).str.strip().str.upper()
    precincts = precincts[precincts["precinct_norm"] != ""].copy()
    precincts = precincts.dissolve(by="precinct_norm", as_index=False, aggfunc="first")

    districts = districts[["SLDLST", "geometry"]].copy()
    districts["district_num"] = districts["SLDLST"].map(build_data._parse_district_num)
    districts = districts[districts["district_num"].notna()].copy()
    districts = districts[["district_num", "geometry"]].dissolve(by="district_num", as_index=False, aggfunc="first")

    if precincts.crs is None and districts.crs is None:
        precincts = precincts.set_crs("EPSG:4326")
        districts = districts.set_crs("EPSG:4326")
    elif precincts.crs is None:
        precincts = precincts.set_crs(districts.crs)
    elif districts.crs is None:
        districts = districts.set_crs(precincts.crs)
    elif precincts.crs != districts.crs:
        districts = districts.to_crs(precincts.crs)

    precincts = precincts.to_crs("EPSG:3857")
    districts = districts.to_crs("EPSG:3857")
    precincts["precinct_area_m2"] = precincts.geometry.area
    precinct_area = dict(zip(precincts["precinct_norm"], precincts["precinct_area_m2"]))

    inter = gpd.overlay(
        precincts[["precinct_norm", "geometry"]],
        districts[["district_num", "geometry"]],
        how="intersection",
        keep_geom_type=False,
    )

    overlap_area: dict[tuple[str, str], float] = {}
    if not inter.empty:
        inter["overlap_area_m2"] = inter.geometry.area
        for _, row in inter.iterrows():
            pn = str(row["precinct_norm"]).strip().upper()
            dnum = str(row["district_num"]).strip()
            if not pn or not dnum:
                continue
            key = (pn, dnum)
            overlap_area[key] = float(overlap_area.get(key) or 0.0) + float(row["overlap_area_m2"] or 0.0)

    out: dict[str, dict[str, float]] = {}
    by_precinct: dict[str, list[tuple[str, float]]] = {}
    for (pn, dnum), area in overlap_area.items():
        by_precinct.setdefault(pn, []).append((dnum, area))
    for pn, ranked in by_precinct.items():
        total_area = float(precinct_area.get(pn) or 0.0)
        if total_area <= 0:
            continue
        shares: dict[str, float] = {}
        for dnum, area in ranked:
            share = float(area or 0.0) / total_area
            if share > 0:
                shares[dnum] = share
        if shares:
            out[pn] = shares
    return out


def _build_one_year(year: int) -> tuple[dict[str, Any], dict[str, Any]] | None:
    sys.path.insert(0, str(REPO_ROOT))
    import build_data  # type: ignore

    contest_path = DATA_DIR / "contests" / f"{CONTEST_TYPE}_{year}.json"
    centroids_path = DATA_DIR / "precinct_centroids.geojson"
    districts_path = DATA_DIR / "tileset" / "sc_state_house_2024_lines_tileset.geojson"
    precincts_path = DATA_DIR / "Voting_Precincts.geojson"
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
    precinct_to_dist_shares = _load_precinct_to_dist_shares() if precincts_path.exists() else {}

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
    matched_weighted = 0
    dem_name = ""
    rep_name = ""
    for row in precinct_rows:
        key = (row.get("county") or "").strip()
        pn = build_data.normalize(key)
        shares_map = precinct_to_dist_shares.get(pn) or {}
        dem_votes = int(row.get("dem_votes") or 0)
        rep_votes = int(row.get("rep_votes") or 0)
        other_votes = int(row.get("other_votes") or 0)
        if shares_map:
            matched += 1
            matched_weighted += 1
            shares = [(str(d), float(s or 0)) for d, s in shares_map.items() if float(s or 0) > 0]
            dem_alloc = _alloc_int(dem_votes, shares)
            rep_alloc = _alloc_int(rep_votes, shares)
            oth_alloc = _alloc_int(other_votes, shares)
            for dnum in sorted(set(dem_alloc) | set(rep_alloc) | set(oth_alloc), key=build_data._district_sort_key):
                node = by_dist.setdefault(dnum, {"dem": 0, "rep": 0, "other": 0})
                node["dem"] += dem_alloc.get(dnum, 0)
                node["rep"] += rep_alloc.get(dnum, 0)
                node["other"] += oth_alloc.get(dnum, 0)
        else:
            dnum = precinct_to_dist.get(pn)
            if not dnum:
                continue
            matched += 1
            node = by_dist.setdefault(dnum, {"dem": 0, "rep": 0, "other": 0})
            node["dem"] += dem_votes
            node["rep"] += rep_votes
            node["other"] += other_votes
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
            "precinct_rows_overlap_weighted": matched_weighted,
            "district_lines_version": "2024_lines",
            "district_lines_file": "data/tileset/sc_state_house_2024_lines_tileset.geojson",
            "source_file": contest_path.name,
            "assignment_method": "precinct_polygon_overlap_with_centroid_fallback_2024_lines",
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
