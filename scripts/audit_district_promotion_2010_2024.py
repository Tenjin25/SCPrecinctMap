#!/usr/bin/env python3
"""Review 2010–2024 staged district changes before any live promotion.

The DRA comparisons cover only elections with local 2022 House-line exports.
They are independent estimates, not official election returns.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTESTS = {"Pres": "president", "US Senate": "us_senate", "gov": "governor", "supt": "superintendent"}


def benchmark_margins() -> dict[tuple[str, str], float]:
    margins = {}
    for source in (ROOT / "data/2022 state house files").glob("district-statistics*.csv"):
        year, label = source.stem.removeprefix("district-statistics ").split(" ", 1)
        contest = CONTESTS.get(label)
        if not contest or not 2010 <= int(year) <= 2024:
            continue
        name = f"state_house_{contest}_{year}_2022_lines.json"
        with source.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                district = row["ID"].strip()
                if district.isdigit():
                    margins[(name, district)] = (float(row["Rep"]) - float(row["Dem"])) * 100
    return margins


def scope_of(relative: Path) -> str:
    if relative.parent.name == "state_house_2022_lines":
        return "state_house_2022_lines"
    if relative.parent.name == "state_house_2024_lines":
        return "state_house_2024_lines"
    if relative.name.startswith("state_house_"):
        return "state_house_root"
    if relative.name.startswith("state_senate_"):
        return "state_senate"
    if relative.name.startswith("congressional_"):
        return "congressional"
    raise ValueError(f"Unknown district scope: {relative}")


def winner_for_margin(margin: float) -> str:
    return "R" if margin > 0 else "D" if margin < 0 else "T"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-root", default="data/district_contests_county_constrained_staging")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    staging = ROOT / args.staging_root
    live = ROOT / "data/district_contests"
    benchmarks = benchmark_margins()
    files = []
    flips = []
    by_group = defaultdict(lambda: {"files": 0, "district_rows": 0, "winner_changes": 0,
                                    "benchmark_rows": 0, "benchmark_supported_flips": 0,
                                    "benchmark_opposed_flips": 0, "benchmark_neither_flips": 0,
                                    "county_fallback_votes": 0})

    for path in sorted(staging.rglob("*.json")):
        relative = path.relative_to(staging)
        if not (live / relative).exists():
            continue
        name = relative.name
        match = re.search(r"_(20\d{2})(?:_202[24]_lines)?\.json$", name)
        if not match:
            continue
        year = int(match.group(1))
        if not 2010 <= year <= 2024:
            continue
        scope = scope_of(relative)
        staged = json.loads(path.read_text(encoding="utf-8"))
        served = json.loads((live / relative).read_text(encoding="utf-8"))
        if "general" not in staged or "general" not in served:
            continue
        proposed = staged["general"]["results"]
        current = served["general"]["results"]
        if set(proposed) != set(current):
            raise ValueError(f"District set changed: {relative}")
        row_flips = []
        benchmark_count = 0
        for district in sorted(proposed, key=lambda key: int(key) if key.isdigit() else key):
            old = current[district]
            new = proposed[district]
            target = benchmarks.get((name, district)) if scope == "state_house_2022_lines" else None
            if target is not None:
                benchmark_count += 1
            if old.get("winner") == new.get("winner"):
                continue
            row = {"file": relative.as_posix(), "year": year, "scope": scope, "district": district,
                   "served_winner": old.get("winner"), "staged_winner": new.get("winner"),
                   "served_margin_pp": old["margin_pct"], "staged_margin_pp": new["margin_pct"],
                   "margin_change_pp": round(float(new["margin_pct"]) - float(old["margin_pct"]), 4),
                   "dra_margin_pp": round(target, 4) if target is not None else None,
                   "dra_support": ("staged" if new.get("winner") == winner_for_margin(target)
                                   else "served" if old.get("winner") == winner_for_margin(target)
                                   else "neither") if target is not None else "unbenchmarked"}
            row_flips.append(row)
            flips.append(row)

        fallback_votes = int(staged.get("meta", {}).get("precinct_votes_county_share_fallback") or 0)
        file_row = {"file": relative.as_posix(), "year": year, "scope": scope,
                    "district_rows": len(proposed), "winner_changes": len(row_flips),
                    "benchmark_rows": benchmark_count,
                    "benchmark_supported_flips": sum(row["dra_support"] == "staged" for row in row_flips),
                    "benchmark_opposed_flips": sum(row["dra_support"] == "served" for row in row_flips),
                    "benchmark_neither_flips": sum(row["dra_support"] == "neither" for row in row_flips),
                    "county_fallback_votes": fallback_votes}
        files.append(file_row)
        group = by_group[(year, scope)]
        for key in group:
            group[key] += file_row[key] if key != "files" else 1

    summary = {"files": len(files), "district_rows": sum(row["district_rows"] for row in files),
               "winner_changes": len(flips),
               "benchmark_supported_flips": sum(row["dra_support"] == "staged" for row in flips),
               "benchmark_opposed_flips": sum(row["dra_support"] == "served" for row in flips),
               "benchmark_neither_flips": sum(row["dra_support"] == "neither" for row in flips),
               "unbenchmarked_flips": sum(row["dra_support"] == "unbenchmarked" for row in flips)}
    report = {"method": "2010–2024 served-versus-staged winner ledger; DRA support is diagnostic and available only for selected 2022 House-line elections.",
              "summary": summary,
              "by_year_scope": [{"year": year, "scope": scope, **counts}
                                for (year, scope), counts in sorted(by_group.items())],
              "files": files, "winner_changes": flips}
    if args.write:
        (staging / "promotion_review_2010_2024.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
