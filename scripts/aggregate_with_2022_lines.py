#!/usr/bin/env python3
"""
Use 2022 SC district line zips to build district GeoJSON + district contest aggregates.

Run from repo root:
    ..\\.venv\\Scripts\\python.exe scripts\\aggregate_with_2022_lines.py
"""

import os
import sys
import traceback
import json
import glob
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import build_data


def _pick_zip(*candidates: str) -> str:
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return candidates[0]


def _load_json(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _sum_precinct_rows(contest_payload: dict) -> dict:
    rows = contest_payload.get("rows") or []
    out = {"dem": 0.0, "rep": 0.0, "other": 0.0, "total": 0.0, "precinct_rows": 0}
    for r in rows:
        if not isinstance(r, dict):
            continue
        county = str(r.get("county") or "")
        if " - " not in county:
            continue
        dv = float(r.get("dem_votes") or 0)
        rv = float(r.get("rep_votes") or 0)
        ov = float(r.get("other_votes") or 0)
        out["dem"] += dv
        out["rep"] += rv
        out["other"] += ov
        out["total"] += (dv + rv + ov)
        out["precinct_rows"] += 1
    return out


def _sum_district_results(dist_payload: dict) -> dict:
    results = ((dist_payload.get("general") or {}).get("results") or {})
    out = {"dem": 0.0, "rep": 0.0, "other": 0.0, "total": 0.0, "districts": 0}
    for _, row in results.items():
        if not isinstance(row, dict):
            continue
        dv = float(row.get("dem_votes") or 0)
        rv = float(row.get("rep_votes") or 0)
        ov = float(row.get("other_votes") or 0)
        tv = float(row.get("total_votes") or (dv + rv + ov))
        out["dem"] += dv
        out["rep"] += rv
        out["other"] += ov
        out["total"] += tv
        out["districts"] += 1
    return out


def write_state_house_2022_lines_qa_report() -> int:
    contests_dir = os.path.join(REPO_ROOT, "data", "contests")
    dist_dir = os.path.join(REPO_ROOT, "data", "district_contests", "state_house_2022_lines")
    out_path = os.path.join(dist_dir, "qa_2022_lines.json")
    if not os.path.isdir(dist_dir):
        return 0

    records = []
    for path in sorted(glob.glob(os.path.join(dist_dir, "state_house_*_2022_lines.json"))):
        name = os.path.basename(path)
        m = re.match(r"^state_house_(.+)_(\d{4})_2022_lines\.json$", name)
        if not m:
            continue
        contest_type = m.group(1)
        year = int(m.group(2))
        contest_slice_path = os.path.join(contests_dir, f"{contest_type}_{year}.json")
        if not os.path.exists(contest_slice_path):
            continue

        dist_payload = _load_json(path) or {}
        contest_payload = _load_json(contest_slice_path) or {}

        src = _sum_precinct_rows(contest_payload)
        agg = _sum_district_results(dist_payload)
        meta = dist_payload.get("meta") or {}

        delta_total = agg["total"] - src["total"]
        delta_dem = agg["dem"] - src["dem"]
        delta_rep = agg["rep"] - src["rep"]
        delta_other = agg["other"] - src["other"]
        pct_err_total = (delta_total / src["total"] * 100.0) if src["total"] else 0.0

        matched = int(meta.get("precinct_rows_matched") or 0)
        weighted = int(meta.get("precinct_rows_block_weighted") or 0)
        fallback_centroid = max(0, matched - weighted)

        records.append(
            {
                "file": name,
                "contest_type": contest_type,
                "year": year,
                "district_count": int(agg["districts"]),
                "source_precinct_rows": int(src["precinct_rows"]),
                "matched_precinct_rows": matched,
                "block_weighted_rows": weighted,
                "centroid_fallback_rows": fallback_centroid,
                "match_coverage_pct": float(meta.get("match_coverage_pct") or 0.0),
                "source_totals": {
                    "dem_votes": round(src["dem"], 6),
                    "rep_votes": round(src["rep"], 6),
                    "other_votes": round(src["other"], 6),
                    "total_votes": round(src["total"], 6),
                },
                "aggregated_totals": {
                    "dem_votes": round(agg["dem"], 6),
                    "rep_votes": round(agg["rep"], 6),
                    "other_votes": round(agg["other"], 6),
                    "total_votes": round(agg["total"], 6),
                },
                "conservation_delta": {
                    "dem_votes": round(delta_dem, 6),
                    "rep_votes": round(delta_rep, 6),
                    "other_votes": round(delta_other, 6),
                    "total_votes": round(delta_total, 6),
                    "total_pct_error": round(pct_err_total, 8),
                },
            }
        )

    records.sort(key=lambda r: (-r["year"], r["contest_type"]))
    summary = {
        "files": len(records),
        "max_abs_total_vote_delta": round(max((abs(r["conservation_delta"]["total_votes"]) for r in records), default=0.0), 6),
        "max_abs_total_pct_error": round(max((abs(r["conservation_delta"]["total_pct_error"]) for r in records), default=0.0), 8),
        "min_match_coverage_pct": round(min((float(r["match_coverage_pct"]) for r in records), default=0.0), 6),
        "max_centroid_fallback_rows": int(max((int(r["centroid_fallback_rows"]) for r in records), default=0)),
    }
    payload = {"summary": summary, "records": records}
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    print(f"  wrote  {out_path}")
    return len(records)


def main() -> int:
    data_src = build_data.DATA_SRC
    data_out = build_data.DATA_OUT

    # Force 2022 lines where available; keep 2024 as a fallback for senate.
    build_data.DISTRICT_ZIPS = [
        (
            _pick_zip(
                os.path.join(data_src, "census", "tl_2022_45_cd118.zip"),
                os.path.join(data_out, "tl_2022_45_cd118.zip"),
            ),
            "tl_2022_45_cd118",
            "congressional",
            "CD118FP",
            "Congressional District",
            "sc_cd118_tileset.geojson",
        ),
        (
            _pick_zip(
                os.path.join(data_src, "census", "tl_2022_45_sldl.zip"),
                os.path.join(data_out, "tl_2022_45_sldl.zip"),
            ),
            "tl_2022_45_sldl",
            "state_house",
            "SLDLST",
            "State House District",
            "sc_state_house_2022_lines_tileset.geojson",
        ),
        (
            _pick_zip(
                os.path.join(data_src, "census", "tl_2022_45_sldu.zip"),
                os.path.join(data_out, "tl_2022_45_sldu.zip"),
                os.path.join(data_src, "census", "tl_2024_45_sldu.zip"),
            ),
            "tl_2022_45_sldu",
            "state_senate",
            "SLDUST",
            "State Senate District",
            "sc_state_senate_2022_lines_tileset.geojson",
        ),
    ]

    build_data.build_district_geojson()
    if any(os.path.exists(path) for path in build_data.ELECTION_FILES.values()):
        build_data.build_election_data()
        build_data.build_district_contests()
    written = build_data.build_statewide_contests_by_district_from_slices()
    if written:
        print(f"\n=== Statewide-by-District Slices ===\n  wrote  {written} file(s)")

    # Build state-house-per-contest 2022-lines files + QA report.
    try:
        from scripts.build_state_house_2022_lines_contest_files import main as build_state_house_2022_lines_files
        print("\n=== State House 2022-Lines Contest Files ===")
        build_state_house_2022_lines_files()
        print("\n=== State House 2022-Lines QA ===")
        write_state_house_2022_lines_qa_report()
    except Exception as exc:
        print(f"\nWARNING: 2022-lines state house post-process failed: {exc}")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"\nFailed: {exc}")
        traceback.print_exc()
        raise SystemExit(1)
