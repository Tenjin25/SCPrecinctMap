#!/usr/bin/env python3
"""Stage two 2022 source-name corrections without regenerating other crosswalks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from aggregate_contests_to_vtd20_crosswalks import VOTE_FIELDS, finalize_row


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/contests"
CURRENT = ROOT / "data/contests_2025_crosswalked"
OVERRIDES = ROOT / "data/crosswalk/reviewed_2022_house_ballot_source_overrides.json"
OLD_SPLITS = ROOT / "data/crosswalk/vtd20_to_2025_vote_weight_splits.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="data/district_contests_county_constrained_staging/source_overrides_2022")
    args = parser.parse_args()
    output = ROOT / args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    overrides = {key: value for key, value in json.loads(OVERRIDES.read_text(encoding="utf-8")).items()
                 if not key.startswith("_")}
    old_splits = json.loads(OLD_SPLITS.read_text(encoding="utf-8"))
    aliases = json.loads((ROOT / "precinct_aliases.json").read_text(encoding="utf-8"))
    summary = {}
    for source_path in sorted(SOURCE.glob("*_2022.json")):
        current_path = CURRENT / source_path.name
        if not current_path.exists():
            continue
        original = json.loads(source_path.read_text(encoding="utf-8"))
        staged = json.loads(current_path.read_text(encoding="utf-8"))
        before_precinct_totals = {field: sum(int(row.get(field) or 0) for row in staged["rows"]
                                             if " - " in row["county"]) for field in VOTE_FIELDS}
        source_rows = {row["county"]: row for row in original["rows"]}
        target_rows = {row["county"].upper(): row for row in staged["rows"]}
        changed = {}
        for source, destinations in overrides.items():
            source_row = source_rows[source]
            prior = old_splits.get(source) or {aliases[source]: 1.0}
            if len(prior) != 1 or next(iter(prior.values())) != 1.0 or len(destinations) != 1:
                raise ValueError(f"Expected one-to-one source correction: {source}")
            old_key = next(iter(prior)).upper()
            new_key = next(iter(destinations)).upper()
            if old_key == new_key:
                continue
            for key, sign in ((old_key, -1), (new_key, 1)):
                target = target_rows[key]
                for field in VOTE_FIELDS:
                    target[field] = int(target.get(field) or 0) + sign * int(source_row.get(field) or 0)
                    if target[field] < 0:
                        raise ValueError(f"Negative {field} after correcting {source} in {source_path.name}")
                finalize_row(target)
            changed[source] = {"from": old_key, "to": new_key, "source_votes": source_row["total_votes"]}
        after_precinct_totals = {field: sum(int(row.get(field) or 0) for row in staged["rows"]
                                            if " - " in row["county"]) for field in VOTE_FIELDS}
        if before_precinct_totals != after_precinct_totals:
            raise ValueError(f"Precinct totals changed for {source_path.name}")
        (output / source_path.name).write_text(json.dumps(staged, separators=(",", ":")) + "\n", encoding="utf-8")
        summary[source_path.name] = changed
    (output / "qa.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"contest_files": len(summary), "corrected_source_rows": sum(map(len, summary.values()))}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
