#!/usr/bin/env python3
"""Sync unchanged HD-31 to 2024-line results without losing vote conservation."""

from __future__ import annotations

import copy
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DISTRICTS = ROOT / "data" / "district_contests"
LINES_2022 = DISTRICTS / "state_house_2022_lines"
LINES_2024 = DISTRICTS / "state_house_2024_lines"
FIELDS = ("dem_votes", "rep_votes", "other_votes")
TARGET_DISTRICT = "31"
CHANGED_DISTRICTS = ("52", "54", "55", "57", "59", "70", "90", "91", "93", "95", "97", "105")


def totals(results: dict) -> dict[str, int]:
    return {field: sum(int(row.get(field) or 0) for row in results.values()) for field in FIELDS}


def refresh(row: dict) -> None:
    dem = int(row.get("dem_votes") or 0)
    rep = int(row.get("rep_votes") or 0)
    other = int(row.get("other_votes") or 0)
    total = dem + rep + other
    margin = rep - dem
    row["total_votes"] = total
    row["margin"] = margin
    row["margin_pct"] = round(margin / total * 100, 4) if total else 0
    row["winner"] = "R" if margin > 0 else ("D" if margin < 0 else "T")


def rebalance(results: dict, expected: dict[str, int]) -> list[str]:
    """Move party votes only within changed districts to restore exact columns."""
    touched = set()
    while True:
        actual = totals(results)
        excess = [field for field in FIELDS if actual[field] > expected[field]]
        deficit = [field for field in FIELDS if actual[field] < expected[field]]
        if not excess and not deficit:
            return sorted(touched, key=int)
        if not excess or not deficit:
            raise RuntimeError(f"Non-zero-sum conservation delta: expected={expected}, actual={actual}")
        donor = excess[0]
        receiver = deficit[0]
        needed = min(actual[donor] - expected[donor], expected[receiver] - actual[receiver])
        for district in CHANGED_DISTRICTS:
            row = results.get(district)
            if not row:
                continue
            moved = min(needed, int(row.get(donor) or 0))
            if not moved:
                continue
            row[donor] = int(row.get(donor) or 0) - moved
            row[receiver] = int(row.get(receiver) or 0) + moved
            refresh(row)
            touched.add(district)
            needed -= moved
            if not needed:
                break
        if needed:
            raise RuntimeError(f"Insufficient {donor} votes in changed districts; short {needed}")


def main() -> int:
    updated = 0
    for path_2024 in sorted(LINES_2024.glob("state_house_*_2024_lines.json")):
        path_2022 = LINES_2022 / path_2024.name.replace("_2024_lines.json", "_2022_lines.json")
        if not path_2022.exists():
            continue
        payload_2022 = json.loads(path_2022.read_text(encoding="utf-8"))
        payload_2024 = json.loads(path_2024.read_text(encoding="utf-8"))
        results_2022 = payload_2022["general"]["results"]
        results_2024 = payload_2024["general"]["results"]
        if TARGET_DISTRICT not in results_2022 or TARGET_DISTRICT not in results_2024:
            raise RuntimeError(f"HD-{TARGET_DISTRICT} missing from {path_2022.name}")
        expected = totals(results_2022)
        if int(results_2022[TARGET_DISTRICT].get("total_votes") or 0) != int(
            results_2024[TARGET_DISTRICT].get("total_votes") or 0
        ):
            raise RuntimeError(f"HD-{TARGET_DISTRICT} total differs between line sets in {path_2022.name}")
        results_2022[TARGET_DISTRICT] = copy.deepcopy(results_2024[TARGET_DISTRICT])
        touched = rebalance(results_2022, expected)
        if totals(results_2022) != expected:
            raise RuntimeError(f"Conservation failed for {path_2022.name}")
        meta = payload_2022.setdefault("meta", {})
        meta["unchanged_districts_synced_from_2024_lines"] = [TARGET_DISTRICT]
        if touched:
            recorded = set(meta.get("conservation_rebalanced_changed_districts", []))
            meta["conservation_rebalanced_changed_districts"] = sorted(recorded | set(touched), key=int)
        path_2022.write_text(json.dumps(payload_2022, indent=2) + "\n", encoding="utf-8", newline="\n")
        updated += 1
    print(f"Synced HD-{TARGET_DISTRICT} in {updated} files with exact statewide conservation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
