import argparse
import csv
import json
import os
import subprocess
import sys


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import build_data  # noqa: E402
import aggregate_with_2022_lines  # noqa: E402


CONTEST_TYPE = "superintendent"
OFFICE_NAMES = {
    "state superintendent of education",
    "superintendent of education",
    "state superintendent of public education",
    "superintendent of public education",
}


def resolve_source_path(year: int, csv_path: str) -> str:
    if os.path.exists(csv_path):
        return csv_path
    needle = f"{os.sep}openelections-data-sc{os.sep}"
    if needle in csv_path:
        fallback = csv_path.replace(needle, f"{os.sep}_tmpdata{os.sep}openelections-data-sc{os.sep}")
        if os.path.exists(fallback):
            return fallback
    year_str = str(year)
    filename = os.path.basename(csv_path)
    fallback = os.path.join(
        build_data.DATA_SRC,
        "_tmpdata",
        "openelections-data-sc",
        year_str,
        filename,
    )
    if os.path.exists(fallback):
        return fallback
    return csv_path


def rebuild_superintendent_contests() -> int:
    contests_dir = os.path.join(build_data.DATA_OUT, "contests")
    os.makedirs(contests_dir, exist_ok=True)

    precinct_norm_set, precinct_display_by_norm = build_data.load_precinct_polygon_index()
    precinct_aliases = build_data.load_precinct_aliases(precinct_display_by_norm)

    written = 0
    for year, csv_path in sorted(build_data.ELECTION_FILES.items()):
        csv_path = resolve_source_path(year, csv_path)
        if not os.path.exists(csv_path):
            continue

        with open(csv_path, newline="", encoding="utf-8-sig") as fh:
            raw_rows = list(csv.DictReader(fh))

        print(f"  source {year}: {os.path.relpath(csv_path, REPO_ROOT)}")

        contest_rows = []
        for row in raw_rows:
            office_raw = (row.get("office") or "").strip().lower()
            if office_raw in OFFICE_NAMES:
                patched = dict(row)
                cand_raw = str(patched.get("candidate") or "").strip().lower()
                party_raw = str(patched.get("party") or "").strip().upper()
                if cand_raw == "lisa ellis" and party_raw == "ALN":
                    patched["party"] = "DEM"
                contest_rows.append(patched)

        if not contest_rows:
            continue

        county_agg, precinct_agg = build_data.aggregate_all(
            contest_rows,
            precinct_norm_set,
            precinct_aliases,
        )
        if not county_agg:
            continue

        county_rows = [build_data.make_row(key, value, year) for key, value in county_agg.items()]
        precinct_rows = [build_data.make_row(key, value, year) for key, value in precinct_agg.items()]
        county_rows.sort(key=lambda r: str(r.get("county") or ""))
        precinct_rows.sort(key=lambda r: str(r.get("county") or ""))
        payload = {
            "year": year,
            "contest_type": CONTEST_TYPE,
            "rows": county_rows + precinct_rows,
        }
        out_name = f"{CONTEST_TYPE}_{year}.json"
        build_data.write_json(payload, os.path.join(contests_dir, out_name))
        print(
            f"    {CONTEST_TYPE} {year}: "
            f"{len(county_rows)} counties + {len(precinct_rows)} precincts"
        )
        written += 1

    manifest_entries = []
    for fn in os.listdir(contests_dir):
        if not fn.endswith(".json") or fn == "manifest.json" or fn.endswith("_from_7131_list.json"):
            continue
        path = os.path.join(contests_dir, fn)
        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
            year = int(payload.get("year") or 0)
            contest_type = str(payload.get("contest_type") or "").strip()
            rows = payload.get("rows") or []
        except Exception:
            continue
        if not year or not contest_type:
            continue
        manifest_entries.append(
            {
                "year": year,
                "contest_type": contest_type,
                "file": fn,
                "rows": len(rows),
            }
        )

    manifest_entries.sort(
        key=lambda e: (-e["year"], build_data._PRIORITY.get(e["contest_type"], 99))
    )
    build_data.write_json({"files": manifest_entries}, os.path.join(contests_dir, "manifest.json"))
    print(f"\n  manifest: {len(manifest_entries)} contest(s)")
    return written


