#!/usr/bin/env python3
import argparse
import csv
import glob
import json
import os
import re
import struct
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.ops import transform
from shapely.strtree import STRtree


def norm(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 .\-]", "", str(value or ""))
    return re.sub(r"\s+", " ", cleaned).strip().upper()


def _abs(base: str, path: str) -> str:
    return path if os.path.isabs(path) else os.path.abspath(os.path.join(base, path))


def read_dbf(path: Path) -> list[dict | None]:
    data = path.read_bytes()
    record_count = struct.unpack_from("<I", data, 4)[0]
    header_len = struct.unpack_from("<H", data, 8)[0]
    record_len = struct.unpack_from("<H", data, 10)[0]
    fields = []
    pos = 32
    while pos + 32 <= len(data) and data[pos] != 0x0D:
        name = data[pos : pos + 11].split(b"\x00", 1)[0].decode("ascii", errors="ignore").strip()
        ftype = chr(data[pos + 11])
        flen = data[pos + 16]
        fields.append((name, ftype, flen))
        pos += 32
    rows = []
    for i in range(record_count):
        start = header_len + i * record_len
        rec = data[start : start + record_len]
        if not rec or rec[:1] == b"*":
            rows.append(None)
            continue
        off = 1
        props = {}
        for name, ftype, flen in fields:
            raw = rec[off : off + flen]
            off += flen
            text = raw.decode("cp1252", errors="replace").strip()
            if ftype in ("N", "F") and text:
                try:
                    val = int(text) if "." not in text else float(text)
                except ValueError:
                    val = text
            else:
                val = text if text else None
            props[name] = val
        rows.append(props)
    return rows


def ring_area(ring):
    area = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        area += x1 * y2 - x2 * y1
    return area / 2.0


def close_ring(ring):
    return ring if not ring or ring[0] == ring[-1] else ring + [ring[0]]


def point_in_ring(point, ring):
    x, y = point
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-30) + xi):
            inside = not inside
        j = i
    return inside


def shapefile_parts_to_geom(parts):
    rings = [close_ring(p) for p in parts if len(p) >= 3]
    outers, holes = [], []
    for ring in rings:
        area = ring_area(ring)
        if area < 0:
            outers.append({"ring": ring, "holes": [], "area": abs(area)})
        else:
            holes.append(ring)
    if not outers:
        outers = [{"ring": ring, "holes": [], "area": abs(ring_area(ring))} for ring in rings]
        holes = []
    outers.sort(key=lambda x: x["area"], reverse=True)
    for hole in holes:
        pt = hole[0]
        for outer in outers:
            if point_in_ring(pt, outer["ring"]):
                outer["holes"].append(hole)
                break
    polys = []
    for outer in outers:
        try:
            poly = Polygon(outer["ring"], outer["holes"])
            if not poly.is_empty:
                if not poly.is_valid:
                    poly = poly.buffer(0)
                if not poly.is_empty:
                    polys.append(poly)
        except Exception:
            continue
    if not polys:
        return None
    return polys[0] if len(polys) == 1 else MultiPolygon(polys)


def read_shp_geoms(path: Path):
    data = path.read_bytes()
    pos = 100
    geoms = []
    while pos + 8 <= len(data):
        content_len = struct.unpack_from(">i", data, pos + 4)[0] * 2
        pos += 8
        end = pos + content_len
        if end > len(data):
            break
        shape_type = struct.unpack_from("<i", data, pos)[0]
        if shape_type == 0:
            geoms.append(None)
        elif shape_type in (5, 15, 25, 31):
            num_parts = struct.unpack_from("<i", data, pos + 36)[0]
            num_points = struct.unpack_from("<i", data, pos + 40)[0]
            parts_off = pos + 44
            points_off = parts_off + num_parts * 4
            starts = list(struct.unpack_from(f"<{num_parts}i", data, parts_off)) + [num_points]
            points = [struct.unpack_from("<2d", data, points_off + i * 16) for i in range(num_points)]
            geoms.append(shapefile_parts_to_geom([points[starts[i] : starts[i + 1]] for i in range(num_parts)]))
        else:
            geoms.append(None)
        pos = end
    return geoms


def iter_zip_shapefile(zip_path: str):
    with tempfile.TemporaryDirectory() as td:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            shp = next(n for n in names if n.lower().endswith(".shp"))
            dbf = next(n for n in names if n.lower().endswith(".dbf"))
            zf.extract(shp, td)
            zf.extract(dbf, td)
        geoms = read_shp_geoms(Path(td) / shp)
        rows = read_dbf(Path(td) / dbf)
        for props, geom in zip(rows, geoms):
            if props and geom is not None and not geom.is_empty:
                yield props, geom


def iter_geojson(path: str):
    payload = json.load(open(path, encoding="utf-8"))
    for feat in payload.get("features", []):
        geom = shape(feat.get("geometry"))
        if not geom.is_empty:
            yield feat.get("properties") or {}, geom


