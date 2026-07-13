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
import argparse
import subprocess
from collections import defaultdict
import csv

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import build_data


def _norm(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 .\-]", "", str(value or ""))
    return re.sub(r"\s+", " ", cleaned).strip().upper()


def _pick_zip(*candidates: str) -> str:
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return candidates[0]


def _load_json(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def build_runtime_crosswalk_csv() -> str:
    """
    Build a merged crosswalk CSV from base precinct_crosswalk_2024.csv plus approved
    rows from scripts/out/precinct_crosswalk_patch_*.csv.
    """
    base_path = os.path.join(REPO_ROOT, "precinct_crosswalk_2024.csv")
    out_dir = os.path.join(REPO_ROOT, "scripts", "out")
    out_path = os.path.join(out_dir, "precinct_crosswalk_runtime_merged.csv")
    os.makedirs(out_dir, exist_ok=True)

    fieldnames = [
        "year",
        "contest_type",
        "county",
        "source_result_key",
        "target_polygon_key",
        "score",
        "status",
        "confidence",
        "notes",
    ]

    rows = []
    seen = set()

    def _add_row(row: dict):
        src = str(row.get("source_result_key") or "").strip()
        tgt = str(row.get("target_polygon_key") or "").strip()
        year = str(row.get("year") or "").strip()
        contest = str(row.get("contest_type") or "").strip().lower()
        status = str(row.get("status") or "").strip().lower()
        if not (src and tgt and year and contest):
            return
        if status and status != "approved":
            return
        key = (year, contest, src.upper(), tgt.upper())
        if key in seen:
            return
        seen.add(key)
        out = {k: str(row.get(k) or "").strip() for k in fieldnames}
        if not out.get("status"):
            out["status"] = "approved"
        rows.append(out)

    if os.path.exists(base_path):
        with open(base_path, encoding="utf-8-sig", newline="") as fh:
            rd = csv.DictReader(fh)
            for r in rd:
                _add_row(r)

    for p in sorted(glob.glob(os.path.join(REPO_ROOT, "scripts", "out", "precinct_crosswalk_patch_*.csv"))):
        try:
            with open(p, encoding="utf-8-sig", newline="") as fh:
                rd = csv.DictReader(fh)
                for r in rd:
                    _add_row(r)
        except OSError:
            continue

    def _dedupe_source_mappings() -> int:
        """
        Keep one alias target per contest/year/source key.

        Patch files can contain both a helpful historical alias and a generated
        "source maps to itself" row. The self row looks high-confidence, but it
        blocks the alias from being used and pushes records into county-share
        fallback. Prefer non-self, reviewed-looking mappings, then confidence.
        """

        def _score_float(row: dict) -> float:
            try:
                return float(row.get("score") or 0.0)
            except Exception:
                return 0.0

        def _priority(row: dict) -> tuple[int, int, int, float]:
            src_norm = _norm(row.get("source_result_key") or "")
            tgt_norm = _norm(row.get("target_polygon_key") or "")
            notes = str(row.get("notes") or "").lower()
            confidence = str(row.get("confidence") or "").strip().lower()
            confidence_rank = {"high": 3, "medium": 2, "low": 1}.get(confidence, 0)
            reviewed = any(token in notes for token in ("manual", "autofuzzy", "spartanburg-york", "prefilled"))
            return (
                0 if src_norm == tgt_norm else 1,
                1 if reviewed else 0,
                confidence_rank,
                _score_float(row),
            )

        best_by_source: dict[tuple[str, str, str], dict] = {}
        for row in rows:
            key = (
                str(row.get("year") or "").strip(),
                str(row.get("contest_type") or "").strip().lower(),
                _norm(row.get("source_result_key") or ""),
            )
            if key not in best_by_source or _priority(row) > _priority(best_by_source[key]):
                best_by_source[key] = row

        original_count = len(rows)
        rows[:] = list(best_by_source.values())
        seen.clear()
        for row in rows:
            seen.add(
                (
                    str(row.get("year") or "").strip(),
                    str(row.get("contest_type") or "").strip().lower(),
                    str(row.get("source_result_key") or "").strip().upper(),
                    str(row.get("target_polygon_key") or "").strip().upper(),
                )
            )
        return original_count - len(rows)

    pruned_crosswalk_conflicts = _dedupe_source_mappings()
    if pruned_crosswalk_conflicts:
        print(f"  crosswalk source conflicts pruned: {pruned_crosswalk_conflicts}")

    def _contest_year_pairs() -> list[tuple[int, str]]:
        manifest_path = os.path.join(REPO_ROOT, "data", "contests", "manifest.json")
        pairs: set[tuple[int, str]] = set()
        if os.path.exists(manifest_path):
            try:
                payload = _load_json(manifest_path) or {}
                for e in (payload.get("files") or []):
                    try:
                        y = int(e.get("year") or 0)
                    except Exception:
                        continue
                    ct = str(e.get("contest_type") or "").strip().lower()
                    if y and ct:
                        pairs.add((y, ct))
            except Exception:
                pass
        if pairs:
            return sorted(pairs)
        default_years = [2006, 2008, 2010, 2012, 2014, 2016, 2018, 2020, 2022, 2024]
        default_contests = [
            "president",
            "us_senate",
            "governor",
            "attorney_general",
            "secretary_of_state",
            "state_treasurer",
            "comptroller_general",
            "commissioner_agriculture",
        ]
        return [(y, c) for y in default_years for c in default_contests]

    def _add_overlap_rows(path: str, source_tag: str) -> int:
        if not os.path.exists(path):
            return 0
        added = 0
        pairs = _contest_year_pairs()
        # Only use overlap backfills for older cycles where naming drift is worst.
        pairs = [(y, c) for (y, c) in pairs if int(y) <= 2014]
        # Do not add overlap mappings for source keys already mapped by base/patch crosswalk.
        mapped_source_by_pair: dict[tuple[int, str], set[str]] = defaultdict(set)
        for r in rows:
            try:
                y = int(str(r.get("year") or "").strip())
            except Exception:
                continue
            c = str(r.get("contest_type") or "").strip().lower()
            s = _norm(str(r.get("source_result_key") or "").strip())
            if y and c and s:
                mapped_source_by_pair[(y, c)].add(s)
        try:
            with open(path, encoding="utf-8-sig", newline="") as fh:
                rd = csv.DictReader(fh)
                for r in rd:
                    try:
                        rank = int(float(r.get("share_rank") or 0))
                    except Exception:
                        rank = 0
                    try:
                        share = float(r.get("share_of_source") or 0.0)
                    except Exception:
                        share = 0.0
                    src = str(r.get("source_key_display") or "").strip()
                    tgt = str(r.get("target_key_display") or "").strip()
                    if not (src and tgt):
                        continue
                    if rank != 1:
                        continue
                    if share >= 0.985:
                        confidence = "high"
                    elif share >= 0.95:
                        confidence = "medium"
                    else:
                        continue
                    src_norm = _norm(src)
                    for year, contest in pairs:
                        if src_norm in mapped_source_by_pair.get((int(year), str(contest).lower()), set()):
                            continue
                        _add_row(
                            {
                                "year": str(year),
                                "contest_type": contest,
                                "county": "",
                                "source_result_key": src,
                                "target_polygon_key": tgt,
                                "score": f"{share:.6f}",
                                "status": "approved",
                                "confidence": confidence,
                                "notes": f"{source_tag}: top1 overlap share={share:.4f}",
                            }
                        )
                        added += 1
        except OSError:
            return 0
        return added

    added_vtd10 = _add_overlap_rows(
        os.path.join(REPO_ROOT, "scripts", "out", "vtd10_to_vtd20_overlap_top5.csv"),
        "vtd10_to_vtd20_overlap",
    )
    added_vtd00 = _add_overlap_rows(
        os.path.join(REPO_ROOT, "scripts", "out", "vtd00_to_vtd20_overlap_top5.csv"),
        "vtd00_to_vtd20_overlap",
    )
    if added_vtd10 or added_vtd00:
        print(f"  overlap imports: vtd10={added_vtd10} vtd00={added_vtd00}")

    rows.sort(key=lambda r: (r["year"], r["contest_type"], r["source_result_key"], r["target_polygon_key"]))
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=fieldnames)
        wr.writeheader()
        wr.writerows(rows)
    print(f"  wrote merged crosswalk: {out_path} ({len(rows)} rows)")
    return out_path


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
    out_path = os.path.join(dist_dir, "qa.json")
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
        county_share_fallback = int(meta.get("precinct_rows_county_share_fallback") or 0)
        county_share_fallback_votes = float(meta.get("precinct_votes_county_share_fallback") or 0.0)
        fallback_centroid = max(0, matched - weighted - county_share_fallback)
        fallback_row_share_pct = (county_share_fallback / src["precinct_rows"] * 100.0) if src["precinct_rows"] else 0.0
        fallback_vote_share_pct = (county_share_fallback_votes / src["total"] * 100.0) if src["total"] else 0.0

        records.append(
            {
                "file": name,
                "contest_type": contest_type,
                "year": year,
                "district_count": int(agg["districts"]),
                "source_precinct_rows": int(src["precinct_rows"]),
                "matched_precinct_rows": matched,
                "block_weighted_rows": weighted,
                "county_share_fallback_rows": county_share_fallback,
                "county_share_fallback_row_share_pct": round(fallback_row_share_pct, 6),
                "county_share_fallback_votes": round(county_share_fallback_votes, 6),
                "centroid_fallback_rows": fallback_centroid,
                "match_coverage_pct": float(meta.get("match_coverage_pct") or 0.0),
                "county_share_fallback_vote_share_pct": round(fallback_vote_share_pct, 8),
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
        "total_county_share_fallback_rows": int(sum(int(r.get("county_share_fallback_rows") or 0) for r in records)),
        "max_centroid_fallback_rows": int(max((int(r["centroid_fallback_rows"]) for r in records), default=0)),
    }
    payload = {"summary": summary, "records": records}
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    print(f"  wrote  {out_path}")
    return len(records)


def _evaluate_quality_gate(records: list, min_coverage_pct: float, max_total_pct_error: float) -> tuple[list, list]:
    kept = []
    pruned = []
    for r in records:
        cov = float(r.get("match_coverage_pct") or 0.0)
        err = abs(float(((r.get("conservation_delta") or {}).get("total_pct_error") or 0.0)))
        reasons = []
        if cov < float(min_coverage_pct):
            reasons.append(f"coverage<{float(min_coverage_pct):.2f}")
        if err > float(max_total_pct_error):
            reasons.append(f"abs_total_pct_error>{float(max_total_pct_error):.4f}")
        row = {
            "file": str(r.get("file") or ""),
            "contest_type": str(r.get("contest_type") or ""),
            "year": int(r.get("year") or 0),
            "match_coverage_pct": cov,
            "abs_total_pct_error": err,
            "reasons": reasons,
        }
        if reasons:
            pruned.append(row)
        else:
            kept.append(row)
    kept.sort(key=lambda x: (-x["year"], x["contest_type"]))
    pruned.sort(key=lambda x: (-x["year"], x["contest_type"]))
    return kept, pruned


def apply_state_house_2022_lines_quality_gate(
    min_coverage_pct: float = 95.0,
    max_total_pct_error: float = 1.0,
    flag_only: bool = False,
    delete_files: bool = True,
) -> int:
    dist_dir = os.path.join(REPO_ROOT, "data", "district_contests", "state_house_2022_lines")
    manifest_path = os.path.join(dist_dir, "manifest_2022_lines.json")
    qa_path = os.path.join(dist_dir, "qa.json")
    gate_report_path = os.path.join(dist_dir, "qa_gate_report.json")
    if not (os.path.exists(manifest_path) and os.path.exists(qa_path)):
        return 0

    manifest = _load_json(manifest_path) or {}
    qa = _load_json(qa_path) or {}
    entries = list(manifest.get("files") or [])
    records = list(qa.get("records") or [])
    if not entries or not records:
        return 0

    kept95, pruned95 = _evaluate_quality_gate(records, 95.0, max_total_pct_error)
    kept98, pruned98 = _evaluate_quality_gate(records, 98.0, max_total_pct_error)
    kept, pruned = _evaluate_quality_gate(records, min_coverage_pct, max_total_pct_error)

    pruned_files = {p["file"] for p in pruned if p.get("file")}

    gate_report = {
        "applied": {
            "min_coverage_pct": float(min_coverage_pct),
            "max_total_pct_error": float(max_total_pct_error),
            "flag_only": bool(flag_only),
        },
        "scenario_95": {"kept": len(kept95), "pruned": len(pruned95), "pruned_records": pruned95},
        "scenario_98": {"kept": len(kept98), "pruned": len(pruned98), "pruned_records": pruned98},
        "applied_result": {"kept": len(kept), "pruned": len(pruned), "pruned_records": pruned},
    }
    with open(gate_report_path, "w", encoding="utf-8") as fh:
        json.dump(gate_report, fh, indent=2)
        fh.write("\n")
    print(f"  wrote  {gate_report_path}")

    if not flag_only:
        kept_entries = [e for e in entries if str(e.get("file") or "") not in pruned_files]
        removed_entries = [e for e in entries if str(e.get("file") or "") in pruned_files]
        manifest["files"] = kept_entries
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
            fh.write("\n")

        if delete_files:
            for e in removed_entries:
                name = str(e.get("file") or "").strip()
                if not name:
                    continue
                p = os.path.join(dist_dir, name)
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass

    print(
        f"  quality-gate {'flagged' if flag_only else 'pruned'} {len(pruned)} file(s) "
        f"(coverage<{float(min_coverage_pct):.2f} or abs_total_pct_error>{float(max_total_pct_error):.4f})"
    )
    return len(pruned)


def _run_quality_gate_and_artifacts(args) -> int:
    from scripts.build_state_house_2022_lines_contest_files import main as build_state_house_2022_lines_files
    print("\n=== State House 2022-Lines Contest Files ===")
    build_state_house_2022_lines_files()
    print("\n=== State House 2022-Lines QA ===")
    write_state_house_2022_lines_qa_report()
    print("\n=== State House 2022-Lines Quality Gate ===")
    return apply_state_house_2022_lines_quality_gate(
        min_coverage_pct=float(args.coverage_threshold),
        max_total_pct_error=float(args.max_total_pct_error),
        flag_only=bool(args.flag_only),
        delete_files=(not bool(args.keep_pruned_files)),
    )


def _auto_backfill_flagged(args) -> int:
    dist_dir = os.path.join(REPO_ROOT, "data", "district_contests", "state_house_2022_lines")
    gate_report_path = os.path.join(dist_dir, "qa_gate_report.json")
    if not os.path.exists(gate_report_path):
        return 0
    gate = _load_json(gate_report_path) or {}
    flagged = list(((gate.get("applied_result") or {}).get("pruned_records") or []))
    if not flagged:
        return 0

    mismatch_prefix = "contest_mismatch_summary_2022_lines_backfill"
    mismatch_cmd = [
        sys.executable,
        os.path.join(REPO_ROOT, "scripts", "build_statewide_contest_mismatch_report.py"),
        "--base",
        REPO_ROOT,
        "--out-prefix",
        mismatch_prefix,
    ]
    subprocess.run(mismatch_cmd, check=True)
    mismatch_csv = os.path.join(REPO_ROOT, "scripts", "out", "contest_mismatch_missing_polygons_2022_lines_backfill.csv")

    by_year = defaultdict(set)
    for row in flagged:
        try:
            year = int(row.get("year") or 0)
        except Exception:
            continue
        contest = str(row.get("contest_type") or "").strip()
        if not year or not contest:
            continue
        by_year[year].add(contest)

    attempted = 0
    succeeded = 0
    for year, contests in sorted(by_year.items()):
        csv_override = str(build_data.ELECTION_FILES.get(int(year)) or "").strip()
        if not csv_override or not os.path.exists(csv_override):
            print(f"  backfill skipped year {year}: no configured CSV found in build_data.ELECTION_FILES")
            continue
        cmd = [
            sys.executable,
            os.path.join(REPO_ROOT, "scripts", "backfill_missing_contest_rows_from_oe_csv.py"),
            "--base",
            REPO_ROOT,
            "--year",
            str(year),
            "--csv",
            csv_override,
            "--mismatch-csv",
            mismatch_csv,
            "--crosswalk-min-confidence",
            str(args.crosswalk_min_confidence),
            "--crosswalk",
            str(getattr(args, "_runtime_crosswalk_rel", "precinct_crosswalk_2024.csv")),
        ]
        for c in sorted(contests):
            cmd.extend(["--contest", c])
        attempted += 1
        try:
            subprocess.run(cmd, check=True)
            succeeded += 1
        except subprocess.CalledProcessError as exc:
            print(f"  backfill skipped year {year}: {exc}")
            continue

    print(f"  auto-backfill batches attempted={attempted} succeeded={succeeded}")
    return succeeded


def _build_legacy_overlap_artifacts(args) -> None:
    out_dir = os.path.join(REPO_ROOT, "scripts", "out")
    os.makedirs(out_dir, exist_ok=True)

    vtd10_out = os.path.join(out_dir, "vtd10_to_vtd20_overlap_top5.csv")
    vtd00_out = os.path.join(out_dir, "vtd00_to_vtd20_overlap_top5.csv")

    print("\n=== Build Legacy Overlap Crosswalk Artifacts ===")
    cmds = [
        [
            sys.executable,
            os.path.join(REPO_ROOT, "scripts", "build_vtd10_to_vtd20_overlap_csv.py"),
            "--base",
            REPO_ROOT,
            "--source",
            os.path.join("Data", "tl_2012_45_vtd10.zip"),
            "--target",
            os.path.join("data", "Voting_Precincts.geojson"),
            "--top-n",
            "5",
            "--out",
            vtd10_out,
        ],
        [
            sys.executable,
            os.path.join(REPO_ROOT, "scripts", "build_vtd00_to_vtd20_overlap_csv.py"),
            "--base",
            REPO_ROOT,
            "--source",
            os.path.join("Data", "vtd00_counties"),
            "--target",
            os.path.join("data", "Voting_Precincts.geojson"),
            "--top-n",
            "5",
            "--out",
            vtd00_out,
        ],
    ]
    for cmd in cmds:
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as exc:
            print(f"  WARNING: overlap artifact step failed: {exc}")
            print("  hint: install geopandas in the active environment to enable overlap generation")
    if os.path.exists(vtd10_out):
        print(f"  wrote  {vtd10_out}")
    if os.path.exists(vtd00_out):
        print(f"  wrote  {vtd00_out}")

    if args.with_tabblocks:
        tabblock_hint = os.path.join(REPO_ROOT, "Data", "BlockAssign_ST45_SC_VTD.txt")
        if os.path.exists(tabblock_hint):
            print("  tabblock hint: found BlockAssign_ST45_SC_VTD.txt")
            print("  next: add block-based join stage (old block vintages -> vtd20) once legacy block files are available")
        else:
            print("  tabblock hint: BlockAssign_ST45_SC_VTD.txt not found; skipping tabblock prep")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build SC district outputs using 2022 lines with QA gates.")
    parser.add_argument("--coverage-threshold", type=float, default=95.0, help="Applied minimum match coverage percent.")
    parser.add_argument("--max-total-pct-error", type=float, default=1.0, help="Applied max abs total percent error.")
    parser.add_argument("--flag-only", action="store_true", help="Only flag low-quality files; do not prune manifest/files.")
    parser.add_argument("--keep-pruned-files", action="store_true", help="When pruning, keep files on disk and only filter manifest.")
    parser.add_argument("--apply-crosswalk", action="store_true", help="Apply precinct crosswalk remaps to all contest slices before district aggregation.")
    parser.add_argument("--crosswalk-min-confidence", default="medium", choices=["low", "medium", "high"], help="Min crosswalk confidence when applying remaps.")
    parser.add_argument("--auto-backfill-flagged", action="store_true", help="After initial QA/gate, backfill flagged contest-years and rerun aggregation + QA/gate.")
    parser.add_argument("--build-legacy-overlaps", action="store_true", help="Build vtd10->vtd20 and vtd00->vtd20 overlap CSV artifacts before aggregation.")
    parser.add_argument("--with-tabblocks", action="store_true", help="Include tabblock-prep checks alongside legacy overlap artifact build.")
    args = parser.parse_args(argv)
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
    # Legacy-years fallback: distribute unmatched precinct rows by county district
    # shares inferred from matched rows in each contest/year.
    build_data.ENABLE_UNMATCHED_COUNTY_SHARE_FALLBACK = True

    if args.build_legacy_overlaps:
        _build_legacy_overlap_artifacts(args)
    runtime_crosswalk = build_runtime_crosswalk_csv()
    args._runtime_crosswalk_rel = os.path.relpath(runtime_crosswalk, REPO_ROOT).replace("\\", "/")

    build_data.build_district_geojson()
    if any(os.path.exists(path) for path in build_data.ELECTION_FILES.values()):
        build_data.build_election_data()
        if args.apply_crosswalk:
            print("\n=== Crosswalk Remap Pass (All Contest Slices) ===")
            cmd = [
                sys.executable,
                os.path.join(REPO_ROOT, "scripts", "apply_precinct_aliases_to_slice.py"),
                "--base",
                REPO_ROOT,
                "--all",
                "--crosswalk",
                str(args._runtime_crosswalk_rel),
                "--crosswalk-min-confidence",
                str(args.crosswalk_min_confidence),
            ]
            subprocess.run(cmd, check=True)
        build_data.build_district_contests()
    written = build_data.build_statewide_contests_by_district_from_slices()
    if written:
        print(f"\n=== Statewide-by-District Slices ===\n  wrote  {written} file(s)")

    try:
        _run_quality_gate_and_artifacts(args)
        if args.auto_backfill_flagged:
            print("\n=== Auto Backfill For Flagged Contest-Years ===")
            batches = _auto_backfill_flagged(args)
            if batches > 0:
                print("\n=== Rebuild After Backfill ===")
                build_data.build_district_contests()
                n2 = build_data.build_statewide_contests_by_district_from_slices()
                if n2:
                    print(f"  wrote {n2} statewide-by-district file(s) after backfill")
                _run_quality_gate_and_artifacts(args)
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