def apply_superintendent_crosswalks(crosswalk_rel: str, crosswalk_min_confidence: str) -> int:
    contests_manifest_path = os.path.join(build_data.DATA_OUT, "contests", "manifest.json")
    if not os.path.exists(contests_manifest_path):
        return 0
    with open(contests_manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh) or {}
    entries = [
        e for e in (manifest.get("files") or [])
        if str(e.get("contest_type") or "").strip() == CONTEST_TYPE
    ]
    updated = 0
    for entry in sorted(entries, key=lambda e: int(e.get("year") or 0)):
        year = int(entry.get("year") or 0)
        if not year:
            continue
        cmd = [
            sys.executable,
            os.path.join(REPO_ROOT, "scripts", "apply_precinct_aliases_to_slice.py"),
            "--base",
            REPO_ROOT,
            "--contest",
            CONTEST_TYPE,
            "--year",
            str(year),
            "--crosswalk",
            crosswalk_rel,
            "--crosswalk-min-confidence",
            str(crosswalk_min_confidence),
        ]
        subprocess.run(cmd, check=True)
        updated += 1
    return updated


def rebuild_superintendent_district_slices() -> int:
    centroids_path = os.path.join(build_data.DATA_OUT, "precinct_centroids.geojson")
    if not os.path.exists(centroids_path):
        print("  skip superintendent district slices: precinct centroids not found")
        return 0

    district_sources = {
        "congressional": (
            os.path.join(build_data.DATA_OUT, "tileset", "sc_cd118_tileset.geojson"),
            "CD118FP",
        ),
        "state_house": (
            os.path.join(build_data.DATA_OUT, "tileset", "sc_state_house_2022_lines_tileset.geojson"),
            "SLDLST",
        ),
        "state_senate": (
            os.path.join(build_data.DATA_OUT, "tileset", "sc_state_senate_2022_lines_tileset.geojson"),
            "SLDUST",
        ),
    }
    for path, _ in district_sources.values():
        if not os.path.exists(path):
            print(f"  skip superintendent district slices: missing {os.path.relpath(path, REPO_ROOT)}")
            return 0

    precinct_to_district = {k: {} for k in district_sources.keys()}
    with open(centroids_path, encoding="utf-8") as fh:
        centroids = json.load(fh) or {}
    centroid_points = []
    for feature in centroids.get("features", []) or []:
        geom = (feature or {}).get("geometry") or {}
        if geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        props = (feature or {}).get("properties") or {}
        precinct_norm = (props.get("precinct_norm") or "").strip().upper()
        if not precinct_norm:
            continue
        centroid_points.append((precinct_norm, float(coords[0]), float(coords[1])))

    for scope, (path, num_field) in district_sources.items():
        with open(path, encoding="utf-8") as fh:
            gj = json.load(fh) or {}
        districts = []
        for feature in gj.get("features", []) or []:
            geom = (feature or {}).get("geometry") or {}
            props = (feature or {}).get("properties") or {}
            district_num = build_data._parse_district_num(props.get(num_field))
            if not district_num:
                continue
            bbox = build_data._geom_bbox(geom.get("coordinates"))
            districts.append((bbox, geom, district_num))
        for precinct_norm, x, y in centroid_points:
            chosen = ""
            for (minx, miny, maxx, maxy), geom, district_num in districts:
                if x < minx or x > maxx or y < miny or y > maxy:
                    continue
                if build_data._point_in_geometry(x, y, geom):
                    chosen = district_num
                    break
            if chosen:
                precinct_to_district[scope][precinct_norm] = chosen

    block_weight_scopes = {"congressional", "state_house", "state_senate"}
    block_weight_maps = build_data.load_block_assignment_precinct_weights()
    if block_weight_maps:
        for scope, mapping in block_weight_maps.items():
            if scope in block_weight_scopes and mapping:
                print(f"  block assignments ({scope}): {len(mapping)} precinct mappings")

    dist_dir = os.path.join(build_data.DATA_OUT, "district_contests")
    os.makedirs(dist_dir, exist_ok=True)
    written = 0

    contest_manifest_path = os.path.join(build_data.DATA_OUT, "contests", "manifest.json")
    with open(contest_manifest_path, encoding="utf-8") as fh:
        contest_manifest = json.load(fh) or {}
    contest_entries = [
        e for e in (contest_manifest.get("files") or [])
        if str(e.get("contest_type") or "").strip() == CONTEST_TYPE
    ]

    def county_from_precinct_key(key: str) -> str:
        if " - " not in key:
            return ""
        return key.split(" - ", 1)[0].strip().title()

    for entry in contest_entries:
        year = entry.get("year")
        fname = entry.get("file")
        if not (year and fname):
            continue
        contest_path = os.path.join(build_data.DATA_OUT, "contests", fname)
        if not os.path.exists(contest_path):
            continue
        with open(contest_path, encoding="utf-8") as fh:
            payload = json.load(fh) or {}
        rows = payload.get("rows") or []
        precinct_rows = [r for r in rows if isinstance(r, dict) and " - " in str(r.get("county") or "")]
        if not precinct_rows:
            continue

        for scope in district_sources.keys():
            scope_weight_map = (block_weight_maps.get(scope) or {}) if scope in block_weight_scopes else {}
            by_dist = {}
            matched = 0
            matched_weighted = 0
            dem_name = ""
            rep_name = ""
            for row in precinct_rows:
                key = (row.get("county") or "").strip()
                precinct_norm = build_data.normalize(key)
                row_dem = float(row.get("dem_votes") or 0)
                row_rep = float(row.get("rep_votes") or 0)
                row_other = float(row.get("other_votes") or 0)
                weights = scope_weight_map.get(precinct_norm) if scope_weight_map else None
                if weights:
                    matched += 1
                    matched_weighted += 1
                    for district_num, share in weights.items():
                        share = float(share or 0)
                        if share <= 0:
                            continue
                        node = by_dist.setdefault(
                            district_num,
                            {"dem": 0.0, "rep": 0.0, "other": 0.0, "dem_cand": "", "rep_cand": ""},
                        )
                        node["dem"] += row_dem * share
                        node["rep"] += row_rep * share
                        node["other"] += row_other * share
                else:
                    district_num = precinct_to_district[scope].get(precinct_norm)
                    if not district_num:
                        continue
                    matched += 1
                    node = by_dist.setdefault(
                        district_num,
                        {"dem": 0.0, "rep": 0.0, "other": 0.0, "dem_cand": "", "rep_cand": ""},
                    )
                    node["dem"] += row_dem
                    node["rep"] += row_rep
                    node["other"] += row_other

                if not dem_name:
                    dem_name = (row.get("dem_candidate") or "").strip()
                if not rep_name:
                    rep_name = (row.get("rep_candidate") or "").strip()

            if not by_dist:
                continue

            for node in by_dist.values():
                if dem_name and not node["dem_cand"]:
                    node["dem_cand"] = dem_name
                if rep_name and not node["rep_cand"]:
                    node["rep_cand"] = rep_name

            results = {}
            for district_num, values in by_dist.items():
                dem_votes = int(round(values["dem"]))
                rep_votes = int(round(values["rep"]))
                other_votes = int(round(values["other"]))
                total = dem_votes + rep_votes + other_votes
                margin = rep_votes - dem_votes
                margin_pct = round(margin / total * 100, 4) if total else 0
                winner = "R" if margin > 0 else ("D" if margin < 0 else "T")
                results[str(district_num)] = {
                    "dem_votes": dem_votes,
                    "rep_votes": rep_votes,
                    "other_votes": other_votes,
                    "total_votes": total,
                    "dem_candidate": values.get("dem_cand", ""),
                    "rep_candidate": values.get("rep_cand", ""),
                    "margin": margin,
                    "margin_pct": margin_pct,
                    "winner": winner,
                    "color": build_data.margin_color(margin_pct),
                }

            out_name = f"{scope}_{CONTEST_TYPE}_{year}.json"
            coverage = round(matched / len(precinct_rows) * 100, 4) if precinct_rows else 0
            payload = {
                "general": {"results": results},
                "meta": {
                    "match_coverage_pct": coverage,
                    "precinct_rows_total": len(precinct_rows),
                    "precinct_rows_matched": matched,
                    "precinct_rows_block_weighted": matched_weighted,
                    "precinct_rows_county_share_fallback": 0,
                    "precinct_votes_county_share_fallback": 0.0,
                },
            }
            build_data.write_json(payload, os.path.join(dist_dir, out_name))
            written += 1

    manifest_entries = []
    for fn in os.listdir(dist_dir):
        if not fn.endswith(".json") or fn == "manifest.json":
            continue
        base = fn[:-5]
        parts = base.split("_")
        if len(parts) < 3:
            continue
        if parts[0] == "state" and len(parts) >= 4:
            scope = "_".join(parts[0:2])
            contest_type = "_".join(parts[2:-1])
            year_part = parts[-1]
        else:
            scope = parts[0]
            contest_type = "_".join(parts[1:-1])
            year_part = parts[-1]
        try:
            year_num = int(year_part)
        except ValueError:
            continue
        rows_count = 0
        try:
            with open(os.path.join(dist_dir, fn), encoding="utf-8") as fh:
                node = json.load(fh) or {}
            rows_count = len(((node.get("general") or {}).get("results") or {}))
        except Exception:
            rows_count = 0
        manifest_entries.append(
            {
                "year": year_num,
                "contest_type": contest_type,
                "scope": scope,
                "file": fn,
                "rows": rows_count,
            }
        )
    manifest_entries.sort(
        key=lambda e: (-e["year"], build_data._PRIORITY.get(e["contest_type"], 99), e["scope"])
    )
    build_data.write_json({"files": manifest_entries}, os.path.join(dist_dir, "manifest.json"))
    return written


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild SC superintendent contest aggregation from OE CSVs."
    )
    parser.add_argument(
        "--with-districts",
        action="store_true",
        help="Also rebuild statewide-by-district slices after refreshing contest JSONs.",
    )
    parser.add_argument(
        "--apply-crosswalk",
        action="store_true",
        help="Apply precinct alias/crosswalk remaps to rebuilt superintendent slices before district refresh.",
    )
    parser.add_argument(
        "--crosswalk",
        default="precinct_crosswalk_2024.csv",
        help="Crosswalk CSV path relative to repo root. Ignored when --use-runtime-crosswalk is set.",
    )
    parser.add_argument(
        "--crosswalk-min-confidence",
        default="medium",
        choices=["low", "medium", "high"],
        help="Minimum confidence threshold for crosswalk-derived mappings.",
    )
    parser.add_argument(
        "--use-runtime-crosswalk",
        action="store_true",
        help="Use the merged runtime crosswalk built from the base crosswalk plus approved patch rows.",
    )
    args = parser.parse_args()

    print("\n=== Superintendent Contest Rebuild ===")
    written = rebuild_superintendent_contests()
    if written == 0:
        print("  no superintendent contest rows found in configured source CSVs")
        return 1

    if args.apply_crosswalk:
        print("\n=== Superintendent Crosswalk Remap Pass ===")
        crosswalk_rel = str(args.crosswalk).replace("\\", "/")
        if args.use_runtime_crosswalk:
            runtime_crosswalk = aggregate_with_2022_lines.build_runtime_crosswalk_csv()
            crosswalk_rel = os.path.relpath(runtime_crosswalk, REPO_ROOT).replace("\\", "/")
        remapped = apply_superintendent_crosswalks(crosswalk_rel, args.crosswalk_min_confidence)
        print(f"  superintendent slices remapped: {remapped}")

    if args.with_districts:
        print("\n=== Statewide-by-District Refresh ===")
        district_written = build_data.build_statewide_contests_by_district_from_slices()
        print(f"  district slices written: {district_written}")
        print("\n=== Superintendent District Refresh ===")
        superintendent_district_written = rebuild_superintendent_district_slices()
        print(f"  superintendent district slices written: {superintendent_district_written}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