def source_paths(path_or_dir: str) -> list[str]:
    if os.path.isdir(path_or_dir):
        return sorted(glob.glob(os.path.join(path_or_dir, "*.zip")))
    return [path_or_dir]


def _first_present(props, names):
    for name in names:
        value = props.get(name)
        if value not in (None, ""):
            return value
    return ""


SC_FIPS_TO_COUNTY = {
    "001": "Abbeville",
    "003": "Aiken",
    "005": "Allendale",
    "007": "Anderson",
    "009": "Bamberg",
    "011": "Barnwell",
    "013": "Beaufort",
    "015": "Berkeley",
    "017": "Calhoun",
    "019": "Charleston",
    "021": "Cherokee",
    "023": "Chester",
    "025": "Chesterfield",
    "027": "Clarendon",
    "029": "Colleton",
    "031": "Darlington",
    "033": "Dillon",
    "035": "Dorchester",
    "037": "Edgefield",
    "039": "Fairfield",
    "041": "Florence",
    "043": "Georgetown",
    "045": "Greenville",
    "047": "Greenwood",
    "049": "Hampton",
    "051": "Horry",
    "053": "Jasper",
    "055": "Kershaw",
    "057": "Lancaster",
    "059": "Laurens",
    "061": "Lee",
    "063": "Lexington",
    "065": "McCormick",
    "067": "Marion",
    "069": "Marlboro",
    "071": "Newberry",
    "073": "Oconee",
    "075": "Orangeburg",
    "077": "Pickens",
    "079": "Richland",
    "081": "Saluda",
    "083": "Spartanburg",
    "085": "Sumter",
    "087": "Union",
    "089": "Williamsburg",
    "091": "York",
}


def source_columns(kind: str, vintage: str):
    if kind == "vtd":
        return {
            "county": [f"COUNTYFP{vintage}", "COUNTYFP"],
            "name": [f"NAME{vintage}", "NAME"],
            "id": [f"GEOID{vintage}", f"VTDIDFP{vintage}", f"VTDST{vintage}"],
        }
    if vintage == "00":
        return {"county": ["COUNTYFP00", "COUNTYFP"], "name": ["BLKIDFP"], "id": ["BLKIDFP"]}
    if vintage == "10":
        return {"county": ["COUNTYFP10", "COUNTYFP"], "name": ["GEOID"], "id": ["GEOID"]}
    if vintage == "20":
        return {"county": ["COUNTYFP20", "COUNTYFP"], "name": ["GEOID20"], "id": ["GEOID20"]}
    raise ValueError(f"Unsupported source kind/vintage: {kind}/{vintage}")


def load_source_features(path_or_dir: str, kind: str, vintage: str, projector, fips_to_county):
    feats = []
    cols = source_columns(kind, vintage)
    for path in source_paths(path_or_dir):
        for props, geom in iter_zip_shapefile(path):
            county_fips = str(_first_present(props, cols["county"]) or "").zfill(3)
            name = str(_first_present(props, cols["name"]) or "").strip()
            if not county_fips or not name:
                continue
            county_name = fips_to_county.get(county_fips, county_fips)
            display_name = name if kind == "vtd" else f"Block {name}"
            geom_p = transform(projector, geom)
            if not geom_p.is_valid:
                geom_p = geom_p.buffer(0)
            feats.append(
                {
                    "county_fips": county_fips,
                    "county_name": county_name,
                    "source_name": display_name,
                    "source_id": str(_first_present(props, cols["id"]) or name).strip(),
                    "source_key_display": f"{county_name} - {display_name}",
                    "source_key_norm": norm(f"{county_name} - {display_name}"),
                    "geometry": geom_p,
                }
            )
    return feats


def load_target_features(path: str, projector):
    iterator = iter_geojson(path) if path.lower().endswith((".json", ".geojson")) else iter_zip_shapefile(path)
    feats = []
    for props, geom in iterator:
        county_fips = str(_first_present(props, ["COUNTYFP20", "COUNTYFP10", "COUNTYFP00", "COUNTYFP"]) or "").zfill(3)
        county_name = str(
            _first_present(props, ["county_nam", "COUNTY_NAM", "COUNTYNAME", "County"])
            or SC_FIPS_TO_COUNTY.get(county_fips)
            or county_fips
        ).strip()
        precinct = str(
            _first_present(props, ["prec_id", "NAME20", "NAME10", "NAME00", "NAMELSAD20", "NAMELSAD10", "NAMELSAD00", "VTDST20", "VTDST10", "VTDST00"])
            or ""
        ).strip()
        target_display = str(props.get("precinct_norm") or "").strip()
        if not target_display:
            target_display = f"{county_name} - {precinct}"
        geom_p = transform(projector, geom)
        if not geom_p.is_valid:
            geom_p = geom_p.buffer(0)
        feats.append(
            {
                "county_fips": county_fips,
                "county_name": county_name,
                "precinct_name": precinct,
                "target_key_display": target_display,
                "target_key_norm": norm(target_display),
                "geometry": geom_p,
            }
        )
    return feats


