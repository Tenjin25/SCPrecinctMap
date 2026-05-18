#!/usr/bin/env python3
import argparse
import csv
import json
import os
import re
from collections import defaultdict
from precinct_crosswalk import load_crosswalk_mappings


OFFICE_MAP = {
    "president": {"president"},
    "us_senate": {"u.s. senate", "us senate"},
    "governor": {"governor and lieutenant governor", "governor"},
    "attorney_general": {"attorney general"},
    "secretary_of_state": {"secretary of state"},
    "state_treasurer": {"state treasurer"},
    "comptroller_general": {"comptroller general"},
    "commissioner_agriculture": {"commissioner of agriculture"},
}


def norm(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 .\-]", "", str(value or ""))
    return re.sub(r"\s+", " ", cleaned).strip().upper()


def margin_color(signed_pct: float) -> str:
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
    for thresh, p, color in colors:
        if p == party and absp >= thresh:
            return color
    return "#f0f0f0"


def find_year_csv(base: str, year: int) -> str:
    year_dir = os.path.join(base, "Data", "_tmpdata", "openelections-data-sc", str(year))
    if not os.path.isdir(year_dir):
        raise SystemExit(f"Missing year directory: {year_dir}")
    matches = [
        os.path.join(year_dir, fn)
        for fn in os.listdir(year_dir)
        if fn.lower().endswith(".csv") and "__sc__general__precinct" in fn.lower()
    ]
    if not matches:
        raise SystemExit(f"No OE precinct CSV found under {year_dir}")
    matches.sort()
    return matches[0]


def load_missing_keys(mismatch_csv: str, contest: str, year: int) -> set[str]:
    out: set[str] = set()
    with open(mismatch_csv, encoding="utf-8", newline="") as fh:
        rd = csv.DictReader(fh)
        for row in rd:
            if str(row.get("contest_type") or "") != contest:
                continue
            try:
                y = int(row.get("year") or 0)
            except ValueError:
                continue
            if y != year:
                continue
            key = norm(row.get("key_norm") or "")
            if key:
                out.add(key)
    return out


def load_polygon_display(voting_precincts_geojson: str) -> dict[str, str]:
    with open(voting_precincts_geojson, encoding="utf-8") as fh:
        gj = json.load(fh) or {}
    out: dict[str, str] = {}
    for feature in gj.get("features", []) or []:
        props = (feature or {}).get("properties") or {}
        key = norm(props.get("precinct_norm") or "")
        if not key:
            continue
        county = str(props.get("county_nam") or "").strip()
        prec = str(props.get("prec_id") or "").strip()
        out[key] = f"{county} - {prec}"
    return out


def load_aliases(aliases_path: str) -> dict[str, str]:
    if not aliases_path or not os.path.exists(aliases_path):
        return {}
    with open(aliases_path, encoding="utf-8") as fh:
        raw = json.load(fh) or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        if k.startswith("_"):
            continue
        nk = norm(k)
        nv = norm(v)
        if nk and nv:
            out[nk] = nv
    return out


def candidate_defaults(rows: list[dict]) -> tuple[str, str]:
    dem = ""
    rep = ""
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not dem and row.get("dem_candidate"):
            dem = str(row.get("dem_candidate") or "")
        if not rep and row.get("rep_candidate"):
            rep = str(row.get("rep_candidate") or "")
        if dem and rep:
            break
    return dem, rep


