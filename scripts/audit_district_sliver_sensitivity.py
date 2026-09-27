#!/usr/bin/env python3
"""Compare live district results with a populated-sliver trial rebuild."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("dem_votes", "rep_votes", "other_votes")
TOTAL_FIELDS = (*FIELDS, "total_votes")
MANIFESTS = ("manifest.json", "state_house_2022_lines/manifest_2022_lines.json",
             "state_house_2024_lines/manifest_2024_lines.json")


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial-root", default="work/sliver_trial")
    parser.add_argument("--output", default="data/district_contests_county_constrained_staging/sliver_sensitivity_2010_2024.json")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    live_root = ROOT / "data/district_contests"
    trial_root = ROOT / args.trial_root
    records = []
    for manifest_rel in MANIFESTS:
        for entry in read(live_root / manifest_rel)["files"]:
            year = int(entry["year"])
            if not 2010 <= year <= 2024 or entry["contest_type"] in ("us_house", "state_house", "state_senate"):
                continue
            relative = Path(manifest_rel).parent / entry["file"]
            live = read(live_root / relative)["general"]["results"]
            trial = read(trial_root / relative)["general"]["results"]
            if set(live) != set(trial):
                raise ValueError(f"District coverage changed: {relative}")
            for field in TOTAL_FIELDS:
                before_total = sum(int(row[field]) for row in live.values())
                after_total = sum(int(row[field]) for row in trial.values())
                if before_total != after_total:
                    raise ValueError(f"Statewide {field} changed: {relative}: {before_total} -> {after_total}")
            for district, before in live.items():
                after = trial[district]
                deltas = {field: int(after[field]) - int(before[field]) for field in FIELDS}
                if any(deltas.values()):
                    records.append({"file": relative.as_posix(), "district": district,
                                    "vote_deltas": deltas,
                                    "margin_before_pp": before["margin_pct"],
                                    "margin_trial_pp": after["margin_pct"],
                                    "margin_change_pp": round(after["margin_pct"] - before["margin_pct"], 4),
                                    "winner_before": before["winner"], "winner_trial": after["winner"]})
    ranked = sorted(records, key=lambda row: abs(row["margin_change_pp"]), reverse=True)
    by_scope = defaultdict(list)
    for row in records:
        relative = Path(row["file"])
        scope = relative.parent.name if relative.parent != Path(".") else relative.name.split("_", 1)[0]
        if scope == "state":
            scope = "state_house" if relative.name.startswith("state_house_") else "state_senate"
        by_scope[scope].append(row)
    scope_summary = {
        scope: {"changed_files": len({row["file"] for row in rows}),
                "changed_district_rows": len(rows),
                "winner_changes": sum(row["winner_before"] != row["winner_trial"] for row in rows),
                "max_abs_margin_change_pp": max(abs(row["margin_change_pp"]) for row in rows),
                "max_abs_party_vote_change": max(abs(value) for row in rows
                                                 for value in row["vote_deltas"].values())}
        for scope, rows in sorted(by_scope.items())
    }
    report = {"method": "All positive block-CVAP district intersections are retained in the trial, including pieces at or below the 0.1% area cutoff. Whole-county decisions and official county-party targets remain unchanged. Statewide party and total votes are checked in every file.",
              "summary": {"changed_district_rows": len(records),
                          "changed_files": len(set(row["file"] for row in records)),
                          "winner_changes": sum(row["winner_before"] != row["winner_trial"] for row in records),
                          "max_abs_margin_change_pp": max((abs(row["margin_change_pp"]) for row in records), default=0),
                          "max_abs_party_vote_change": max((abs(value) for row in records
                                                            for value in row["vote_deltas"].values()), default=0)},
              "by_scope": scope_summary,
              "largest_changes": ranked[:50],
              "winner_changes": [row for row in records if row["winner_before"] != row["winner_trial"]]}
    if args.write:
        path = ROOT / args.output
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    for row in report["winner_changes"]:
        print(f"{row['file']} district {row['district']}: {row['winner_before']} -> {row['winner_trial']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
