#!/usr/bin/env python3
"""Build 2020-block VAP shares for SC precincts split by current district lines."""

from __future__ import annotations

import argparse
import csv
import io
import json
import zipfile
from collections import defaultdict
from pathlib import Path

import shapefile
from pyproj import Transformer
from shapely import make_valid
from shapely.geometry import shape
from shapely.ops import transform
from shapely.strtree import STRtree

from aggregate_contests_to_vtd20_crosswalks import norm
from build_current_precinct_district_crosswalks import SCOPES


ROOT = Path(__file__).resolve().parents[1]
AREA_WEIGHTS = ROOT / "data/crosswalk/current_precinct_to_district_weights.json"
PRECINCTS = ROOT / "data/Voting_Precincts.geojson"
BLOCKS = ROOT / "work/crosswalk_inputs/tl_2020_45_tabblock20.zip"
BLOCK_VAP = ROOT / "data/dradata/Demographic_Data_Block_SC.v07.zip"
RDH_CVAP = ROOT / "data/sc_cvap_2024_2020_b_csv.zip"
OUTPUT = ROOT / "data/crosswalk/current_precinct_to_district_vap_weights.json"
MATERIAL_SPLIT = 0.001


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weight-source", choices=("rdh-cvap24", "census-vap20"), default="rdh-cvap24")
    parser.add_argument("--blocks", type=Path, default=BLOCKS)
    parser.add_argument("--rdh-cvap", type=Path, default=RDH_CVAP)
    parser.add_argument("--census-vap", type=Path, default=BLOCK_VAP)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    source = json.loads(AREA_WEIGHTS.read_text(encoding="utf-8"))
    area_weights = {scope: {norm(key): shares for key, shares in node["weights"].items()}
                    for scope, node in source["scopes"].items()}
    precinct_geojson = json.loads(PRECINCTS.read_text(encoding="utf-8"))
    project = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    split = defaultdict(dict)
    needed_by_county = defaultdict(dict)
    for feature in precinct_geojson.get("features", []):
        props = feature.get("properties") or {}
        key = norm(props.get("precinct_norm"))
        fips = str(props.get("source_fips") or props.get("COUNTYFP20") or "").zfill(3)
        for scope, weights in area_weights.items():
            shares = weights.get(key) or {}
            material = {district: float(share) for district, share in shares.items() if float(share) > MATERIAL_SPLIT}
            if len(material) > 1:
                split[scope][key] = material
                needed_by_county[fips][key] = feature

    source_path, csv_name, geoid_field, weight_field = (
        (args.rdh_cvap, "sc_cvap_2024_2020_b.csv", "GEOID20", "CVAP_TOT24")
        if args.weight_source == "rdh-cvap24" else
        (args.census_vap, "demographic_data_block_SC.v07.csv", "GEOID", "V_20_VAP_Total")
    )
    with zipfile.ZipFile(source_path) as archive:
        reader = csv.DictReader(io.TextIOWrapper(archive.open(csv_name), encoding="utf-8-sig"))
        vap = {row[geoid_field]: max(0.0, float(row[weight_field] or 0)) for row in reader}

    district_geometries = {}
    for scope, (relative, field) in SCOPES.items():
        payload = json.loads((ROOT / relative).read_text(encoding="utf-8"))
        district_geometries[scope] = {
            str(int(feature["properties"][field])): transform(project.transform, make_valid(shape(feature["geometry"])))
            for feature in payload.get("features", [])
            if feature.get("geometry") and feature.get("properties", {}).get(field)
        }

    # Only retain blocks with VAP in counties containing a material split.
    blocks_by_county = defaultdict(list)
    reader = shapefile.Reader(str(args.blocks))
    geoid_index = [field[0] for field in reader.fields[1:]].index("GEOID20")
    for record in reader.iterShapeRecords():
        geoid = str(record.record[geoid_index])
        count = vap.get(geoid, 0.0)
        county = geoid[2:5]
        if count <= 0 or county not in needed_by_county:
            continue
        geom = transform(project.transform, make_valid(shape(record.shape.__geo_interface__)))
        if not geom.is_empty and geom.area > 0:
            blocks_by_county[county].append((geom, count))

    output = {"meta": {"method": "2020-block population weighted precinct-to-district intersection",
                       "weight_source": args.weight_source,
                       "weight_file": display_path(source_path),
                       "weight_field": weight_field, "block_file": display_path(args.blocks),
                       "material_split_threshold": MATERIAL_SPLIT}, "scopes": {scope: {"weights": {}} for scope in SCOPES}}
    fallbacks = defaultdict(int)
    for county, features in sorted(needed_by_county.items()):
        blocks = blocks_by_county.get(county) or []
        tree = STRtree([geom for geom, _ in blocks]) if blocks else None
        for key, feature in features.items():
            precinct = transform(project.transform, make_valid(shape(feature["geometry"])))
            pieces = []
            if tree:
                for idx in tree.query(precinct):
                    block, count = blocks[int(idx)]
                    intersection = precinct.intersection(block)
                    if intersection.is_empty or intersection.area <= 0:
                        continue
                    pieces.append((intersection, count / block.area))
            for scope in SCOPES:
                candidates = split[scope].get(key)
                if not candidates:
                    continue
                estimates = {}
                for district in candidates:
                    geometry = district_geometries[scope][district]
                    estimates[district] = sum(intersection.intersection(geometry).area * density
                                              for intersection, density in pieces if intersection.intersects(geometry))
                total = sum(estimates.values())
                if total > 0:
                    output["scopes"][scope]["weights"][key] = {
                        district: value / total for district, value in estimates.items() if value > 0
                    }
                else:
                    fallbacks[scope] += 1
        print(f"{county}: {len(features)} split precincts, {len(blocks)} populated blocks")
    output["meta"]["area_fallback_precincts"] = dict(fallbacks)
    output["meta"]["vap_weighted_precincts"] = {
        scope: len(node["weights"]) for scope, node in output["scopes"].items()
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output["meta"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
