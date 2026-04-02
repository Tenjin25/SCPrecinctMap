#!/usr/bin/env python3
"""
Calibrate 2024 Presidential results for SC State House districts.

Recomputes `data/district_contests/state_house_president_2024.json` by assigning
each precinct row in `data/contests/president_2024.json` to a State House district
via the precinct centroid point-in-polygon mapping (same approach as build_data.py).

Also syncs the Dem/Rep/Oth share columns in:
  - data/district-statistics 2024 pres state house.csv

Run from repo root:
  .\\.venv\\Scripts\\python.exe scripts\\calibrate_state_house_president_2024.py
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
DATA_SRC_DIR = REPO_ROOT / "Data"


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=False)
        fh.write("\n")
    tmp.replace(path)


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    tmp.replace(path)


@dataclass(frozen=True)
class DistrictTotals:
    dem: int
    rep: int
    other: int

    @property
    def total(self) -> int:
        return self.dem + self.rep + self.other


def _format_share(x: float) -> str:
    # Match existing file style: 4 decimals.
    return f"{x:.4f}"


def _compute_state_house_president_2024(
    *,
    contest_path: Path,
    centroids_path: Path,
    district_polys_path: Path,
) -> tuple[dict[str, DistrictTotals], dict[str, Any]]:
    # Import build_data.py for shared normalization + point-in-geometry helpers.
    sys.path.insert(0, str(REPO_ROOT))
    import build_data  # type: ignore

    contest = _load_json(contest_path) or {}
    rows = contest.get("rows") or []
    precinct_rows = [
        r
        for r in rows
        if isinstance(r, dict) and " - " in str(r.get("county") or "")
    ]
    if not precinct_rows:
        raise RuntimeError(f"No precinct rows found in {contest_path}")

    centroids = _load_json(centroids_path) or {}
    districts = _load_json(district_polys_path) or {}

    # Build district polygon list: (bbox, geom, dnum)
    polys: list[tuple[tuple[float, float, float, float], dict[str, Any], str]] = []
    for feat in districts.get("features", []) or []:
        geom = (feat or {}).get("geometry") or {}
        props = (feat or {}).get("properties") or {}
        dnum = build_data._parse_district_num(props.get("SLDLST"))
        if not dnum:
            continue
        bbox = build_data._geom_bbox(geom.get("coordinates"))
        polys.append((bbox, geom, dnum))
    if not polys:
        raise RuntimeError(f"No district polygons found in {district_polys_path}")

    # precinct_norm -> district
    prec_to_dist: dict[str, str] = {}
    for f in centroids.get("features", []) or []:
        geom = (f or {}).get("geometry") or {}
        if geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        x, y = float(coords[0]), float(coords[1])
        props = (f or {}).get("properties") or {}
        pn = (props.get("precinct_norm") or "").strip().upper()
        if not pn:
            continue

        chosen = ""
        for (minx, miny, maxx, maxy), g, dnum in polys:
            if x < minx or x > maxx or y < miny or y > maxy:
                continue
            if build_data._point_in_geometry(x, y, g):
                chosen = dnum
                break
        if chosen:
            prec_to_dist[pn] = chosen

    # Prefer block-derived fractional allocation weights (Census block assignment),
    # falling back to centroid point-in-polygon where weights are unavailable.
    block_weights_all = build_data.load_block_assignment_precinct_weights() or {}
    block_weights = block_weights_all.get("state_house") or {}

    def _alloc_int(votes: int, shares: list[tuple[str, float]]) -> dict[str, int]:
        """
        Largest remainder method: allocate integer `votes` across districts by `share`,
        ensuring allocations sum exactly to `votes`.
        """
        if votes <= 0 or not shares:
            return {}
        floors: dict[str, int] = {}
        fracs: list[tuple[float, str]] = []
        used = 0
        for dnum, share in shares:
            s = float(share or 0)
            if s <= 0:
                continue
            exact = votes * s
            base = int(exact // 1)
            floors[dnum] = floors.get(dnum, 0) + base
            used += base
            fracs.append((exact - base, dnum))
        remain = votes - used
        if remain > 0 and fracs:
            fracs.sort(reverse=True, key=lambda x: x[0])
            for _, dnum in fracs[:remain]:
                floors[dnum] = floors.get(dnum, 0) + 1
        return floors

    # Aggregate precinct rows into districts.
    dem_name = ""
    rep_name = ""
    by_dist = defaultdict(lambda: [0, 0, 0])  # dem, rep, other (ints)
    matched = 0
    matched_weighted = 0
    for r in precinct_rows:
        key = (r.get("county") or "").strip()
        pn = build_data.normalize(key)
        weights = block_weights.get(pn)

        if weights:
            matched += 1
            matched_weighted += 1
            shares = [(str(d), float(s or 0)) for d, s in weights.items() if float(s or 0) > 0]
            dem_votes = int(r.get("dem_votes") or 0)
            rep_votes = int(r.get("rep_votes") or 0)
            other_votes = int(r.get("other_votes") or 0)
            dem_alloc = _alloc_int(dem_votes, shares)
            rep_alloc = _alloc_int(rep_votes, shares)
            oth_alloc = _alloc_int(other_votes, shares)
            all_dnums = set(dem_alloc) | set(rep_alloc) | set(oth_alloc)
            for dnum in all_dnums:
                by_dist[dnum][0] += dem_alloc.get(dnum, 0)
                by_dist[dnum][1] += rep_alloc.get(dnum, 0)
                by_dist[dnum][2] += oth_alloc.get(dnum, 0)
        else:
            dnum = prec_to_dist.get(pn)
            if not dnum:
                continue
            matched += 1
            by_dist[dnum][0] += int(r.get("dem_votes") or 0)
            by_dist[dnum][1] += int(r.get("rep_votes") or 0)
            by_dist[dnum][2] += int(r.get("other_votes") or 0)

        if not dem_name:
            dem_name = (r.get("dem_candidate") or "").strip()
        if not rep_name:
            rep_name = (r.get("rep_candidate") or "").strip()

    totals: dict[str, DistrictTotals] = {
        str(dnum): DistrictTotals(dem=v[0], rep=v[1], other=v[2])
        for dnum, v in by_dist.items()
    }

    meta = {
        "match_coverage_pct": round(matched / len(precinct_rows) * 100, 4)
        if precinct_rows
        else 0,
        "precinct_rows_total": len(precinct_rows),
        "precinct_rows_matched": matched,
        "precinct_rows_block_weighted": matched_weighted,
        "dem_candidate": dem_name,
        "rep_candidate": rep_name,
    }
    return totals, meta


def _write_district_contest_json(
    *,
    out_path: Path,
    totals: dict[str, DistrictTotals],
    meta: dict[str, Any],
) -> None:
    # Import helper for margin color consistency.
    sys.path.insert(0, str(REPO_ROOT))
    import build_data  # type: ignore

    results: dict[str, Any] = {}
    for dnum, t in sorted(totals.items(), key=lambda kv: build_data._district_sort_key(kv[0])):
        total = t.total
        margin = t.rep - t.dem
        mpct = round(margin / total * 100, 4) if total else 0
        winner = "R" if margin > 0 else ("D" if margin < 0 else "T")
        results[str(dnum)] = {
            "dem_votes": t.dem,
            "rep_votes": t.rep,
            "other_votes": t.other,
            "total_votes": total,
            "dem_candidate": meta.get("dem_candidate", ""),
            "rep_candidate": meta.get("rep_candidate", ""),
            "margin": margin,
            "margin_pct": mpct,
            "winner": winner,
            "color": build_data.margin_color(mpct),
        }

    payload = {
        "general": {"results": results},
        "meta": {
            "match_coverage_pct": meta.get("match_coverage_pct", 0),
            "precinct_rows_total": meta.get("precinct_rows_total", 0),
            "precinct_rows_matched": meta.get("precinct_rows_matched", 0),
            "precinct_rows_block_weighted": meta.get("precinct_rows_block_weighted", 0),
        },
    }
    _write_json(out_path, payload)


def _sync_csv_shares(
    *,
    csv_path: Path,
    totals: dict[str, DistrictTotals],
) -> tuple[int, int]:
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        rows: list[dict[str, str]] = list(reader)

    if not fieldnames or "ID" not in fieldnames:
        raise RuntimeError(f"Unexpected CSV format: {csv_path}")

    updated = 0
    missing = 0
    for r in rows:
        # Some source CSVs have a trailing comma on each row; DictReader stores
        # those extra cells under the special None key.
        if None in r:
            r.pop(None, None)
        did = (r.get("ID") or "").strip().strip('"')
        if not did or did.lower() == "un":
            continue
        t = totals.get(did)
        if not t or t.total <= 0:
            missing += 1
            continue
        dem = round(t.dem / t.total, 4)
        rep = round(t.rep / t.total, 4)
        oth = round(1.0 - dem - rep, 4)
        r["Dem"] = _format_share(dem)
        r["Rep"] = _format_share(rep)
        r["Oth"] = _format_share(oth)
        updated += 1

    _write_csv(csv_path, fieldnames, rows)
    return updated, missing


def _apply_target_shares_from_csv(
    *,
    targets_csv_path: Path,
    totals: dict[str, DistrictTotals],
) -> None:
    """
    Apply target Dem/Rep/Oth *shares* (e.g. from DRA) to the computed district totals,
    preserving each district's total_votes.
    """
    with targets_csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if None in row:
                row.pop(None, None)
            did = (row.get("ID") or "").strip().strip('"').lstrip("0")
            if not did or did.lower() == "un":
                continue
            if did not in totals:
                continue
            try:
                dem_s = float(row.get("Dem") or 0)
                rep_s = float(row.get("Rep") or 0)
                oth_s = float(row.get("Oth") or 0)
            except ValueError:
                continue
            s = dem_s + rep_s + oth_s
            if s <= 0:
                continue
            # If the shares don't sum to 1.0 due to rounding/export quirks, renormalize.
            dem_s, rep_s, oth_s = dem_s / s, rep_s / s, oth_s / s

            total = totals[did].total
            dem_votes = int(round(total * dem_s))
            rep_votes = int(round(total * rep_s))
            other_votes = total - dem_votes - rep_votes
            if other_votes < 0:
                # Fallback: clamp other to 0, preserve total.
                other_votes = 0
                rep_votes = total - dem_votes
                if rep_votes < 0:
                    dem_votes = 0
                    rep_votes = total
            totals[did] = DistrictTotals(dem=dem_votes, rep=rep_votes, other=other_votes)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--write",
        action="store_true",
        help="Write updated district JSON (default: dry run summary only).",
    )
    ap.add_argument(
        "--sync-csv",
        action="store_true",
        help="Also update the Dem/Rep/Oth share columns in the output CSV.",
    )
    ap.add_argument(
        "--sync-data-src",
        action="store_true",
        help="Also update the CSV under ./Data (if you keep a source copy there).",
    )
    ap.add_argument(
        "--override-margin",
        action="append",
        default=[],
        metavar="DISTRICT=MARGIN_PCT",
        help="Override a district's margin_pct (Rep-Dem as percent of total votes). Example: 57=8 for R+8.",
    )
    ap.add_argument(
        "--targets-csv",
        default="",
        metavar="PATH",
        help="Optional CSV (e.g. DRA export) with ID,Dem,Rep,Oth shares to calibrate district partisanship.",
    )
    args = ap.parse_args()

    contest_path = DATA_DIR / "contests" / "president_2024.json"
    centroids_path = DATA_DIR / "precinct_centroids.geojson"
    district_polys_path = DATA_DIR / "tileset" / "sc_state_house_2022_lines_tileset.geojson"
    out_json_path = DATA_DIR / "district_contests" / "state_house_president_2024.json"
    csv_paths = [
        DATA_DIR / "district-statistics 2024 pres state house.csv",
    ]
    if args.sync_data_src:
        csv_paths.append(DATA_SRC_DIR / "district-statistics 2024 pres state house.csv")

    totals, meta = _compute_state_house_president_2024(
        contest_path=contest_path,
        centroids_path=centroids_path,
        district_polys_path=district_polys_path,
    )

    if args.targets_csv:
        _apply_target_shares_from_csv(
            targets_csv_path=(Path(args.targets_csv).expanduser().resolve()),
            totals=totals,
        )

    # Apply manual margin overrides (useful when calibrating to external tools like DRA).
    for raw in args.override_margin or []:
        if "=" not in raw:
            raise SystemExit(f"Invalid --override-margin value (expected DISTRICT=MARGIN): {raw!r}")
        did, mpct_s = raw.split("=", 1)
        did = did.strip().lstrip("0")
        if not did:
            raise SystemExit(f"Invalid district id in override: {raw!r}")
        try:
            target_mpct = float(mpct_s.strip())
        except ValueError:
            raise SystemExit(f"Invalid margin_pct in override: {raw!r}")
        t = totals.get(did)
        if not t:
            raise SystemExit(f"District {did} not found in computed totals; cannot override.")
        total = t.total
        other_votes = t.other
        two_party_total = total - other_votes
        target_margin_votes = int(round(target_mpct / 100.0 * total))
        rep_votes = int(round((two_party_total + target_margin_votes) / 2.0))
        dem_votes = two_party_total - rep_votes
        if dem_votes < 0 or rep_votes < 0:
            raise SystemExit(f"Override {raw!r} impossible with total={total} other={other_votes}.")
        totals[did] = DistrictTotals(dem=dem_votes, rep=rep_votes, other=other_votes)

    sum_total = sum(t.total for t in totals.values())
    print(f"districts={len(totals)} total_votes={sum_total} coverage={meta.get('match_coverage_pct')}%")

    if not args.write:
        print("dry run (use --write to update files)")
        return 0

    _write_district_contest_json(out_path=out_json_path, totals=totals, meta=meta)
    print(f"wrote {out_json_path.relative_to(REPO_ROOT)}")

    if args.sync_csv:
        for p in csv_paths:
            if not p.exists():
                continue
            updated, missing = _sync_csv_shares(csv_path=p, totals=totals)
            print(f"updated {updated} row(s) in {p.relative_to(REPO_ROOT)} (missing={missing})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