def fips_to_county_from_targets(targets):
    out = {}
    for t in targets:
        if t.get("county_fips") and t.get("county_name"):
            out.setdefault(t["county_fips"], t["county_name"])
    return out


def dissolve_features(features, key_name, meta_keys):
    grouped = {}
    for feat in features:
        key = feat[key_name]
        if key not in grouped:
            grouped[key] = {k: feat[k] for k in meta_keys}
            grouped[key]["geometry"] = feat["geometry"]
        else:
            grouped[key]["geometry"] = grouped[key]["geometry"].union(feat["geometry"])
    return list(grouped.values())


def main():
    ap = argparse.ArgumentParser(description="Build professional projected-area legacy geometry -> 2020 precinct overlap crosswalk.")
    ap.add_argument("--source", required=True, help="Source VTD zip or directory of county VTD zips")
    ap.add_argument("--source-kind", default="vtd", choices=["vtd", "tabblock"], help="Source geometry type")
    ap.add_argument("--source-vintage", required=True, choices=["00", "10", "20"], help="Source vintage suffix")
    ap.add_argument("--target", default="data/Voting_Precincts.geojson", help="Target 2020 precinct GeoJSON or VTD20 zip")
    ap.add_argument("--out", required=True, help="Output CSV")
    ap.add_argument("--top-n", type=int, default=5)
    ap.add_argument("--min-share", type=float, default=0.0005)
    args = ap.parse_args()

    project = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True).transform
    targets = dissolve_features(
        load_target_features(args.target, project),
        "target_key_norm",
        ["county_fips", "county_name", "precinct_name", "target_key_display", "target_key_norm"],
    )
    sources = dissolve_features(
        load_source_features(args.source, args.source_kind, args.source_vintage, project, fips_to_county_from_targets(targets)),
        "source_key_norm",
        ["county_fips", "county_name", "source_name", "source_id", "source_key_display", "source_key_norm"],
    )

    target_geoms = [t["geometry"] for t in targets]
    target_by_id = {id(t["geometry"]): t for t in targets}
    index = STRtree(target_geoms)

    rows = []
    for i, src in enumerate(sources, 1):
        src_geom = src["geometry"]
        src_area = float(src_geom.area or 0.0)
        ranked = []
        for cand_ix in index.query(src_geom):
            tgt = targets[int(cand_ix)] if not hasattr(cand_ix, "geom_type") else target_by_id[id(cand_ix)]
            if src["county_fips"] != tgt["county_fips"]:
                continue
            inter = src_geom.intersection(tgt["geometry"])
            if inter.is_empty:
                continue
            area = float(inter.area or 0.0)
            share = area / src_area if src_area > 0 else 0.0
            if share >= args.min_share:
                ranked.append((share, area, tgt))
        ranked.sort(key=lambda x: x[1], reverse=True)
        if not ranked:
            rows.append({
                "source_county_fips": src["county_fips"],
                "source_county_name": src["county_name"],
                "source_name": src["source_name"],
                "source_id": src["source_id"],
                "source_key_display": src["source_key_display"],
                "source_key_norm": src["source_key_norm"],
                "source_area_m2": f"{src_area:.6f}",
                "target_county_name": "",
                "target_county_fips": "",
                "target_precinct": "",
                "target_key_display": "",
                "target_key_norm": "",
                "overlap_area_m2": "0.000000",
                "share_of_source": "0.000000",
                "share_rank": "1",
            })
        else:
            for rank, (share, area, tgt) in enumerate(ranked[: args.top_n], 1):
                rows.append({
                    "source_county_fips": src["county_fips"],
                    "source_county_name": src["county_name"],
                    "source_name": src["source_name"],
                    "source_id": src["source_id"],
                    "source_key_display": src["source_key_display"],
                    "source_key_norm": src["source_key_norm"],
                    "source_area_m2": f"{src_area:.6f}",
                    "target_county_name": tgt["county_name"],
                    "target_county_fips": tgt["county_fips"],
                    "target_precinct": tgt["precinct_name"],
                    "target_key_display": tgt["target_key_display"],
                    "target_key_norm": tgt["target_key_norm"],
                    "overlap_area_m2": f"{area:.6f}",
                    "share_of_source": f"{share:.6f}",
                    "share_rank": str(rank),
                })
        if i % 250 == 0:
            print(f"processed {i}/{len(sources)} sources")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "source_county_fips",
        "source_county_name",
        "source_name",
        "source_id",
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
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=fieldnames)
        wr.writeheader()
        wr.writerows(rows)

    print(f"Wrote {out_path}")
    print(f"Source precincts: {len(sources)}")
    print(f"Target precincts: {len(targets)}")
    print(f"Output rows: {len(rows)}")


if __name__ == "__main__":
    main()
