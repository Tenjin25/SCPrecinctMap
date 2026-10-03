#!/usr/bin/env python3
"""Remove presidential and gubernatorial running mates from district JSON labels."""

from __future__ import annotations

import json
from pathlib import Path

from apply_rdh_block_equivalency_results import ticket_lead


ROOT = Path(__file__).resolve().parents[1]
DISTRICTS = ROOT / "data/district_contests"


def main() -> int:
    files = rows = 0
    for path in sorted(DISTRICTS.rglob("*.json")):
        if "_president_" not in path.name and "_governor_" not in path.name:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        results = (payload.get("general") or {}).get("results") or {}
        changed = 0
        for result in results.values():
            for field in ("dem_candidate", "rep_candidate"):
                old = str(result.get(field) or "")
                new = ticket_lead(old)
                if new != old:
                    result[field] = new
                    changed += 1
        if changed:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
            files += 1
            rows += changed
    print(f"stripped running-mate labels from {rows} candidate fields in {files} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
