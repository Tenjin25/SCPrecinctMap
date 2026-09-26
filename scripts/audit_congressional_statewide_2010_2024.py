#!/usr/bin/env python3
"""Rank statewide-contest projections on congressional lines for review."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("dem_votes", "rep_votes", "other_votes", "total_votes")


def totals(rows: list[dict]) -> dict[str, int]:
    return {field: sum(int(row.get(field) or 0) for row in rows) for field in FIELDS}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-root", default="data/district_contests_county_constrained_staging")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    stage = ROOT / args.staging_root
    live = ROOT / "data/district_contests"
    sources = ROOT / "data/contests_2025_crosswalked"
    manifest = json.loads((sources / "manifest.json").read_text(encoding="utf-8"))
    eligible_sources = {entry["file"] for entry in manifest["files"]}
    files = []
    for path in sorted(stage.glob("congressional_*.json")):
        contest, year_text = path.stem.removeprefix("congressional_").rsplit("_", 1)
        year = int(year_text)
        if not 2010 <= year <= 2024:
            continue
        if f"{contest}_{year}.json" not in eligible_sources:
            continue
        source = json.loads((sources / f"{contest}_{year}.json").read_text(encoding="utf-8"))
        canonical = totals([row for row in source["rows"] if " - " not in row["county"]])
        served = json.loads((live / path.name).read_text(encoding="utf-8"))
        staged = json.loads(path.read_text(encoding="utf-8"))
        old = served["general"]["results"]
        new = staged["general"]["results"]
        served_totals = totals(list(old.values()))
        staged_totals = totals(list(new.values()))
        margin_changes = {district: round(float(new[district]["margin_pct"]) -
                                          float(old[district]["margin_pct"]), 4) for district in new}
        files.append({
            "file": path.name, "year": year, "contest": contest,
            "canonical_statewide_totals": canonical,
            "served_statewide_delta": {field: served_totals[field] - canonical[field] for field in FIELDS},
            "staged_statewide_delta": {field: staged_totals[field] - canonical[field] for field in FIELDS},
            "largest_margin_change_pp": max(abs(value) for value in margin_changes.values()),
            "largest_margin_change_district": max(margin_changes, key=lambda key: abs(margin_changes[key])),
            "winner_changes": sum(old[district]["winner"] != new[district]["winner"] for district in new),
            "precinct_votes_county_share_fallback": staged["meta"]["precinct_votes_county_share_fallback"],
            "match_coverage_pct": staged["meta"]["match_coverage_pct"],
        })
    by_year = defaultdict(list)
    for row in files:
        by_year[row["year"]].append(row)
    years = [{"year": year, "files": len(rows),
              "largest_served_vote_shortfall": max(-row["served_statewide_delta"]["total_votes"] for row in rows),
              "largest_margin_change_pp": max(row["largest_margin_change_pp"] for row in rows),
              "winner_changes": sum(row["winner_changes"] for row in rows),
              "staged_statewide_failures": sum(any(row["staged_statewide_delta"].values()) for row in rows)}
             for year, rows in sorted(by_year.items())]
    report = {"method": "Compare served and staged seven-district sums with official county-row statewide totals for every 2010–2024 statewide contest projected on congressional lines. Vote shortfall is per contest, not a sum of distinct voters.",
              "summary": {"files": len(files), "years": len(years),
                          "served_statewide_failures": sum(any(row["served_statewide_delta"].values()) for row in files),
                          "staged_statewide_failures": sum(any(row["staged_statewide_delta"].values()) for row in files),
                          "winner_changes": sum(row["winner_changes"] for row in files)},
              "years": years,
              "files": sorted(files, key=lambda row: (-max(0, -row["served_statewide_delta"]["total_votes"]),
                                                     -row["largest_margin_change_pp"]))}
    if args.write:
        (stage / "congressional_statewide_review_2010_2024.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"summary": report["summary"], "years": years}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
