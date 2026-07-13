#!/usr/bin/env python3
"""Build current Fiscal Affairs precinct-to-district overlap crosswalks."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from pyproj import Transformer
from shapely import make_valid
from shapely.geometry import shape
from shapely.ops import transform
from shapely.strtree import STRtree


REPO_ROOT = Path(__file__).resolve().parents[1]
SCOPES = {
    "congressional": ("data/tileset/sc_cd118_tileset.geojson", "CD118FP"),
    "state_house_2022": ("data/tileset/sc_state_house_2022_lines_tileset.geojson", "SLDLST"),
    "state_house_2024": ("data/tileset/sc_state_house_2024_lines_tileset.geojson", "SLDLST"),
    "state_senate_2022": ("data/tileset/sc_state_senate_2022_lines_tileset.geojson", "SLDUST"),
}


def district_number(value) -> str:
    text = str(value or "").strip()
    try:
        return str(int(text))
    except ValueError:
        return text


def projected_geometry(raw: dict, transformer: Transformer):
    geom = make_valid(shape(raw))
    return transform(transformer.transform, geom)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--precincts", default="data/Voting_Precincts.geojson")
    parser.add_argument("--out", default="data/crosswalk/current_precinct_to_district_weights.json")
    parser.add_argument("--csv-out", default="data/crosswalk/current_precinct_to_district_weights.csv")
    args = parser.parse_args()

    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    precinct_payload = json.loads((REPO_ROOT / args.precincts).read_text(encoding="utf-8"))
    precincts = []
    for feature in precinct_payload.get("features") or []:
        props = (feature or {}).get("properties") or {}
        key = str(props.get("precinct_norm") or "").strip().upper()
        raw_geom = (feature or {}).get("geometry")
        if key and raw_geom:
            precincts.append((key, projected_geometry(raw_geom, transformer)))

    output = {
        "meta": {
            "precinct_file": args.precincts.replace("\\", "/"),
            "precinct_source": "South Carolina Revenue and Fiscal Affairs Office",
            "precinct_count": len(precincts),
            "method": "equal-area polygon overlap normalized within each precinct",
        },
        "scopes": {},
    }
    csv_rows = []
    for scope, (district_rel, field) in SCOPES.items():
        district_payload = json.loads((REPO_ROOT / district_rel).read_text(encoding="utf-8"))
        district_ids = []
        district_geoms = []
        for feature in district_payload.get("features") or []:
            props = (feature or {}).get("properties") or {}
            dnum = district_number(props.get(field))
            raw_geom = (feature or {}).get("geometry")
            if dnum and raw_geom:
                district_ids.append(dnum)
                district_geoms.append(projected_geometry(raw_geom, transformer))
        tree = STRtree(district_geoms)
        weights = {}
        for precinct_key, precinct_geom in precincts:
            overlaps = []
            for idx in tree.query(precinct_geom):
                area = precinct_geom.intersection(district_geoms[int(idx)]).area
                if area > 0:
                    overlaps.append((district_ids[int(idx)], float(area)))
            total = sum(area for _, area in overlaps)
            if total <= 0:
                continue
            shares = {dnum: area / total for dnum, area in overlaps}
            weights[precinct_key] = shares
            for dnum, share in sorted(shares.items(), key=lambda item: int(item[0]) if item[0].isdigit() else item[0]):
                csv_rows.append({"scope": scope, "precinct_norm": precinct_key, "district": dnum, "weight": f"{share:.12f}"})
        output["scopes"][scope] = {
            "district_file": district_rel,
            "district_field": field,
            "district_count": len(set(district_ids)),
            "precincts_mapped": len(weights),
            "precincts_unmapped": len(precincts) - len(weights),
            "weights": weights,
        }
        print(f"{scope}: {len(weights)}/{len(precincts)} precincts mapped")

    out_path = REPO_ROOT / args.out
    out_path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    csv_path = REPO_ROOT / args.csv_out
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=("scope", "precinct_norm", "district", "weight"))
        writer.writeheader()
        writer.writerows(csv_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
