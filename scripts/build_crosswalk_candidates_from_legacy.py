#!/usr/bin/env python3
import argparse
import csv
import json
import os
import re
from collections import defaultdict


def norm(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 .\-]", "", str(value or ""))
    return re.sub(r"\s+", " ", cleaned).strip().upper()


def load_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh) or {}
    return payload if isinstance(payload, dict) else {}


def build_result_source_lookup(results_csv_path: str, office: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if not results_csv_path or not os.path.exists(results_csv_path):
        return out
    with open(results_csv_path, encoding="utf-8", newline="") as fh:
        rd = csv.DictReader(fh)
        for row in rd:
            if str(row.get("office") or "").strip().lower() != str(office).strip().lower():
                continue
            county = str(row.get("county") or "").strip().title()
            precinct = str(row.get("precinct") or "").strip().title()
            display = f"{county} - {precinct}"
            key = norm(display)
            if key and key not in out:
                out[key] = display
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Build crosswalk candidate rows from legacy alias/split mappings.")
    ap.add_argument("--base", default=".", help="Repo base directory")
    ap.add_argument("--year", type=int, default=2024, help="Election year")
    ap.add_argument("--contest", default="president", help="Contest type")
    ap.add_argument("--crosswalk", default="precinct_crosswalk_2024.csv", help="Existing crosswalk CSV relative to base")
    ap.add_argument("--aliases", default="precinct_aliases.json", help="Legacy aliases JSON relative to base")
    ap.add_argument("--splits", default="precinct_splits_2024.json", help="Legacy splits JSON relative to base")
    ap.add_argument("--weighted-splits", default="precinct_weighted_splits_2024.json", help="Legacy weighted splits JSON relative to base")
    ap.add_argument(
        "--results-csv",
        default="Data/_tmpdata/openelections-data-sc/2024/20241105__sc__general__precinct.csv",
        help="Results CSV used to preserve exact source_result_key strings",
    )
    ap.add_argument("--office", default="president", help="Office name in results CSV")
    ap.add_argument(
        "--out",
        default="data/crosswalk/precinct_crosswalk_2024_candidates.csv",
        help="Output CSV path relative to base",
    )
    args = ap.parse_args()

    base = os.path.abspath(args.base)
    crosswalk_path = os.path.join(base, args.crosswalk)
    aliases_path = os.path.join(base, args.aliases)
    splits_path = os.path.join(base, args.splits)
    weighted_path = os.path.join(base, args.weighted_splits)
    results_csv_path = os.path.join(base, args.results_csv)
    out_path = os.path.join(base, args.out)

    existing_pairs: set[tuple[str, str]] = set()
    if os.path.exists(crosswalk_path):
        with open(crosswalk_path, encoding="utf-8", newline="") as fh:
            rd = csv.DictReader(fh)
            for row in rd:
                if str(row.get("status") or "").strip().lower() != "approved":
                    continue
                if str(row.get("contest_type") or "").strip().lower() != str(args.contest).strip().lower():
                    continue
                try:
                    year = int(str(row.get("year") or "").strip())
                except ValueError:
                    continue
                if year != int(args.year):
                    continue
                src = str(row.get("source_result_key") or "").strip()
                tgt = str(row.get("target_polygon_key") or "").strip()
                if src and tgt:
                    existing_pairs.add((norm(src), norm(tgt)))

    legacy_pairs: dict[tuple[str, str], str] = {}
    aliases = load_json(aliases_path)
    for k, v in aliases.items():
        if not isinstance(k, str) or not isinstance(v, str) or k.startswith("_"):
            continue
        legacy_pairs[(norm(k), norm(v))] = "legacy_alias"

    splits = load_json(splits_path)
    for k, v in splits.items():
        if not isinstance(k, str) or k.startswith("_"):
            continue
        vals = v if isinstance(v, list) else [v]
        for item in vals:
            if isinstance(item, str):
                legacy_pairs[(norm(k), norm(item))] = "legacy_split"

    weighted = load_json(weighted_path)
    for k, v in weighted.items():
        if not isinstance(k, str) or k.startswith("_"):
            continue
        if isinstance(v, dict):
            items = [(to, wt) for to, wt in v.items()]
        elif isinstance(v, list):
            items = [((it or {}).get("to"), (it or {}).get("weight")) for it in v if isinstance(it, dict)]
        else:
            items = []
        for to, wt in items:
            if not isinstance(to, str):
                continue
            try:
                w = float(wt)
            except (TypeError, ValueError):
                continue
            if w <= 0:
                continue
            legacy_pairs[(norm(k), norm(to))] = "legacy_weighted_split"

    by_source: dict[str, set[str]] = defaultdict(set)
    for (src, tgt), _ in legacy_pairs.items():
        if (src, tgt) in existing_pairs:
            continue
        if src and tgt:
            by_source[src].add(tgt)

    source_lookup = build_result_source_lookup(results_csv_path, args.office)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(
            [
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
        )
        for src in sorted(by_source.keys()):
            targets = sorted(by_source[src])
            source_kind = "split" if len(targets) > 1 else "alias"
            source_display = source_lookup.get(src, src.title())
            for tgt in targets:
                wr.writerow(
                    [
                        args.year,
                        args.contest,
                        "",
                        source_display,
                        tgt.title(),
                        "1.0000",
                        "approved",
                        "medium",
                        f"generated_from_legacy_{source_kind}_mapping",
                    ]
                )

    print(f"wrote {out_path}")
    print(f"existing approved crosswalk pairs: {len(existing_pairs)}")
    print(f"generated candidate pairs: {sum(len(v) for v in by_source.values())}")
    print(f"source keys resolved from results CSV: {len(source_lookup)}")


if __name__ == "__main__":
    main()
