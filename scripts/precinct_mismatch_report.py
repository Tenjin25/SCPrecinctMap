import argparse
import csv
import difflib
import json
import os
import re
from collections import defaultdict

DEFAULT_IGNORED_MISSING = {
    "AIKEN - SRS",
    "BARNWELL - SRS",
}


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 .\-]", "", str(s or ""))).strip().upper()


def load_precinct_polygons(voting_precincts_geojson_path: str):
    with open(voting_precincts_geojson_path, encoding="utf-8") as fh:
        gj = json.load(fh)

    by_county = defaultdict(list)  # county_norm -> list[(precinct_norm, display)]
    all_norms = set()
    for f in gj.get("features", []):
        p = (f or {}).get("properties") or {}
        pn = norm(p.get("precinct_norm") or "")
        if not pn:
            continue
        county = str(p.get("county_nam") or "").strip()
        prec = str(p.get("prec_id") or "").strip()
        display = f"{county} - {prec}".strip(" -")
        cn = norm(county)
        by_county[cn].append((pn, display))
        all_norms.add(pn)
    return all_norms, by_county


def load_contest_precinct_rows(contest_json_path: str):
    with open(contest_json_path, encoding="utf-8") as fh:
        payload = json.load(fh)
    rows = payload.get("rows") or []
    precinct_keys = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        key = str(r.get("county") or "")
        if " - " not in key:
            continue
        precinct_keys.append(key)
    return precinct_keys


def load_aliases(aliases_path: str):
    if not aliases_path or not os.path.exists(aliases_path):
        return {}
    with open(aliases_path, encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        if k.startswith("_"):
            continue
        out[norm(k)] = norm(v)
    return out


def best_matches(needle_precinct: str, haystack: list[tuple[str, str]], limit: int = 5):
    """
    Return list of (score, polygon_precinct_norm, polygon_display).
    """
    if not needle_precinct or not haystack:
        return []
    # Compare only precinct part (after ' - ') for better signal.
    n = needle_precinct.split(" - ", 1)[1] if " - " in needle_precinct else needle_precinct
    n = norm(n)
    scored = []
    for pn, display in haystack:
        p = pn.split(" - ", 1)[1] if " - " in pn else pn
        p = norm(p)
        score = difflib.SequenceMatcher(None, n, p).ratio()
        scored.append((score, pn, display))
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored[:limit]


def main():
    ap = argparse.ArgumentParser(description="Report precinct name mismatches between results slices and polygon precincts.")
    ap.add_argument("--base", default=os.path.join(os.path.dirname(__file__), ".."), help="Repo root (default: ../)")
    ap.add_argument("--contest", default="president", help="Contest type (e.g. president, us_senate)")
    ap.add_argument("--year", default="2024", help="Election year (e.g. 2024)")
    ap.add_argument("--out", default="", help="Output CSV path (default: scripts/out/precinct_mismatches_<contest>_<year>.csv)")
    ap.add_argument("--aliases", default="precinct_aliases.json", help="Alias JSON path relative to base")
    ap.add_argument(
        "--ignore-missing",
        default="",
        help="Comma-separated normalized polygon keys to ignore in missing_polygon output (in addition to defaults).",
    )
    ap.add_argument(
        "--no-default-ignore",
        action="store_true",
        help="Do not apply built-in missing_polygon ignores (AIKEN - SRS, BARNWELL - SRS).",
    )
    args = ap.parse_args()

    base = os.path.abspath(args.base)
    contest = str(args.contest).strip()
    year = str(args.year).strip()

    voting_precincts = os.path.join(base, "data", "Voting_Precincts.geojson")
    contest_path = os.path.join(base, "data", "contests", f"{contest}_{year}.json")
    aliases_path = os.path.join(base, args.aliases)

    if not os.path.exists(voting_precincts):
        raise SystemExit(f"Missing {voting_precincts}")
    if not os.path.exists(contest_path):
        raise SystemExit(f"Missing {contest_path}")

    poly_norms, poly_by_county = load_precinct_polygons(voting_precincts)
    rows = load_contest_precinct_rows(contest_path)
    aliases = load_aliases(aliases_path)

    row_norms = set()
    row_norm_to_raw = {}
    for k in rows:
        nk = norm(k)
        # Apply alias if present (FROM -> TO)
        nk = aliases.get(nk, nk)
        row_norms.add(nk)
        row_norm_to_raw.setdefault(nk, k)

    missing_polys = sorted(poly_norms - row_norms)
    extra_rows = sorted(row_norms - poly_norms)

    ignored_missing = set()
    if not args.no_default_ignore:
        ignored_missing |= set(DEFAULT_IGNORED_MISSING)
    if args.ignore_missing:
        ignored_missing |= {norm(x) for x in str(args.ignore_missing).split(",") if str(x).strip()}
    if ignored_missing:
        missing_polys = [k for k in missing_polys if k not in ignored_missing]

    out_path = args.out
    if not out_path:
        out_dir = os.path.join(base, "scripts", "out")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"precinct_mismatches_{contest}_{year}.csv")

    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["type", "key_norm", "raw_key_example", "best_match_1", "score_1", "best_match_2", "score_2", "best_match_3", "score_3"])

        for pn in missing_polys:
            county = pn.split(" - ", 1)[0] if " - " in pn else ""
            matches = best_matches(pn, poly_by_county.get(norm(county), []), limit=3)
            row = ["missing_polygon", pn, "", "", "", "", "", "", ""]
            for i, (score, _, display) in enumerate(matches):
                row[3 + i * 2] = display
                row[4 + i * 2] = f"{score:.4f}"
            w.writerow(row)

        for rn in extra_rows:
            raw_ex = row_norm_to_raw.get(rn, "")
            county = rn.split(" - ", 1)[0] if " - " in rn else ""
            matches = best_matches(rn, poly_by_county.get(norm(county), []), limit=3)
            row = ["extra_result_row", rn, raw_ex, "", "", "", "", "", ""]
            for i, (score, _, display) in enumerate(matches):
                row[3 + i * 2] = display
                row[4 + i * 2] = f"{score:.4f}"
            w.writerow(row)

    print(f"Wrote {out_path}")
    print(f"Polygons: {len(poly_norms)} | Result precinct rows: {len(row_norms)}")
    if ignored_missing:
        print(f"Ignored missing polygons: {len(ignored_missing)}")
    print(f"Missing polygons (no result row): {len(missing_polys)}")
    print(f"Extra result rows (no polygon): {len(extra_rows)}")


if __name__ == "__main__":
    main()
