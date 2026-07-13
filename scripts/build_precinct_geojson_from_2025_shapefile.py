#!/usr/bin/env python3
import argparse
import json
import math
import os
import re
import struct
from pathlib import Path


STATEFP = "45"
NONRESIDENT_PRECINCT_NORMS = {"AIKEN - SRS", "BARNWELL - SRS"}
STATE_PLANE_FOOT_TO_METER = 0.3048
FALSE_EASTING_FT = 2000000.0
FALSE_NORTHING_FT = 0.0
CENTRAL_MERIDIAN = math.radians(-81.0)
STANDARD_PARALLEL_1 = math.radians(32.5)
STANDARD_PARALLEL_2 = math.radians(34.83333333333334)
LATITUDE_OF_ORIGIN = math.radians(31.83333333333333)
EARTH_RADIUS_M = 6378137.0
FLATTENING = 1 / 298.257222101
ECCENTRICITY = math.sqrt(2 * FLATTENING - FLATTENING * FLATTENING)


def norm(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 .\-]", "", str(value or ""))
    return re.sub(r"\s+", " ", cleaned).strip().upper()


def title_name(value: str) -> str:
    s = re.sub(r"\s+", " ", str(value or "").replace("_", " ")).strip()
    if not s:
        return ""
    words = []
    for word in s.lower().split(" "):
        if not word:
            continue
        if word in {"and"}:
            words.append(word)
        elif re.fullmatch(r"[ivx]+", word):
            words.append(word.upper())
        else:
            words.append(word[:1].upper() + word[1:])
    out = " ".join(words)
    out = re.sub(r"\bNo\.\s*(\d+)", r"No. \1", out, flags=re.I)
    return out


def t_func(phi: float) -> float:
    esin = ECCENTRICITY * math.sin(phi)
    return math.tan(math.pi / 4 - phi / 2) / ((1 - esin) / (1 + esin)) ** (ECCENTRICITY / 2)


def m_func(phi: float) -> float:
    return math.cos(phi) / math.sqrt(1 - (ECCENTRICITY * math.sin(phi)) ** 2)


M1 = m_func(STANDARD_PARALLEL_1)
M2 = m_func(STANDARD_PARALLEL_2)
T1 = t_func(STANDARD_PARALLEL_1)
T2 = t_func(STANDARD_PARALLEL_2)
T0 = t_func(LATITUDE_OF_ORIGIN)
N = math.log(M1 / M2) / math.log(T1 / T2)
F = M1 / (N * (T1 ** N))
RHO0 = EARTH_RADIUS_M * F * (T0 ** N)


def stateplane_sc_to_lonlat(x_ft: float, y_ft: float) -> list[float]:
    x_m = (x_ft - FALSE_EASTING_FT) * STATE_PLANE_FOOT_TO_METER
    y_m = (y_ft - FALSE_NORTHING_FT) * STATE_PLANE_FOOT_TO_METER
    rho = math.copysign(math.hypot(x_m, RHO0 - y_m), N)
    theta = math.atan2(x_m, RHO0 - y_m)
    t = (rho / (EARTH_RADIUS_M * F)) ** (1 / N)
    phi = math.pi / 2 - 2 * math.atan(t)
    for _ in range(8):
        esin = ECCENTRICITY * math.sin(phi)
        phi = math.pi / 2 - 2 * math.atan(t * ((1 - esin) / (1 + esin)) ** (ECCENTRICITY / 2))
    lon = CENTRAL_MERIDIAN + theta / N
    return [round(math.degrees(lon), 7), round(math.degrees(phi), 7)]


def read_dbf(path: Path) -> tuple[list[dict], list[str]]:
    data = path.read_bytes()
    count = struct.unpack("<I", data[4:8])[0]
    header_len = struct.unpack("<H", data[8:10])[0]
    record_len = struct.unpack("<H", data[10:12])[0]
    fields = []
    offset = 1
    i = 32
    while i < header_len and data[i] != 0x0D:
        raw = data[i : i + 32]
        name = raw[:11].split(b"\x00", 1)[0].decode("ascii", errors="ignore")
        length = raw[16]
        fields.append((name, offset, length))
        offset += length
        i += 32
    rows = []
    for rec_idx in range(count):
        start = header_len + rec_idx * record_len
        rec = data[start : start + record_len]
        if not rec or rec[:1] == b"*":
            rows.append({})
            continue
        row = {}
        for name, off, length in fields:
            row[name] = rec[off : off + length].decode("utf-8", errors="ignore").strip()
        rows.append(row)
    return rows, [name for name, _, _ in fields]


