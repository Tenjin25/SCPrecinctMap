#!/usr/bin/env python3
"""Compare served and staged 2022-line House margins with local DRA exports.

The DRA exports are independent plan-level election estimates. They provide a
check on district placement; they are not official precinct or county returns.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTESTS = {"Pres": "president", "US Senate": "us_senate", "gov": "governor", "supt": "superintendent"}


def comparison(values: list[float]) -> dict:
    ordered = sorted(values)
    return {
        "mean_abs_margin_delta_pp": round(statistics.mean(ordered), 4),
        "median_abs_margin_delta_pp": round(statistics.median(ordered), 4),
        "p95_abs_margin_delta_pp": round(ordered[int(0.95 * (len(ordered) - 1))], 4),
        "max_abs_margin_delta_pp": round(ordered[-1], 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-root", default="data/district_contests_county_constrained_staging")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    staging = ROOT / args.staging_root
    records = []
    by_file = []
    for source in sorted((ROOT / "data/2022 state house files").glob("district-statistics*.csv")):
        year, label = source.stem.removeprefix("district-statistics ").split(" ", 1)
        contest = CONTESTS.get(label)
        if not contest:
            continue
        filename = f"state_house_{contest}_{year}_2022_lines.json"
        relative = Path("state_house_2022_lines") / filename
        served = json.loads((ROOT / "data/district_contests" / relative).read_text(encoding="utf-8"))["general"]["results"]
        staged = json.loads((staging / relative).read_text(encoding="utf-8"))["general"]["results"]
        file_rows = []
        with source.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                district = row["ID"].strip()
                if not district.isdigit():
                    continue
                target = (float(row["Rep"]) - float(row["Dem"])) * 100
                old = float(served[district]["margin_pct"])
                new = float(staged[district]["margin_pct"])
                file_rows.append({
                    "file": filename, "district": district,
                    "dra_margin_pp": round(target, 4),
                    "served_margin_pp": old, "staged_margin_pp": new,
                    "served_abs_delta_pp": round(abs(old - target), 4),
                    "staged_abs_delta_pp": round(abs(new - target), 4),
                })
        records.extend(file_rows)
        old_deltas = [r["served_abs_delta_pp"] for r in file_rows]
        new_deltas = [r["staged_abs_delta_pp"] for r in file_rows]
        by_file.append({
            "benchmark": source.relative_to(ROOT).as_posix(), "districts": len(file_rows),
            "served": comparison(old_deltas), "staged": comparison(new_deltas),
            "staged_closer": sum(new < old for old, new in zip(old_deltas, new_deltas)),
            "served_closer": sum(old < new for old, new in zip(old_deltas, new_deltas)),
            "staged_winner_matches": sum((r["staged_margin_pp"] > 0) == (r["dra_margin_pp"] > 0) for r in file_rows),
            "served_winner_matches": sum((r["served_margin_pp"] > 0) == (r["dra_margin_pp"] > 0) for r in file_rows),
        })
    report = {
        "method": "Compare signed Republican-minus-Democratic margin percentage points with local DRA district-statistics CSVs; DRA estimates are diagnostic, not official returns.",
        "summary": {
            "benchmarks": len(by_file), "district_rows": len(records),
            "served": comparison([r["served_abs_delta_pp"] for r in records]),
            "staged": comparison([r["staged_abs_delta_pp"] for r in records]),
            "staged_closer": sum(r["staged_abs_delta_pp"] < r["served_abs_delta_pp"] for r in records),
            "served_closer": sum(r["served_abs_delta_pp"] < r["staged_abs_delta_pp"] for r in records),
        },
        "files": by_file,
        "largest_staged_errors": sorted(records, key=lambda r: r["staged_abs_delta_pp"], reverse=True)[:30],
        "largest_improvements": sorted(records, key=lambda r: r["served_abs_delta_pp"] - r["staged_abs_delta_pp"], reverse=True)[:30],
    }
    if args.write:
        (staging / "dra_benchmark_audit.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