def add_backfills_for_contest(
    contest_json_path: str,
    csv_path: str,
    contest: str,
    year: int,
    missing_keys: set[str],
    polygon_display: dict[str, str],
    aliases: dict[str, str],
    alias_source_by_key: dict[str, str] | None = None,
) -> tuple[int, int, dict[str, int]]:
    with open(contest_json_path, encoding="utf-8") as fh:
        payload = json.load(fh) or {}
    rows = payload.get("rows") or []
    if not isinstance(rows, list):
        rows = []

    existing_precinct_norms = {
        norm(str(r.get("county") or ""))
        for r in rows
        if isinstance(r, dict) and " - " in str(r.get("county") or "")
    }
    targets = sorted(missing_keys - existing_precinct_norms)
    if not targets:
        return 0, 0, {"alias_crosswalk_hits": 0, "alias_legacy_hits": 0}
    target_set = set(targets)

    offices = OFFICE_MAP.get(contest) or set()
    if not offices:
        raise SystemExit(f"Unsupported contest_type for backfill: {contest}")

    agg = defaultdict(lambda: {"dem_votes": 0, "rep_votes": 0, "other_votes": 0, "dem_candidate": "", "rep_candidate": ""})
    source_stats = {"alias_crosswalk_hits": 0, "alias_legacy_hits": 0}
    with open(csv_path, encoding="utf-8", newline="") as fh:
        rd = csv.DictReader(fh)
        for row in rd:
            office = str(row.get("office") or "").strip().lower()
            if office not in offices:
                continue
            county = str(row.get("county") or "").strip().title()
            precinct = str(row.get("precinct") or "").strip().title()
            raw_key_norm = norm(f"{county} - {precinct}")
            key_norm = aliases.get(raw_key_norm, raw_key_norm)
            if raw_key_norm != key_norm and alias_source_by_key:
                src = alias_source_by_key.get(raw_key_norm, "")
                if src == "crosswalk":
                    source_stats["alias_crosswalk_hits"] += 1
                elif src == "legacy":
                    source_stats["alias_legacy_hits"] += 1
            if key_norm not in target_set:
                continue

            try:
                votes = int(row.get("votes") or 0)
            except (TypeError, ValueError):
                votes = 0
            party = str(row.get("party") or "").strip().upper()
            cand = str(row.get("candidate") or "").strip()
            node = agg[key_norm]

            if party == "DEM":
                node["dem_votes"] += votes
                if cand and not node["dem_candidate"]:
                    node["dem_candidate"] = cand
            elif party == "REP":
                node["rep_votes"] += votes
                if cand and not node["rep_candidate"]:
                    node["rep_candidate"] = cand
            else:
                node["other_votes"] += votes

    default_dem, default_rep = candidate_defaults(rows)
    added = 0
    found = 0
    for key_norm in targets:
        node = agg.get(key_norm)
        if not node:
            continue
        found += 1
        dem = int(node["dem_votes"])
        rep = int(node["rep_votes"])
        other = int(node["other_votes"])
        total = dem + rep + other
        if total <= 0:
            continue

        margin = rep - dem
        margin_pct = round((margin / total) * 100, 4) if total else 0.0
        display = polygon_display.get(key_norm, key_norm.title())
        row_out = {
            "county": display,
            "dem_votes": dem,
            "rep_votes": rep,
            "other_votes": other,
            "total_votes": total,
            "dem_candidate": node["dem_candidate"] or default_dem,
            "rep_candidate": node["rep_candidate"] or default_rep,
            "margin": margin,
            "margin_pct": margin_pct,
            "winner": "R" if margin > 0 else ("D" if margin < 0 else "T"),
            "color": margin_color(margin_pct),
        }
        rows.append(row_out)
        added += 1

    if added:
        county_rows = [r for r in rows if isinstance(r, dict) and " - " not in str(r.get("county") or "")]
        precinct_rows = [r for r in rows if isinstance(r, dict) and " - " in str(r.get("county") or "")]
        precinct_rows.sort(key=lambda r: norm(str(r.get("county") or "")))
        payload["rows"] = county_rows + precinct_rows
        with open(contest_json_path, "w", encoding="utf-8", newline="") as fh:
            json.dump(payload, fh, separators=(",", ":"))

    return added, found, source_stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill missing precinct rows from OpenElections CSV.")
    ap.add_argument("--base", default=".", help="Repo base directory")
    ap.add_argument("--year", type=int, required=True, help="Election year")
    ap.add_argument("--contest", action="append", required=True, help="contest_type (repeatable)")
    ap.add_argument("--mismatch-csv", required=True, help="Mismatch missing-polygons CSV")
    ap.add_argument("--csv", default="", help="OE source CSV path (optional)")
    ap.add_argument("--crosswalk", default="precinct_crosswalk_2024.csv", help="Versioned crosswalk CSV path relative to base")
    ap.add_argument("--crosswalk-min-confidence", default="medium", choices=["low", "medium", "high"], help="Minimum confidence from crosswalk rows")
    ap.add_argument("--no-crosswalk", action="store_true", help="Disable crosswalk aliases")
    ap.add_argument("--debug-mapping-source", action="store_true", help="Print crosswalk vs legacy alias attribution")
    args = ap.parse_args()

    base = os.path.abspath(args.base)
    mismatch_csv = args.mismatch_csv if os.path.isabs(args.mismatch_csv) else os.path.join(base, args.mismatch_csv)
    csv_path = args.csv if args.csv else find_year_csv(base, args.year)
    if not os.path.isabs(csv_path):
        csv_path = os.path.join(base, csv_path)
    if not os.path.exists(csv_path):
        raise SystemExit(f"Missing OE CSV: {csv_path}")
    if not os.path.exists(mismatch_csv):
        raise SystemExit(f"Missing mismatch CSV: {mismatch_csv}")

    polygon_display = load_polygon_display(os.path.join(base, "Data", "Voting_Precincts.geojson"))
    crosswalk_path = os.path.join(base, args.crosswalk)
    aliases = {}
    alias_source_by_key: dict[str, str] = {}
    if not args.no_crosswalk:
        cw_aliases, _, _ = load_crosswalk_mappings(
            crosswalk_path,
            year=args.year,
            min_confidence=args.crosswalk_min_confidence,
            display_by_norm=None,
        )
        aliases.update(cw_aliases)
        for k in cw_aliases:
            alias_source_by_key[k] = "crosswalk"
    legacy_aliases = load_aliases(os.path.join(base, "precinct_aliases.json"))
    for k, v in legacy_aliases.items():
        aliases.setdefault(k, v)
        alias_source_by_key.setdefault(k, "legacy")
    contests_dir = os.path.join(base, "data", "contests")
    total_added = 0
    total_found = 0
    total_source_stats = {"alias_crosswalk_hits": 0, "alias_legacy_hits": 0}

    for contest in args.contest:
        contest = str(contest).strip()
        path = os.path.join(contests_dir, f"{contest}_{args.year}.json")
        if not os.path.exists(path):
            print(f"skip missing contest slice: {path}")
            continue
        missing_keys = load_missing_keys(mismatch_csv, contest, args.year)
        if not missing_keys:
            print(f"{contest}_{args.year}: no missing keys in mismatch CSV")
            continue
        added, found, source_stats = add_backfills_for_contest(
            contest_json_path=path,
            csv_path=csv_path,
            contest=contest,
            year=args.year,
            missing_keys=missing_keys,
            polygon_display=polygon_display,
            aliases=aliases,
            alias_source_by_key=alias_source_by_key,
        )
        total_added += added
        total_found += found
        total_source_stats["alias_crosswalk_hits"] += int(source_stats.get("alias_crosswalk_hits") or 0)
        total_source_stats["alias_legacy_hits"] += int(source_stats.get("alias_legacy_hits") or 0)
        print(f"{contest}_{args.year}: missing_keys={len(missing_keys)} found_in_csv={found} rows_added={added}")
        if args.debug_mapping_source:
            print(
                f"{contest}_{args.year}: alias_hits(crosswalk={source_stats['alias_crosswalk_hits']},legacy={source_stats['alias_legacy_hits']})"
            )

    print(f"Total found in CSV: {total_found}")
    print(f"Total rows added: {total_added}")
    if args.debug_mapping_source:
        print(
            "Total alias mapping hits: "
            f"crosswalk={total_source_stats['alias_crosswalk_hits']}, "
            f"legacy={total_source_stats['alias_legacy_hits']}"
        )


if __name__ == "__main__":
    main()
