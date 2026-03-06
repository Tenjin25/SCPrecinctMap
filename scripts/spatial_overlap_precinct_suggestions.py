#!/usr/bin/env python3
import argparse
import csv
import json
import os
import re
from collections import defaultdict

import geopandas as gpd


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 .\-]", "", str(value or ""))).strip().upper()


def to_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


def load_target_polygons(path: str) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    gdf = gdf[["county_nam", "prec_id", "geometry"]].copy()
    gdf["county_nam"] = gdf["county_nam"].astype(str).str.strip()
    gdf["prec_id"] = gdf["prec_id"].astype(str).str.strip()
    gdf["target_key_norm"] = (gdf["county_nam"] + " - " + gdf["prec_id"]).map(norm)
    gdf["target_key_display"] = gdf["county_nam"] + " - " + gdf["prec_id"]
    gdf["county_norm"] = gdf["county_nam"].map(norm)
    gdf = gdf[gdf["target_key_norm"].str.contains(" - ", regex=False)]
    return gdf


def load_source_polygons(path: str) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    gdf = gdf[["COUNTY_NAM", "PCTNAME", "geometry"]].copy()
    gdf["COUNTY_NAM"] = gdf["COUNTY_NAM"].astype(str).str.strip()
    gdf["PCTNAME"] = gdf["PCTNAME"].astype(str).str.strip()
    gdf["source_key_norm"] = (gdf["COUNTY_NAM"] + " - " + gdf["PCTNAME"]).map(norm)
    gdf["source_key_display"] = gdf["COUNTY_NAM"].str.title() + " - " + gdf["PCTNAME"].str.title()
    gdf["county_norm"] = gdf["COUNTY_NAM"].map(norm)
    gdf = gdf[gdf["source_key_norm"].str.contains(" - ", regex=False)]
    gdf = gdf[["source_key_norm", "source_key_display", "county_norm", "geometry"]]
    # Dissolve to one geometry per source key.
    gdf = gdf.dissolve(by="source_key_norm", as_index=False, aggfunc="first")
    return gdf


