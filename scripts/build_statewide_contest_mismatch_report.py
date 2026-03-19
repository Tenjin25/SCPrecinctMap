#!/usr/bin/env python3
import argparse
import csv
import json
import os
import re
from collections import Counter


DEFAULT_IGNORED_MISSING = {
    "AIKEN - SRS",
    "BARNWELL - SRS",
}


def norm(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 .\-]", "", str(value or ""))
    return re.sub(r"\s+", " ", cleaned).strip().upper()


def load_polygon_keys(voting_precincts_geojson: str) -> set[str]:
    with open(voting_precincts_geojson, encoding="utf-8") as fh:
        gj = json.load(fh) or {}
    out: set[str] = set()
    for feature in gj.get("features", []) or []:
        props = (feature or {}).get("properties") or {}
        pn = norm(props.get("precinct_norm") or "")
        if pn:
            out.add(pn)
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


def parse_contest_year(filename: str) -> tuple[str | None, int | None]:
    if not filename.endswith(".json") or filename == "manifest.json":
        return None, None
    stem = filename[:-5]
    i = stem.rfind("_")
    if i <= 0:
        return None, None
    contest = stem[:i]
    year_s = stem[i + 1 :]
    try:
        year = int(year_s)
    except ValueError:
        return None, None
    return contest, year


def build_report(
    contests_dir: str,
    polygon_norms: set[str],
    aliases: dict[str, str],
    ignored_missing: set[str],
) -> tuple[list[dict], list[dict], list[dict]]:
    summary_rows: list[dict] = []
    extra_rows: list[dict] = []
    missing_rows: list[dict] = []

    for fn in sorted(os.listdir(contests_dir)):
        contest, year = parse_contest_year(fn)
        if not contest:
            continue
        path = os.path.join(contests_dir, fn)
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh) or {}
        rows = payload.get("rows") or []

        result_precinct_norms: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = str(row.get("county") or "")
            if " - " not in key:
                continue
            nk = norm(key)
            nk = aliases.get(nk, nk)
            if nk:
                result_precinct_norms.add(nk)

        missing = sorted((polygon_norms - result_precinct_norms) - ignored_missing)
        extra = sorted(result_precinct_norms - polygon_norms)

        summary_rows.append(
            {
                "contest_type": contest,
                "year": year,
                "result_precinct_rows": len(result_precinct_norms),
                "missing_polygons": len(missing),
                "extra_result_rows": len(extra),
            }
        )

        for key_norm in extra:
            county_norm = key_norm.split(" - ", 1)[0] if " - " in key_norm else ""
            extra_rows.append(
                {
                    "contest_type": contest,
                    "year": year,
                    "county_norm": county_norm,
                    "key_norm": key_norm,
                }
            )

        for key_norm in missing:
            county_norm = key_norm.split(" - ", 1)[0] if " - " in key_norm else ""
            missing_rows.append(
                {
                    "contest_type": contest,
                    "year": year,
                    "county_norm": county_norm,
                    "key_norm": key_norm,
                }
            )

    summary_rows.sort(key=lambda r: (int(r["year"]), str(r["contest_type"])))
    return summary_rows, extra_rows, missing_rows


