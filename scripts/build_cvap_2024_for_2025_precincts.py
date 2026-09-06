#!/usr/bin/env python3
"""Crosswalk 2020-VTD CVAP estimates onto the current 2025 precinct layer.

The input CVAP estimates are already assigned to 2020 VTDs.  The existing
VTD20-to-2025 crosswalk describes each source VTD as shares of current
precincts.  Counts are allocated with largest remainders so each source and
statewide field total is preserved exactly.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import defaultdict
from pathlib import Path


CVAP_FIELDS = (
    "CVAP_TOT24",
    "CVAP_HSP24",
    "CVAP_NHS24",
    "CVAP_WHT24",
    "CVAP_BLA24",
    "CVAP_ASI24",
    "CVAP_AMI24",
    "CVAP_NHP24",
    "CVAP_2OM24",
)


def norm(value: object) -> str:
    return " ".join(str(value or "").strip().upper().split())


def match_key(value: object) -> str:
    """Loose key used only to reconcile punctuation in two VTD20 name sources."""
    return re.sub(r"[^A-Z0-9]", "", norm(value))


def allocate_integer(total: int, targets: list[tuple[str, float]]) -> dict[str, int]:
    positive = [(key, max(0.0, float(weight))) for key, weight in targets]
    weight_sum = sum(weight for _, weight in positive)
    if not positive or weight_sum <= 0:
        return {}
    raw = [(key, total * weight / weight_sum) for key, weight in positive]
    out = {key: math.floor(value) for key, value in raw}
    remainder = total - sum(out.values())
    for key, _ in sorted(raw, key=lambda item: (-(item[1] - math.floor(item[1])), item[0]))[:remainder]:
        out[key] += 1
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cvap", default="data/sc_cvap_2024_by_precinct_norm.json")
    parser.add_argument("--crosswalk", default="data/crosswalk/vtd20_to_2025_vote_weight_splits.json")
    parser.add_argument("--precincts", default="data/Voting_Precincts.geojson")
    parser.add_argument("--district-crosswalk", default="data/crosswalk/current_precinct_to_district_weights.json")
    parser.add_argument("--output", default="data/sc_cvap_2024_by_2025_precinct.csv")
    parser.add_argument("--aggregate-dir", default="data/cvap_aggregates")
    parser.add_argument("--qa", default="data/sc_cvap_2024_by_2025_precinct_qa.json")
    args = parser.parse_args()

    with Path(args.cvap).open(encoding="utf-8") as fh:
        cvap_doc = json.load(fh)
    with Path(args.crosswalk).open(encoding="utf-8") as fh:
        crosswalk_doc = json.load(fh)
    with Path(args.precincts).open(encoding="utf-8") as fh:
        precinct_doc = json.load(fh)
    with Path(args.district_crosswalk).open(encoding="utf-8") as fh:
        district_crosswalk_doc = json.load(fh)

    source_rows = {norm(key): row for key, row in cvap_doc["by_precinct_norm"].items()}
    crosswalk = {
        norm(key): {norm(target): float(weight) for target, weight in targets.items()}
        for key, targets in crosswalk_doc.items()
        if not key.startswith("_") and isinstance(targets, dict)
    }
    crosswalk_by_loose_key: dict[str, list[str]] = defaultdict(list)
    for source_key in crosswalk:
        crosswalk_by_loose_key[match_key(source_key)].append(source_key)
    current_precincts = {
        norm(feature.get("properties", {}).get("precinct_norm"))
        for feature in precinct_doc.get("features", [])
    }
    current_precincts.discard("")

    totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    missing_crosswalk: list[str] = []
    for source_key, source_row in source_rows.items():
        matched_crosswalk_key = source_key
        target_weights = crosswalk.get(source_key)
        if not target_weights:
            candidates = crosswalk_by_loose_key.get(match_key(source_key), [])
            if len(candidates) == 1:
                matched_crosswalk_key = candidates[0]
                target_weights = crosswalk[matched_crosswalk_key]
        if not target_weights:
            missing_crosswalk.append(source_key)
            continue
        targets = [(target, weight) for target, weight in target_weights.items() if target in current_precincts]
        for field in CVAP_FIELDS:
            allocated = allocate_integer(int(round(float(source_row.get(field, 0) or 0))), targets)
            for target, value in allocated.items():
                totals[target][field] += value

    fieldnames = [
        "precinct_id", "cvap_18plus", "hispanic_cvap", "non_hispanic_cvap",
        "white_cvap", "black_cvap", "asian_cvap", "native_cvap",
        "pacific_cvap", "multiracial_cvap", "white_cvap_pct",
        "black_cvap_pct", "hispanic_cvap_pct", "asian_cvap_pct",
        "native_cvap_pct", "pacific_cvap_pct", "multiracial_cvap_pct",
    ]
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for precinct_id in sorted(current_precincts):
            row = totals.get(precinct_id, {})
            total = int(row.get("CVAP_TOT24", 0))
            def pct(field: str) -> str:
                return f"{(100 * int(row.get(field, 0)) / total):.2f}" if total else ""
            writer.writerow({
                "precinct_id": precinct_id,
                "cvap_18plus": total,
                "hispanic_cvap": int(row.get("CVAP_HSP24", 0)),
                "non_hispanic_cvap": int(row.get("CVAP_NHS24", 0)),
                "white_cvap": int(row.get("CVAP_WHT24", 0)),
                "black_cvap": int(row.get("CVAP_BLA24", 0)),
                "asian_cvap": int(row.get("CVAP_ASI24", 0)),
                "native_cvap": int(row.get("CVAP_AMI24", 0)),
                "pacific_cvap": int(row.get("CVAP_NHP24", 0)),
                "multiracial_cvap": int(row.get("CVAP_2OM24", 0)),
                "white_cvap_pct": pct("CVAP_WHT24"),
                "black_cvap_pct": pct("CVAP_BLA24"),
                "hispanic_cvap_pct": pct("CVAP_HSP24"),
                "asian_cvap_pct": pct("CVAP_ASI24"),
                "native_cvap_pct": pct("CVAP_AMI24"),
                "pacific_cvap_pct": pct("CVAP_NHP24"),
                "multiracial_cvap_pct": pct("CVAP_2OM24"),
            })

    aggregate_dir = Path(args.aggregate_dir)
    aggregate_dir.mkdir(parents=True, exist_ok=True)

    county_totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for precinct_id, row in totals.items():
        county = precinct_id.split(" - ", 1)[0]
        for field in CVAP_FIELDS:
            county_totals[county][field] += int(row.get(field, 0))
    with (aggregate_dir / "county_2025.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["county", *CVAP_FIELDS])
        writer.writeheader()
        for county in sorted(county_totals):
            writer.writerow({"county": county, **county_totals[county]})

    aggregate_files = {
        "congressional": "congressional_2022.csv",
        "state_house_2022": "state_house_2022.csv",
        "state_house_2024": "state_house_2024.csv",
        "state_senate_2022": "state_senate_2022.csv",
    }
    for scope, filename in aggregate_files.items():
        scope_weights = district_crosswalk_doc.get("scopes", {}).get(scope, {}).get("weights", {})
        district_totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for precinct_id, row in totals.items():
            weights = scope_weights.get(precinct_id, {})
            targets = [(str(district), float(weight)) for district, weight in weights.items()]
            for field in CVAP_FIELDS:
                for district, value in allocate_integer(int(row.get(field, 0)), targets).items():
                    district_totals[district][field] += value
        with (aggregate_dir / filename).open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["district", *CVAP_FIELDS])
            writer.writeheader()
            for district in sorted(district_totals, key=lambda value: int(value)):
                writer.writerow({"district": district, **district_totals[district]})

    source_sums = {field: sum(int(round(float(row.get(field, 0) or 0))) for row in source_rows.values()) for field in CVAP_FIELDS}
    output_sums = {field: sum(row.get(field, 0) for row in totals.values()) for field in CVAP_FIELDS}
    populated = {key for key, row in totals.items() if row.get("CVAP_TOT24", 0) > 0}
    qa = {
        "source_cvap": args.cvap,
        "crosswalk": args.crosswalk,
        "precinct_geometry": args.precincts,
        "source_precinct_count": len(source_rows),
        "current_precinct_count": len(current_precincts),
        "output_precinct_count": len(current_precincts),
        "populated_precinct_count": len(populated),
        "zero_total_precincts": sorted(current_precincts - populated),
        "missing_source_crosswalks": sorted(missing_crosswalk),
        "source_field_totals": source_sums,
        "output_field_totals": output_sums,
        "field_total_differences": {field: output_sums[field] - source_sums[field] for field in CVAP_FIELDS},
    }
    with Path(args.qa).open("w", encoding="utf-8") as fh:
        json.dump(qa, fh, indent=2)
        fh.write("\n")
    print(json.dumps(qa, indent=2))


if __name__ == "__main__":
    main()