def load_unmatched_contest_rows(contest_json_path: str, target_key_norms: set[str]) -> dict[str, dict]:
    with open(contest_json_path, encoding="utf-8") as fh:
        payload = json.load(fh) or {}
    out: dict[str, dict] = {}
    for row in payload.get("rows", []) or []:
        if not isinstance(row, dict):
            continue
        key = str(row.get("county") or "").strip()
        if " - " not in key:
            continue
        key_norm = norm(key)
        if key_norm in target_key_norms:
            continue
        node = out.setdefault(
            key_norm,
            {
                "raw_key": key,
                "dem_votes": 0,
                "rep_votes": 0,
                "other_votes": 0,
                "total_votes": 0,
            },
        )
        node["dem_votes"] += to_int(row.get("dem_votes"))
        node["rep_votes"] += to_int(row.get("rep_votes"))
        node["other_votes"] += to_int(row.get("other_votes"))
        node["total_votes"] += to_int(row.get("total_votes"))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Suggest unresolved precinct mappings using spatial overlap.")
    ap.add_argument("--base", default=os.path.join(os.path.dirname(__file__), ".."), help="Repo base path")
    ap.add_argument("--contest", default="president", help="Contest type")
    ap.add_argument("--year", default="2024", help="Election year")
    ap.add_argument(
        "--source-shp",
        default=os.path.join("..", "Data", "sc_2024_gen_prec", "sc_2024_gen_no_splits_prec", "sc_2024_gen_no_splits_prec.shp"),
        help="Source precinct shapefile path (absolute or relative to base)",
    )
    ap.add_argument(
        "--target-geojson",
        default=os.path.join("data", "Voting_Precincts.geojson"),
        help="Target precinct geojson path (absolute or relative to base)",
    )
    ap.add_argument("--top-n", type=int, default=3, help="How many candidate targets to include")
    ap.add_argument("--alias-threshold", type=float, default=0.92, help="Top overlap share needed for alias recommendation")
    ap.add_argument("--alias-gap", type=float, default=0.20, help="Min gap between top-1 and top-2 for alias recommendation")
    ap.add_argument("--split-min-share", type=float, default=0.10, help="Min overlap share to include in split recommendation")
    ap.add_argument("--split-coverage", type=float, default=0.90, help="Min covered source area for split recommendation")
    ap.add_argument(
        "--out",
        default="",
        help="Output CSV path (default: scripts/out/spatial_overlap_suggestions_<contest>_<year>.csv)",
    )
    args = ap.parse_args()

    base = os.path.abspath(args.base)
    contest_path = os.path.join(base, "data", "contests", f"{args.contest}_{args.year}.json")
    source_shp = args.source_shp if os.path.isabs(args.source_shp) else os.path.abspath(os.path.join(base, args.source_shp))
    target_geojson = (
        args.target_geojson if os.path.isabs(args.target_geojson) else os.path.join(base, args.target_geojson)
    )
    out_path = (
        args.out
        if args.out
        else os.path.join(base, "scripts", "out", f"spatial_overlap_suggestions_{args.contest}_{args.year}.csv")
    )
    out_path = out_path if os.path.isabs(out_path) else os.path.join(base, out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    if not os.path.exists(contest_path):
        raise SystemExit(f"Missing contest JSON: {contest_path}")
    if not os.path.exists(source_shp):
        raise SystemExit(f"Missing source shapefile: {source_shp}")
    if not os.path.exists(target_geojson):
        raise SystemExit(f"Missing target geojson: {target_geojson}")

    target = load_target_polygons(target_geojson)
    target_key_norms = set(target["target_key_norm"].tolist())
    unmatched = load_unmatched_contest_rows(contest_path, target_key_norms)
    if not unmatched:
        print("No unmatched precinct rows found.")
        return

    source = load_source_polygons(source_shp)
    source = source[source["source_key_norm"].isin(unmatched.keys())].copy()

    if source.empty:
        raise SystemExit("No unmatched contest keys were found in the source shapefile.")

    # Align CRS and project for area calculations.
    if source.crs is None and target.crs is None:
        source = source.set_crs("EPSG:4326")
        target = target.set_crs("EPSG:4326")
    elif source.crs is None and target.crs is not None:
        source = source.set_crs(target.crs)
    elif source.crs is not None and target.crs is None:
        target = target.set_crs(source.crs)
    elif source.crs != target.crs:
        target = target.to_crs(source.crs)

    source = source.to_crs("EPSG:3857")
    target = target.to_crs("EPSG:3857")

    source["source_area"] = source.geometry.area
    source_area = dict(zip(source["source_key_norm"], source["source_area"]))

    county_to_source_keys: dict[str, list[str]] = defaultdict(list)
    for key_norm in unmatched.keys():
        county = key_norm.split(" - ", 1)[0] if " - " in key_norm else ""
        county_to_source_keys[county].append(key_norm)

    rows_out: list[dict[str, str]] = []
    seen_source: set[str] = set()

    for county_norm, source_keys in county_to_source_keys.items():
        s = source[source["source_key_norm"].isin(source_keys)].copy()
        t = target[target["county_norm"] == county_norm].copy()
        if s.empty:
            continue
        if t.empty:
            for src_key in s["source_key_norm"].tolist():
                seen_source.add(src_key)
                votes = unmatched[src_key]
                rows_out.append(
                    {
                        "source_key_norm": src_key,
                        "source_key_raw": votes["raw_key"],
                        "total_votes": str(votes["total_votes"]),
                        "dem_votes": str(votes["dem_votes"]),
                        "rep_votes": str(votes["rep_votes"]),
                        "other_votes": str(votes["other_votes"]),
                        "coverage_ratio": "0.0000",
                        "recommendation": "manual_review",
                        "recommended_target": "",
                        "recommended_split_json": "",
                    }
                )
            continue

        inter = gpd.overlay(
            s[["source_key_norm", "geometry"]],
            t[["target_key_norm", "target_key_display", "geometry"]],
            how="intersection",
            keep_geom_type=False,
        )

        grouped = defaultdict(list)
        if not inter.empty:
            inter["area"] = inter.geometry.area
            for _, row in inter.iterrows():
                src = str(row["source_key_norm"])
                dst = str(row["target_key_norm"])
                display = str(row["target_key_display"])
                area = float(row["area"])
                grouped[src].append((dst, display, area))

        for src_key in s["source_key_norm"].tolist():
            seen_source.add(src_key)
            votes = unmatched[src_key]
            src_area = float(source_area.get(src_key) or 0.0)
            matches = defaultdict(float)
            match_display = {}
            for dst, display, area in grouped.get(src_key, []):
                matches[dst] += float(area)
                match_display[dst] = display

            ranked = sorted(matches.items(), key=lambda kv: kv[1], reverse=True)
            ranked_with_share = []
            covered = 0.0
            for dst, area in ranked:
                share = (area / src_area) if src_area > 0 else 0.0
                covered += share
                ranked_with_share.append((dst, match_display.get(dst, dst), share))

            top_share = ranked_with_share[0][2] if ranked_with_share else 0.0
            second_share = ranked_with_share[1][2] if len(ranked_with_share) > 1 else 0.0
            top_target = ranked_with_share[0][1] if ranked_with_share else ""

            rec = "manual_review"
            recommended_target = ""
            recommended_split_json = ""
            if ranked_with_share and top_share >= args.alias_threshold and (top_share - second_share) >= args.alias_gap:
                rec = "alias"
                recommended_target = top_target
            else:
                split_items = [(dsp, share) for _, dsp, share in ranked_with_share if share >= args.split_min_share]
                split_cover = sum(s for _, s in split_items)
                if len(split_items) >= 2 and split_cover >= args.split_coverage:
                    rec = "weighted_split"
                    norm_sum = sum(s for _, s in split_items)
                    obj = {name: round(s / norm_sum, 6) for name, s in split_items}
                    recommended_split_json = json.dumps(obj, ensure_ascii=True, separators=(",", ":"))

            row_out = {
                "source_key_norm": src_key,
                "source_key_raw": votes["raw_key"],
                "total_votes": str(votes["total_votes"]),
                "dem_votes": str(votes["dem_votes"]),
                "rep_votes": str(votes["rep_votes"]),
                "other_votes": str(votes["other_votes"]),
                "coverage_ratio": f"{covered:.4f}",
                "recommendation": rec,
                "recommended_target": recommended_target,
                "recommended_split_json": recommended_split_json,
            }
            for i in range(args.top_n):
                if i < len(ranked_with_share):
                    _, display, share = ranked_with_share[i]
                    row_out[f"candidate_{i+1}"] = display
                    row_out[f"share_{i+1}"] = f"{share:.4f}"
                else:
                    row_out[f"candidate_{i+1}"] = ""
                    row_out[f"share_{i+1}"] = ""
            rows_out.append(row_out)

    # Unmatched rows not present in source shapefile.
    missing_in_source = sorted(set(unmatched.keys()) - seen_source)
    for src_key in missing_in_source:
        votes = unmatched[src_key]
        row_out = {
            "source_key_norm": src_key,
            "source_key_raw": votes["raw_key"],
            "total_votes": str(votes["total_votes"]),
            "dem_votes": str(votes["dem_votes"]),
            "rep_votes": str(votes["rep_votes"]),
            "other_votes": str(votes["other_votes"]),
            "coverage_ratio": "0.0000",
            "recommendation": "not_in_source_shapefile",
            "recommended_target": "",
            "recommended_split_json": "",
        }
        for i in range(args.top_n):
            row_out[f"candidate_{i+1}"] = ""
            row_out[f"share_{i+1}"] = ""
        rows_out.append(row_out)

    rows_out.sort(key=lambda r: int(r.get("total_votes") or 0), reverse=True)
    fieldnames = [
        "source_key_norm",
        "source_key_raw",
        "total_votes",
        "dem_votes",
        "rep_votes",
        "other_votes",
        "coverage_ratio",
        "recommendation",
        "recommended_target",
        "recommended_split_json",
    ]
    for i in range(args.top_n):
        fieldnames.extend([f"candidate_{i+1}", f"share_{i+1}"])

    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_out)

    count_alias = sum(1 for r in rows_out if r.get("recommendation") == "alias")
    count_split = sum(1 for r in rows_out if r.get("recommendation") == "weighted_split")
    count_manual = sum(1 for r in rows_out if r.get("recommendation") not in {"alias", "weighted_split"})
    print(f"Wrote {out_path}")
    print(f"Unmatched rows analyzed: {len(rows_out)}")
    print(f"Recommendations: alias={count_alias}, weighted_split={count_split}, manual={count_manual}")


if __name__ == "__main__":
    main()
