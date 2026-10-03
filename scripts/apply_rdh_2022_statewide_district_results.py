#!/usr/bin/env python3
"""Rebuild 2022 statewide contests for Congress and State Senate.

Uses the dated RDH 2022 statewide precinct polygons and results, 2020-block
CVAP, and the official congressional/Senate block-equivalency workbooks.
Statewide party totals already used by the project are preserved exactly.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

import shapefile
from pyproj import CRS, Transformer
from shapely import make_valid
from shapely.geometry import shape
from shapely.ops import transform
from shapely.strtree import STRtree


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.apply_rdh_block_equivalency_results import (  # noqa: E402
    PARTIES, allocate_integer, load_plan, make_result, read_json, ticket_lead, write_json,
)


RDH_ZIP = ROOT / "data/rdh_blockfiles/sc_2022_gen_prec.zip"
RDH_BASE = "sc_2022_gen_st_prec/sc_2022_gen_st_prec"
BLOCKS = ROOT / "work/crosswalk_inputs/tl_2020_45_tabblock20.zip"
CVAP_ZIP = ROOT / "data/sc_cvap_2024_2020_b_csv.zip"
CVAP_MEMBER = "sc_cvap_2024_2020_b.csv"
DISTRICT_DIR = ROOT / "data/district_contests"
AUDIT = DISTRICT_DIR / "rdh_2022_statewide_district_audit.json"

PLANS = {
    "congressional": (ROOT / "data/ConPassed BlockEQ.xlsx", 7, "congressional"),
    "state_senate": (ROOT / "data/H4493_Senate.xlsx", 46, "state_senate"),
}

CONTESTS = {
    "governor": {"dem": ("G22GOVDCUN",), "rep": ("G22GOVRMCM",), "other": ("G22GOVLREE", "G22GOVOWRI")},
    "attorney_general": {"dem": (), "rep": ("G22ATGRWIL",), "other": ("G22ATGOWRI",)},
    "secretary_of_state": {"dem": ("G22SOSDBUT",), "rep": ("G22SOSRHAM",), "other": ("G22SOSOWRI",)},
    "state_treasurer": {"dem": (), "rep": ("G22TRERLOF",), "other": ("G22TREAWOR", "G22TREOWRI")},
    "comptroller_general": {"dem": (), "rep": ("G22COMRECK",), "other": ("G22COMOWRI",)},
    "superintendent": {"dem": ("G22SUPDELL",), "rep": ("G22SUPRWEA",), "other": ("G22SUPAELL", "G22SUPGMIC", "G22SUPOWRI")},
    "commissioner_agriculture": {"dem": (), "rep": ("G22AGRRWEA",), "other": ("G22AGRCNEL", "G22AGRGEDM", "G22AGROWRI")},
    "us_senate": {"dem": ("G22USSDMAT",), "rep": ("G22USSRSCO",), "other": ("G22USSOWRI",)},
}


def load_cvap() -> dict[str, float]:
    with zipfile.ZipFile(CVAP_ZIP) as archive:
        reader = csv.DictReader(io.TextIOWrapper(archive.open(CVAP_MEMBER), encoding="utf-8-sig"))
        return {row["GEOID20"]: max(0.0, float(row["CVAP_TOT24"] or 0)) for row in reader}


def zip_reader(path: Path, base: str) -> tuple[shapefile.Reader, CRS]:
    with zipfile.ZipFile(path) as archive:
        reader = shapefile.Reader(
            shp=io.BytesIO(archive.read(base + ".shp")),
            shx=io.BytesIO(archive.read(base + ".shx")),
            dbf=io.BytesIO(archive.read(base + ".dbf")),
            encoding="utf-8",
        )
        crs = CRS.from_wkt(archive.read(base + ".prj").decode("utf-8"))
    return reader, crs


def number(value: object) -> float:
    return float(value or 0)


def build_precinct_weights(assignments: dict[str, dict[str, str]]) -> tuple[list[dict], dict[str, dict[str, float]], dict]:
    cvap = load_cvap()
    block_reader = shapefile.Reader(str(BLOCKS))
    block_crs = CRS.from_wkt(block_reader.shapeTypeName and zipfile.ZipFile(BLOCKS).read("tl_2020_45_tabblock20.prj").decode("utf-8"))
    project_blocks = Transformer.from_crs(block_crs, "EPSG:5070", always_xy=True)
    field_names = [field[0] for field in block_reader.fields[1:]]
    geoid_idx = field_names.index("GEOID20")
    block_geoms = []
    block_rows = []
    for item in block_reader.iterShapeRecords():
        geoid = str(item.record[geoid_idx])
        geom = transform(project_blocks.transform, make_valid(shape(item.shape.__geo_interface__)))
        if geom.is_empty or geom.area <= 0:
            continue
        block_geoms.append(geom)
        block_rows.append((geoid, geom.area, cvap.get(geoid, 0.0)))
    tree = STRtree(block_geoms)

    precinct_reader, precinct_crs = zip_reader(RDH_ZIP, RDH_BASE)
    project_precincts = Transformer.from_crs(precinct_crs, "EPSG:5070", always_xy=True)
    records = []
    weights = {plan: {} for plan in assignments}
    fallbacks = defaultdict(int)
    uncovered = []
    for item in precinct_reader.iterShapeRecords():
        record = item.record.as_dict()
        key = str(record["UNIQUE_ID"])
        precinct = transform(project_precincts.transform, make_valid(shape(item.shape.__geo_interface__)))
        by_plan_cvap = {plan: defaultdict(float) for plan in assignments}
        by_plan_area = {plan: defaultdict(float) for plan in assignments}
        for idx in tree.query(precinct):
            geoid, block_area, count = block_rows[int(idx)]
            block = block_geoms[int(idx)]
            intersection = precinct.intersection(block)
            if intersection.is_empty or intersection.area <= 0:
                continue
            area = intersection.area
            weighted = count * area / block_area
            for plan, lookup in assignments.items():
                district = lookup[geoid]
                by_plan_cvap[plan][district] += weighted
                by_plan_area[plan][district] += area
        for plan in assignments:
            values = by_plan_cvap[plan]
            total = sum(values.values())
            if total <= 0:
                values = by_plan_area[plan]
                total = sum(values.values())
                fallbacks[plan] += 1
            if total <= 0:
                uncovered.append({"precinct": key, "plan": plan})
                continue
            weights[plan][key] = {district: value / total for district, value in values.items() if value > 0}
        records.append(record)
    if uncovered:
        raise ValueError(f"Uncovered precinct/plan pairs: {uncovered[:10]}")
    qa = {"precincts": len(records), "area_fallback_precincts": dict(fallbacks), "uncovered": len(uncovered)}
    return records, weights, qa


def raw_district_totals(records: list[dict], weights: dict[str, dict[str, float]], contest: dict) -> dict[str, dict[str, float]]:
    out = defaultdict(lambda: {party: 0.0 for party in PARTIES})
    for record in records:
        shares = weights[str(record["UNIQUE_ID"])]
        values = {
            "dem_votes": sum(number(record[column]) for column in contest["dem"]),
            "rep_votes": sum(number(record[column]) for column in contest["rep"]),
            "other_votes": sum(number(record[column]) for column in contest["other"]),
        }
        for district, share in shares.items():
            for party in PARTIES:
                out[district][party] += values[party] * share
    return out


def apply_file(path: Path, raw: dict, plan: str, workbook: Path, write: bool) -> dict:
    payload = read_json(path)
    previous = payload["general"]["results"]
    if set(previous) != set(raw):
        raise ValueError(f"District mismatch in {path}: {len(previous)} vs {len(raw)}")
    canonical = {party: sum(int(row.get(party) or 0) for row in previous.values()) for party in PARTIES}
    allocated = {party: allocate_integer(canonical[party], {d: row[party] for d, row in raw.items()}) for party in PARTIES}
    first = next(iter(previous.values()))
    strip_ticket = "_president_" in path.name or "_governor_" in path.name
    dem_candidate = ticket_lead(first.get("dem_candidate")) if strip_ticket else first.get("dem_candidate", "")
    rep_candidate = ticket_lead(first.get("rep_candidate")) if strip_ticket else first.get("rep_candidate", "")
    current = {
        district: make_result({party: allocated[party][district] for party in PARTIES}, dem_candidate, rep_candidate)
        for district in sorted(raw, key=int)
    }
    changes = []
    for district, row in current.items():
        old = previous[district]
        if any(old.get(field) != row.get(field) for field in (*PARTIES, "margin_pct", "winner")):
            changes.append({"district": district, "old_margin_pct": old.get("margin_pct"), "new_margin_pct": row["margin_pct"],
                            "shift_pp": round(row["margin_pct"] - float(old.get("margin_pct") or 0), 4),
                            "old_winner": old.get("winner"), "new_winner": row["winner"]})
    totals = {party: sum(row[party] for row in current.values()) for party in PARTIES}
    if totals != canonical:
        raise ValueError(f"Conservation failure in {path}")
    if write:
        payload["general"]["results"] = current
        payload.setdefault("meta", {}).update({
            "assignment_method": "rdh_2022_dated_precinct_geometry_with_official_block_equivalency_cvap24",
            "block_equivalency_plan": plan,
            "block_equivalency_file": workbook.relative_to(ROOT).as_posix(),
            "precinct_election_file": RDH_ZIP.relative_to(ROOT).as_posix(),
            "precinct_geometry_member": RDH_BASE + ".shp",
            "block_weight_source": CVAP_ZIP.relative_to(ROOT).as_posix() + "#CVAP_TOT24",
            "canonical_statewide_totals": canonical,
            "district_statewide_totals": totals,
            "conservation_delta": {party: 0 for party in PARTIES},
        })
        write_json(path, payload)
    return {"file": path.relative_to(ROOT).as_posix(), "changed_districts": len(changes),
            "max_abs_margin_shift_pp": max((abs(row["shift_pp"]) for row in changes), default=0),
            "winner_flips": [row for row in changes if row["old_winner"] != row["new_winner"]],
            "canonical_statewide_totals": canonical}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    assignments = {plan: load_plan(workbook, count) for plan, (workbook, count, _) in PLANS.items()}
    records, weights, qa = build_precinct_weights(assignments)
    report = {"write": args.write, "method": "2022 dated statewide precinct geometry intersected with 2020 blocks, weighted by CVAP24, then joined to official district block equivalencies", "qa": qa, "files": []}
    for plan, (workbook, _, prefix) in PLANS.items():
        for contest_name, contest in CONTESTS.items():
            raw = raw_district_totals(records, weights[plan], contest)
            path = DISTRICT_DIR / f"{prefix}_{contest_name}_2022.json"
            report["files"].append(apply_file(path, raw, plan, workbook, args.write))
    report["summary"] = {"files": len(report["files"]),
                         "changed_district_records": sum(row["changed_districts"] for row in report["files"]),
                         "winner_flips": sum(len(row["winner_flips"]) for row in report["files"]),
                         "max_abs_margin_shift_pp": max(row["max_abs_margin_shift_pp"] for row in report["files"])}
    write_json(AUDIT, report)
    print(json.dumps(report["summary"], indent=2))
    print(f"wrote {AUDIT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