def read_polygon_records(path: Path) -> list[list[list[list[float]]]]:
    data = path.read_bytes()
    pos = 100
    records = []
    while pos < len(data):
        if pos + 8 > len(data):
            break
        content_words = struct.unpack(">i", data[pos + 4 : pos + 8])[0]
        content_start = pos + 8
        content_end = content_start + content_words * 2
        shape_type = struct.unpack("<i", data[content_start : content_start + 4])[0]
        if shape_type == 0:
            records.append([])
            pos = content_end
            continue
        if shape_type != 5:
            raise ValueError(f"Unsupported shape type {shape_type}; expected Polygon")
        num_parts = struct.unpack("<i", data[content_start + 36 : content_start + 40])[0]
        num_points = struct.unpack("<i", data[content_start + 40 : content_start + 44])[0]
        parts_start = content_start + 44
        parts = list(struct.unpack(f"<{num_parts}i", data[parts_start : parts_start + 4 * num_parts]))
        points_start = parts_start + 4 * num_parts
        points = [
            struct.unpack("<2d", data[points_start + i * 16 : points_start + (i + 1) * 16])
            for i in range(num_points)
        ]
        rings = []
        for idx, part_start in enumerate(parts):
            part_end = parts[idx + 1] if idx + 1 < len(parts) else num_points
            ring = [stateplane_sc_to_lonlat(x, y) for x, y in points[part_start:part_end]]
            if ring and ring[0] != ring[-1]:
                ring.append(ring[0])
            rings.append(ring)
        records.append(rings)
        pos = content_end
    return records


def centroid_from_rings(rings: list[list[list[float]]]) -> list[float]:
    best = max((ring for ring in rings if len(ring) >= 4), key=len, default=[])
    if not best:
        return [0, 0]
    area2 = 0.0
    cx = 0.0
    cy = 0.0
    for i in range(len(best) - 1):
        x1, y1 = best[i]
        x2, y2 = best[i + 1]
        cross = x1 * y2 - x2 * y1
        area2 += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(area2) < 1e-12:
        return [round(sum(p[0] for p in best) / len(best), 7), round(sum(p[1] for p in best) / len(best), 7)]
    return [round(cx / (3 * area2), 7), round(cy / (3 * area2), 7)]


def build_properties(row: dict) -> dict:
    county = title_name(row.get("County") or "")
    pname = title_name(row.get("PName") or row.get("Name") or "")
    pcode = str(row.get("PCode") or "").strip()
    fips = str(row.get("FIPS") or "").strip().zfill(3)
    geoid = f"{STATEFP}{fips}{pcode.zfill(6)}"
    precinct_norm = norm(f"{county} - {pname}")
    return {
        "STATEFP20": STATEFP,
        "COUNTYFP20": fips,
        "VTDST20": pcode.zfill(6),
        "GEOID20": geoid,
        "NAME20": pname,
        "NAMELSAD20": pname,
        "county_nam": county,
        "prec_id": pname,
        "precinct_norm": precinct_norm,
        "county_norm": norm(county),
        "precinct_code": pcode,
        "precinct_full_name": pname,
        "precinct_display_name": f"{county} - {pname}" if county and pname else pname,
        "source_precinct_name": row.get("PName") or "",
        "source_precinct_code": pcode,
        "source_county": row.get("County") or "",
        "source_fips": fips,
        "source_codemap": row.get("CodeMapNum") or "",
        "source_act": row.get("ActNumber") or "",
        "source_effective_date": row.get("EffectiveD") or "",
        "source_bill": row.get("Bill") or "",
        "source_rfa_map": row.get("RFAMapURL") or "",
        "source_layer": "2025Precincts",
        "source_agency": "South Carolina Revenue and Fiscal Affairs Office",
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Convert the 2025 Fiscal Affairs precinct shapefile to app-ready GeoJSON.")
    ap.add_argument("--shp", default="work/precincts_2025/2025Precincts.shp")
    ap.add_argument("--dbf", default="work/precincts_2025/2025Precincts.dbf")
    ap.add_argument("--out", default="data/Voting_Precincts.geojson")
    ap.add_argument("--centroids-out", default="data/precinct_centroids.geojson")
    args = ap.parse_args()

    rows, _fields = read_dbf(Path(args.dbf))
    shapes = read_polygon_records(Path(args.shp))
    if len(rows) != len(shapes):
        raise SystemExit(f"DBF/SHP record count mismatch: {len(rows)} vs {len(shapes)}")

    features = []
    centroids = []
    for row, rings in zip(rows, shapes):
        if not row or not rings:
            continue
        props = build_properties(row)
        if props["precinct_norm"] in NONRESIDENT_PRECINCT_NORMS:
            continue
        geometry = {"type": "Polygon", "coordinates": rings}
        features.append({"type": "Feature", "properties": props, "geometry": geometry})
        centroids.append({
            "type": "Feature",
            "properties": props.copy(),
            "geometry": {"type": "Point", "coordinates": centroid_from_rings(rings)},
        })

    out = {"type": "FeatureCollection", "features": features}
    centroid_out = {"type": "FeatureCollection", "features": centroids}
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        json.dump(out, fh, separators=(",", ":"))
    with open(args.centroids_out, "w", encoding="utf-8", newline="") as fh:
        json.dump(centroid_out, fh, separators=(",", ":"))
    print(f"wrote {args.out} ({len(features)} features)")
    print(f"wrote {args.centroids_out} ({len(centroids)} features)")


if __name__ == "__main__":
    main()
