#!/usr/bin/env python3
import argparse
import csv
import os
import re
from collections import defaultdict

import geopandas as gpd


def norm(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 .\-]", "", str(value or ""))
    return re.sub(r"\s+", " ", cleaned).strip().upper()


def _abs(base: str, path: str) -> str:
    return path if os.path.isabs(path) else os.path.abspath(os.path.join(base, path))


def _require_columns(gdf: gpd.GeoDataFrame, cols: list[str], label: str) -> None:
    missing = [c for c in cols if c not in gdf.columns]
    if missing:
        raise SystemExit(f"{label} is missing required columns: {', '.join(missing)}")


def _parse_counties(value: str) -> set[str]:
    if not value:
        return set()
    parts = [p.strip() for p in value.split(",")]
    return {norm(p) for p in parts if p}


def main() -> None:
    ap = argparse.ArgumentParser(description="Build VTD10 -> VTD20 overlap crosswalk CSV.")
    ap.add_argument("--base", default=os.path.join(os.path.dirname(__file__), ".."), help="Repo base path")
    ap.add_argument("--source", default=os.path.join("Data", "tl_2012_45_vtd10.zip"), help="Source VTD10 file")
    ap.add_argument("--target", default=os.path.join("Data", "Voting_Precincts.geojson"), help="Target precinct file")
    ap.add_argument("--counties", default="", help="Comma-separated county names filter (optional)")
    ap.add_argument("--top-n", type=int, default=5, help="Top overlap targets per source precinct")
    ap.add_argument("--out", required=True, help="Output CSV path")
    args = ap.parse_args()

    base = os.path.abspath(args.base)
    source_path = _abs(base, args.source)
    target_path = _abs(base, args.target)
    out_path = _abs(base, args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    if not os.path.exists(source_path):
        raise SystemExit(f"Missing source: {source_path}")
    if not os.path.exists(target_path):
        raise SystemExit(f"Missing target: {target_path}")

    source = gpd.read_file(source_path)
    target = gpd.read_file(target_path)

    _require_columns(source, ["COUNTYFP10", "NAME10", "geometry"], "Source")
    _require_columns(target, ["COUNTYFP20", "county_nam", "prec_id", "geometry"], "Target")

    target["county_fips"] = target["COUNTYFP20"].astype(str).str.zfill(3)
    target["county_name"] = target["county_nam"].astype(str).str.strip()
    target["precinct_name"] = target["prec_id"].astype(str).str.strip()
    target["county_norm"] = target["county_name"].map(norm)
    target["target_key_display"] = target["county_name"] + " - " + target["precinct_name"]
    target["target_key_norm"] = target["target_key_display"].map(norm)

    fips_to_name = (
        target[["county_fips", "county_name"]]
        .dropna()
        .drop_duplicates(subset=["county_fips"])
        .set_index("county_fips")["county_name"]
        .to_dict()
    )

    source["county_fips"] = source["COUNTYFP10"].astype(str).str.zfill(3)
    source["source_name"] = source["NAME10"].astype(str).str.strip()
    source["county_name"] = source["county_fips"].map(lambda f: fips_to_name.get(f, f))
    source["county_norm"] = source["county_name"].map(norm)
    source["source_key_display"] = source["county_name"] + " - " + source["source_name"]
    source["source_key_norm"] = source["source_key_display"].map(norm)

    county_filter = _parse_counties(args.counties)
    if county_filter:
        source = source[source["county_norm"].isin(county_filter)].copy()
        target = target[target["county_norm"].isin(county_filter)].copy()

    if source.empty:
        raise SystemExit("No source rows after county filter.")
    if target.empty:
        raise SystemExit("No target rows after county filter.")

    source = source[["source_key_norm", "source_key_display", "county_name", "county_fips", "source_name", "geometry"]]
    source = source.dissolve(by="source_key_norm", as_index=False, aggfunc="first")

    target = target[["target_key_norm", "target_key_display", "county_name", "county_fips", "precinct_name", "geometry"]]
    target = target.dissolve(by="target_key_norm", as_index=False, aggfunc="first")

    if source.crs is None and target.crs is None:
        source = source.set_crs("EPSG:4326")
        target = target.set_crs("EPSG:4326")
    elif source.crs is None:
        source = source.set_crs(target.crs)
    elif target.crs is None:
        target = target.set_crs(source.crs)
    elif source.crs != target.crs:
        target = target.to_crs(source.crs)

    source = source.to_crs("EPSG:3857")
    target = target.to_crs("EPSG:3857")

    source["source_area_m2"] = source.geometry.area
    source_area = dict(zip(source["source_key_norm"], source["source_area_m2"]))

    inter = gpd.overlay(
        source[["source_key_norm", "geometry"]],
        target[["target_key_norm", "geometry"]],
        how="intersection",
        keep_geom_type=False,
    )

    overlap_area: dict[tuple[str, str], float] = defaultdict(float)
    if not inter.empty:
        inter["overlap_area_m2"] = inter.geometry.area
        for _, row in inter.iterrows():
            src = str(row["source_key_norm"])
            dst = str(row["target_key_norm"])
            overlap_area[(src, dst)] += float(row["overlap_area_m2"])

    source_meta = {
        row["source_key_norm"]: {
            "source_county_name": row["county_name"],
            "source_county_fips": row["county_fips"],
            "source_name": row["source_name"],
            "source_key_display": row["source_key_display"],
        }
        for _, row in source.iterrows()
    }
    target_meta = {
        row["target_key_norm"]: {
            "target_county_name": row["county_name"],
            "target_county_fips": row["county_fips"],
            "target_precinct": row["precinct_name"],
            "target_key_display": row["target_key_display"],
        }
        for _, row in target.iterrows()
    }

    by_source: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for (src, dst), area in overlap_area.items():
        by_source[src].append((dst, area))

    rows_out: list[dict[str, str]] = []
    for src_key, meta in source_meta.items():
        src_area = float(source_area.get(src_key) or 0.0)
        ranked = sorted(by_source.get(src_key, []), key=lambda t: t[1], reverse=True)
        if not ranked:
            rows_out.append(
                {
                    "source_county_name": meta["source_county_name"],
                    "source_county_fips": meta["source_county_fips"],
                    "source_name": meta["source_name"],
                    "source_key_display": meta["source_key_display"],
                    "source_key_norm": src_key,
                    "source_area_m2": f"{src_area:.6f}",
                    "target_county_name": "",
                    "target_county_fips": "",
                    "target_precinct": "",
                    "target_key_display": "",
                    "target_key_norm": "",
                    "overlap_area_m2": "0.000000",
                    "share_of_source": "0.000000",
                    "share_rank": "1",
                }
            )
            continue

        for idx, (dst_key, area) in enumerate(ranked[: args.top_n], start=1):
            tmeta = target_meta.get(dst_key, {})
            share = (area / src_area) if src_area > 0 else 0.0
            rows_out.append(
                {
                    "source_county_name": meta["source_county_name"],
                    "source_county_fips": meta["source_county_fips"],
                    "source_name": meta["source_name"],
                    "source_key_display": meta["source_key_display"],
                    "source_key_norm": src_key,
                    "source_area_m2": f"{src_area:.6f}",
                    "target_county_name": tmeta.get("target_county_name", ""),
                    "target_county_fips": tmeta.get("target_county_fips", ""),
                    "target_precinct": tmeta.get("target_precinct", ""),
                    "target_key_display": tmeta.get("target_key_display", ""),
                    "target_key_norm": dst_key,
                    "overlap_area_m2": f"{area:.6f}",
                    "share_of_source": f"{share:.6f}",
                    "share_rank": str(idx),
                }
            )

    rows_out.sort(key=lambda r: (r["source_county_name"], r["source_name"], int(r["share_rank"])))

    fieldnames = [
        "source_county_name",
        "source_county_fips",
        "source_name",
        "source_key_display",
        "source_key_norm",
        "source_area_m2",
        "target_county_name",
        "target_county_fips",
        "target_precinct",
        "target_key_display",
        "target_key_norm",
        "overlap_area_m2",
        "share_of_source",
        "share_rank",
    ]

    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_out)

    print(f"Wrote {out_path}")
    print(f"Source precincts: {len(source_meta)}")
    print(f"Output rows: {len(rows_out)}")


if __name__ == "__main__":
    main()