def write_csv(path: str, fieldnames: list[str], rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def write_county_rollups(rows: list[dict], count_field: str, out_path: str) -> None:
    counts: Counter[tuple[str, int, str]] = Counter()
    for row in rows:
        contest = str(row.get("contest_type") or "")
        year = int(row.get("year") or 0)
        county = str(row.get("county_norm") or "")
        counts[(contest, year, county)] += 1

    out_rows = [
        {
            "contest_type": contest,
            "year": year,
            "county_norm": county,
            count_field: n,
        }
        for (contest, year, county), n in counts.items()
    ]
    out_rows.sort(key=lambda r: (int(r["year"]), str(r["contest_type"]), -int(r[count_field]), str(r["county_norm"])))
    write_csv(out_path, ["contest_type", "year", "county_norm", count_field], out_rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build statewide mismatch reports for all contest slices.")
    ap.add_argument("--base", default=".", help="Repo base path")
    ap.add_argument(
        "--contests-dir",
        default=os.path.join("data", "contests"),
        help="Contest slices directory (relative to base unless absolute)",
    )
    ap.add_argument(
        "--voting-precincts",
        default=os.path.join("data", "Voting_Precincts.geojson"),
        help="Voting precinct GeoJSON path (relative to base unless absolute)",
    )
    ap.add_argument(
        "--aliases",
        default="precinct_aliases.json",
        help="Alias JSON path (relative to base unless absolute)",
    )
    ap.add_argument(
        "--out-prefix",
        default="contest_mismatch_summary",
        help="Output filename prefix under scripts/out",
    )
    ap.add_argument(
        "--out-dir",
        default=os.path.join("scripts", "out"),
        help="Output directory (relative to base unless absolute)",
    )
    ap.add_argument(
        "--ignore-missing",
        default="",
        help="Comma-separated normalized polygon keys to ignore in missing output",
    )
    ap.add_argument(
        "--no-default-ignore",
        action="store_true",
        help="Do not apply built-in ignored missing keys",
    )
    args = ap.parse_args()

    base = os.path.abspath(args.base)
    contests_dir = args.contests_dir if os.path.isabs(args.contests_dir) else os.path.join(base, args.contests_dir)
    voting_precincts = (
        args.voting_precincts
        if os.path.isabs(args.voting_precincts)
        else os.path.join(base, args.voting_precincts)
    )
    aliases_path = args.aliases if os.path.isabs(args.aliases) else os.path.join(base, args.aliases)
    out_dir = args.out_dir if os.path.isabs(args.out_dir) else os.path.join(base, args.out_dir)

    if not os.path.isdir(contests_dir):
        raise SystemExit(f"Missing contests dir: {contests_dir}")
    if not os.path.exists(voting_precincts):
        raise SystemExit(f"Missing voting precincts file: {voting_precincts}")

    ignored_missing = set()
    if not args.no_default_ignore:
        ignored_missing |= set(DEFAULT_IGNORED_MISSING)
    if args.ignore_missing:
        ignored_missing |= {norm(x) for x in str(args.ignore_missing).split(",") if str(x).strip()}

    polygon_norms = load_polygon_keys(voting_precincts)
    aliases = load_aliases(aliases_path)
    summary_rows, extra_rows, missing_rows = build_report(
        contests_dir=contests_dir,
        polygon_norms=polygon_norms,
        aliases=aliases,
        ignored_missing=ignored_missing,
    )

    summary_path = os.path.join(out_dir, f"{args.out_prefix}.csv")
    extra_path = os.path.join(out_dir, f"{args.out_prefix.replace('summary', 'extra_rows')}.csv")
    missing_path = os.path.join(out_dir, f"{args.out_prefix.replace('summary', 'missing_polygons')}.csv")
    extra_county_path = os.path.join(
        out_dir, f"{args.out_prefix.replace('summary', 'extra_rows_county')}.csv"
    )
    missing_county_path = os.path.join(
        out_dir, f"{args.out_prefix.replace('summary', 'missing_polygons_county')}.csv"
    )

    write_csv(
        summary_path,
        ["contest_type", "year", "result_precinct_rows", "missing_polygons", "extra_result_rows"],
        summary_rows,
    )
    write_csv(extra_path, ["contest_type", "year", "county_norm", "key_norm"], extra_rows)
    write_csv(missing_path, ["contest_type", "year", "county_norm", "key_norm"], missing_rows)
    write_county_rollups(extra_rows, "extra_count", extra_county_path)
    write_county_rollups(missing_rows, "missing_count", missing_county_path)

    with_extra = sum(1 for r in summary_rows if int(r.get("extra_result_rows") or 0) > 0)
    total_extra = sum(int(r.get("extra_result_rows") or 0) for r in summary_rows)
    total_missing = sum(int(r.get("missing_polygons") or 0) for r in summary_rows)

    print(f"Wrote {summary_path}")
    print(f"Wrote {extra_path}")
    print(f"Wrote {missing_path}")
    print(f"Wrote {extra_county_path}")
    print(f"Wrote {missing_county_path}")
    print(f"Contests scanned: {len(summary_rows)}")
    print(f"Contests with extra rows: {with_extra}")
    print(f"Total extra rows: {total_extra}")
    print(f"Total missing polygons: {total_missing}")


if __name__ == "__main__":
    main()
