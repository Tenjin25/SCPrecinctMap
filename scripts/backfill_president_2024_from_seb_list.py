#!/usr/bin/env python3
"""
Targeted backfill for 2024 President precinct rows from SC SEB "list" export.

Why: The SEB list export (e.g. `data/7131_list.csv`) can contain split precincts
like Charleston "Deer Park 1A/1B/2A/2B/2C" and "Lincolnville" that may be
aggregated away in other county-level exports. However, using the SEB list as
the *primary* 2024 input can destabilize statewide matching.

This script patches ONLY selected Charleston precinct keys in:
  - data/contests/president_2024.json

Then you should rebuild district slices:
  python build_data.py
or re-run just the statewide-by-district step if you prefer.

Defaults:
  - Reads SEB list from data/7131_list.csv (President 2024)
  - Writes updated JSON in-place
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from typing import Dict, Tuple


def normalize_party(party: str) -> str:
    p = (party or "").strip().upper()
    if not p:
        return ""
    if p in {"DEM", "DEMOCRAT", "DEMOCRATIC", "D"}:
        return "DEM"
    if p in {"REP", "REPUBLICAN", "R", "GOP"}:
        return "REP"
    if p in {"LIB", "LIBERTARIAN"}:
        return "LIB"
    if p in {"GRN", "GREEN"}:
        return "GRN"
    if p in {"CON", "CONSTITUTION"}:
        return "CON"
    if p in {"WFP", "WORKING FAMILIES", "WORKINGFAMILIES"}:
        return "WFP"
    return p


def load_precinct_display_map(polygons_path: str) -> Dict[str, str]:
    """
    Map normalized precinct_norm -> display key ("County - Precinct").
    """
    with open(polygons_path, encoding="utf-8") as fh:
        gj = json.load(fh) or {}
    out: Dict[str, str] = {}
    for feat in gj.get("features", []) or []:
        props = (feat or {}).get("properties") or {}
        pn = (props.get("precinct_norm") or "").strip().upper()
        county = (props.get("county_nam") or "").strip()
        prec = (props.get("prec_id") or "").strip()
        if pn and county and prec:
            out[pn] = f"{county} - {prec}"
    return out


_DP_RE = re.compile(r"^DEER\s+PARK\s+(0?(\d+))([A-Z])?$", re.IGNORECASE)


def _canon_seb_precinct_label(raw: str) -> str:
    """
    SEB list division_name examples:
      - "Deer Park 01a"  -> "Deer Park 1A"
      - "Deer Park 2c"   -> "Deer Park 2C"
      - "Lincolnville"   -> "Lincolnville"
    """
    s = (raw or "").strip()
    if not s:
        return ""
    m = _DP_RE.match(s)
    if m:
        num = int(m.group(2))
        suf = (m.group(3) or "").upper()
        return f"Deer Park {num}{suf}"
    return s.title()


def load_target_rows_from_seb_list(
    seb_list_csv: str,
    targets: set[str],
) -> Dict[str, Dict]:
    """
    Returns map of display key -> aggregated row node:
      { "Charleston - Deer Park 1A": {dem,rep,other, dem_cand, rep_cand}, ... }
    """
    agg: Dict[str, Dict] = {}
    with open(seb_list_csv, newline="", encoding="utf-8") as fh:
        r = csv.DictReader(fh)
        for row in r:
            if (row.get("office_name") or "").strip() != "President of the United States":
                continue
            if (row.get("division_type") or "").strip().lower() != "precinct":
                continue
            precinct = _canon_seb_precinct_label(row.get("division_name") or "")
            if not precinct:
                continue
            key = f"Charleston - {precinct}"
            if key not in targets:
                continue

            party = normalize_party(row.get("candidate_party_name") or "")
            cand = (row.get("candidate_name") or "").strip()
            try:
                votes = int(float(row.get("votes") or 0))
            except ValueError:
                continue

            node = agg.get(key)
            if not node:
                node = {"dem": 0, "rep": 0, "other": 0, "dem_cand": "", "rep_cand": ""}
                agg[key] = node

            if party == "DEM":
                node["dem"] += votes
                if cand and not node["dem_cand"]:
                    node["dem_cand"] = cand
            elif party == "REP":
                node["rep"] += votes
                if cand and not node["rep_cand"]:
                    node["rep_cand"] = cand
            else:
                node["other"] += votes

    return agg


def make_row(county_key: str, v: Dict) -> Dict:
    total = int(v.get("dem") or 0) + int(v.get("rep") or 0) + int(v.get("other") or 0)
    margin = int(v.get("rep") or 0) - int(v.get("dem") or 0)
    mpct = round(margin / total * 100, 4) if total else 0
    winner = "R" if margin > 0 else ("D" if margin < 0 else "T")
    return {
        "county": county_key,
        "dem_votes": int(v.get("dem") or 0),
        "rep_votes": int(v.get("rep") or 0),
        "other_votes": int(v.get("other") or 0),
        "total_votes": total,
        "dem_candidate": v.get("dem_cand") or "",
        "rep_candidate": v.get("rep_cand") or "",
        "margin": margin,
        "margin_pct": mpct,
        "winner": winner,
        # Match build_data.py: it derives a color from signed margin_pct.
        "color": margin_color(mpct),
    }


def margin_color(signed_pct: float) -> str:
    # Mirror build_data.py thresholds (avoid importing that module).
    if abs(signed_pct) < 0.001:
        return "#f0f0f0"
    party = "R" if signed_pct > 0 else "D"
    absp = abs(signed_pct)
    colors = [
        (40, "R", "#67000d"),
        (30, "R", "#a50f15"),
        (20, "R", "#cb181d"),
        (10, "R", "#ef3b2c"),
        (5, "R", "#fc8a6a"),
        (0, "R", "#fcbba1"),
        (0, "T", "#f0f0f0"),
        (0, "D", "#c6dbef"),
        (5, "D", "#9ecae1"),
        (10, "D", "#6baed6"),
        (20, "D", "#4292c6"),
        (30, "D", "#2171b5"),
        (40, "D", "#08519c"),
        (999, "D", "#08306b"),
    ]
    best = "#f0f0f0"
    for thresh, p, color in sorted(colors, reverse=True, key=lambda x: x[0]):
        if p == party and absp >= thresh:
            best = color
            break
    return best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seb-list", default=os.path.join("data", "7131_list.csv"))
    ap.add_argument("--polygons", default=os.path.join("data", "Voting_Precincts.geojson"))
    ap.add_argument("--inout-json", default=os.path.join("data", "contests", "president_2024.json"))
    args = ap.parse_args()

    if not os.path.exists(args.seb_list):
        raise SystemExit(f"Missing SEB list CSV: {args.seb_list}")
    if not os.path.exists(args.polygons):
        raise SystemExit(f"Missing polygons: {args.polygons}")
    if not os.path.exists(args.inout_json):
        raise SystemExit(f"Missing contest JSON: {args.inout_json}")

    display_by_norm = load_precinct_display_map(args.polygons)

    # Targets (results keys) in "County - Precinct" form.
    targets = {
        "Charleston - Lincolnville",
        "Charleston - Deer Park 1A",
        "Charleston - Deer Park 1B",
        "Charleston - Deer Park 2A",
        "Charleston - Deer Park 2B",
        "Charleston - Deer Park 2C",
    }

    seb_agg = load_target_rows_from_seb_list(args.seb_list, targets)

    # If polygons have a different display label for any target, prefer polygon display key.
    # This is crucial for Lincolnville, whose polygon label is misspelled ("LICOLNVILLE").
    remapped: Dict[str, Dict] = {}
    for key, node in seb_agg.items():
        # precinct_norms in polygons are already in "COUNTY - PREC" form.
        display = display_by_norm.get(re.sub(r"\s+", " ", key).strip().upper())
        out_key = display or key
        remapped[out_key] = node

    with open(args.inout_json, encoding="utf-8") as fh:
        payload = json.load(fh) or {}

    rows = payload.get("rows") or []
    if not isinstance(rows, list):
        raise SystemExit("Unexpected JSON format: rows is not a list")

    # Remove any existing rows for our target precinct keys (including polygon-display variants).
    remove_keys = set(remapped.keys()) | targets
    kept = [r for r in rows if isinstance(r, dict) and (r.get("county") not in remove_keys)]

    # Keep stable ordering: existing rows first, then add backfilled rows at end.
    added = [make_row(k, v) for k, v in sorted(remapped.items())]
    payload["rows"] = kept + added

    with open(args.inout_json, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"))

    print(f"Patched {args.inout_json}")
    print(f"Added/updated precinct rows: {len(added)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
