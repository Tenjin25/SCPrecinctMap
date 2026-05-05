#!/usr/bin/env python3
"""
Convert SC State Election Board "list" exports (like `7131_list.csv`) into the
OpenElections-style precinct CSV shape expected by `build_data.py`.

Input columns (expected, from SEB list export):
  - office_name, candidate_name, candidate_party_name, votes
  - division_type (County/Precinct), division_name

Output columns:
  county,precinct,office,district,party,candidate,votes

Notes:
  - The SEB "list" export often omits county on precinct rows (e.g. "Deer Park 01a").
    For those, we infer the county by looking up the precinct label in
    `data/Voting_Precincts.geojson` and requiring a unique match.
  - Rows like "Total Votes Cast" / "Total Ballots Cast" / "Overvotes/Undervotes"
    are dropped (they have no party and should not be treated as candidates).
"""

import argparse
import csv
import json
import os
import re
from collections import defaultdict


def normalize(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9 .\-]", "", str(s or ""))
    return re.sub(r"\s+", " ", s).strip().upper()


_NO_LEADING_ZEROS = re.compile(r"\bNO\.?\s*0+(\d+)\b", re.IGNORECASE)
_NUM_SLASH_NUM = re.compile(r"\b0*([0-9]{1,3})\s*/\s*([0-9]{1,2})\b")
_NUM_SLASH_ALPHA = re.compile(r"\b0*([0-9]{1,3})\s*/\s*([A-Z]{1,2})\b", re.IGNORECASE)
_LEADING_ZERO_NUM_ALPHA = re.compile(r"\b0+([0-9]+)([A-Z]{1,2})\b", re.IGNORECASE)
_LEADING_ZERO_NUM = re.compile(r"\b0+([0-9]+)\b")
_APOS = re.compile(r"[’'`]")


def normalize_precinct_label(name: str) -> str:
    s = (name or "").strip()
    if not s:
        return ""
    s = _APOS.sub("", s)
    s = _NO_LEADING_ZEROS.sub(lambda m: f"No. {int(m.group(1))}", s)
    s = _NUM_SLASH_ALPHA.sub(lambda m: f"{int(m.group(1))}{m.group(2).upper()}", s)
    s = _NUM_SLASH_NUM.sub(lambda m: f"{int(m.group(1))}{m.group(2)}", s)
    s = _LEADING_ZERO_NUM_ALPHA.sub(lambda m: f"{int(m.group(1))}{m.group(2).upper()}", s)
    s = _LEADING_ZERO_NUM.sub(lambda m: f"{int(m.group(1))}", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.title()


def load_polygon_precinct_index(polygons_path: str) -> tuple[set[str], dict[str, set[str]]]:
    """
    Returns:
      counties_norm: set of normalized county names
      county_by_prec_norm: map normalize(precinct_label) -> set(normalize(county))
    """
    with open(polygons_path, encoding="utf-8") as fh:
        gj = json.load(fh) or {}

    counties_norm: set[str] = set()
    county_by_prec_norm: dict[str, set[str]] = defaultdict(set)
    for feat in gj.get("features", []) or []:
        props = (feat or {}).get("properties") or {}
        county = (props.get("county_nam") or "").strip()
        prec = (props.get("prec_id") or "").strip()
        if not (county and prec):
            continue
        c_norm = normalize(county)
        p_norm = normalize(normalize_precinct_label(prec))
        if not (c_norm and p_norm):
            continue
        counties_norm.add(c_norm)
        county_by_prec_norm[p_norm].add(c_norm)
    return counties_norm, county_by_prec_norm


def office_to_oe(office_name: str) -> str:
    o = (office_name or "").strip().lower()
    if o == "president of the united states":
        return "PRESIDENT"
    # Fallback: keep as-is but uppercased.
    return (office_name or "").strip().upper()


def should_skip_row(row: dict) -> bool:
    cand = (row.get("candidate_name") or "").strip().lower()
    party = (row.get("candidate_party_name") or "").strip()
    if not party:
        # Totals/overvotes/undervotes rows typically have no party.
        return True
    if cand in {"total votes cast", "total ballots cast", "overvotes/undervotes"}:
        return True
    return False


def infer_county_and_precinct(
    division_type: str,
    division_name: str,
    counties_norm: set[str],
    county_by_prec_norm: dict[str, set[str]],
) -> tuple[str, str]:
    dt = (division_type or "").strip().lower()
    dn = (division_name or "").strip()
    if not dn:
        return "", ""

    if dt == "county":
        return dn.title(), ""

    if dt != "precinct":
        return "", ""

    # Some rows are like "Abbeville No. 01" (county embedded).
    dn_norm = normalize(dn)
    for c_norm in counties_norm:
        # Match "<county> <rest>" or "<county> - <rest>"
        if dn_norm.startswith(c_norm + " "):
            county = c_norm.title()
            return county, normalize_precinct_label(dn)
        if dn_norm.startswith(c_norm + " - "):
            county = c_norm.title()
            # Here the right side is typically the precinct label itself.
            precinct = dn[len(c_norm) + 3 :].strip()
            return county, normalize_precinct_label(precinct)

    # Otherwise, try unique precinct label lookup from polygons.
    p_norm = normalize(normalize_precinct_label(dn))
    cands = county_by_prec_norm.get(p_norm) or set()
    if len(cands) == 1:
        county = next(iter(cands)).title()
        return county, normalize_precinct_label(dn)

    # Ambiguous or missing: leave blank so it can be diagnosed upstream.
    return "", normalize_precinct_label(dn)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Path to SEB list CSV (e.g., data/7131_list.csv)")
    ap.add_argument("--output", required=True, help="Path to write OpenElections-style precinct CSV")
    ap.add_argument(
        "--polygons",
        default=os.path.join("data", "Voting_Precincts.geojson"),
        help="Precinct polygon GeoJSON for county inference (default: data/Voting_Precincts.geojson)",
    )
    args = ap.parse_args()

    if not os.path.exists(args.input):
        raise SystemExit(f"Missing input: {args.input}")
    if not os.path.exists(args.polygons):
        raise SystemExit(f"Missing polygons: {args.polygons}")

    counties_norm, county_by_prec_norm = load_polygon_precinct_index(args.polygons)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    out_rows = 0
    missing_county = 0

    with open(args.input, newline="", encoding="utf-8") as fin, open(
        args.output, "w", newline="", encoding="utf-8"
    ) as fout:
        r = csv.DictReader(fin)
        w = csv.DictWriter(
            fout,
            fieldnames=["county", "precinct", "office", "district", "party", "candidate", "votes"],
        )
        w.writeheader()

        for row in r:
            if should_skip_row(row):
                continue

            county, precinct = infer_county_and_precinct(
                row.get("division_type"),
                row.get("division_name"),
                counties_norm,
                county_by_prec_norm,
            )

            if not county:
                missing_county += 1
                continue

            w.writerow(
                {
                    "county": county,
                    "precinct": precinct,
                    "office": office_to_oe(row.get("office_name") or ""),
                    "district": "",
                    "party": (row.get("candidate_party_name") or "").strip().upper(),
                    "candidate": (row.get("candidate_name") or "").strip(),
                    "votes": str(row.get("votes") or "").strip(),
                }
            )
            out_rows += 1

    print(f"Wrote {args.output}")
    print(f"Rows: {out_rows} (skipped missing/ambiguous county: {missing_county})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
