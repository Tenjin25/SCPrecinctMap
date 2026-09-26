#!/usr/bin/env python3
"""Rank remaining 2010–2024 source precincts using county fallback."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from aggregate_contests_to_vtd20_crosswalks import norm


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-root", default="data/district_contests_county_constrained_staging")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    staging = ROOT / args.staging_root
    geojson = json.loads((ROOT / "data/Voting_Precincts.geojson").read_text(encoding="utf-8"))
    aliases = set()
    for feature in geojson["features"]:
        props = feature["properties"]
        county = str(props.get("county_nam") or "").strip()
        for value in (props.get("precinct_norm"), props.get("precinct_display_name"),
                      f"{county} - {str(props.get('precinct_full_name') or '').strip()}",
                      f"{county} - {str(props.get('prec_id') or '').strip()}"):
            if norm(value):
                aliases.add(norm(value))
    qa = json.loads((staging / "current_geojson_qa.json").read_text(encoding="utf-8"))
    aliases.update(qa["punctuation_aliases_2010_2024"])
    bridge = json.loads((ROOT / "data/crosswalk/reviewed_vtd_name_bridge_2010_2024.json").read_text(encoding="utf-8"))
    exact = json.loads((staging / "mixed_county_components.json").read_text(encoding="utf-8"))["plans"]
    whole_by_scope = {scope: set(node["whole_county_destinations"]) for scope, node in exact.items()}
    manifest = json.loads((ROOT / "data/contests_2025_crosswalked/manifest.json").read_text(encoding="utf-8"))
    by_name = defaultdict(lambda: {"votes_across_contests": 0, "contest_rows": 0,
                                   "contests": [], "split_county_scopes": set()})
    for entry in manifest["files"]:
        year = int(entry["year"])
        if not 2010 <= year <= 2024:
            continue
        rows = json.loads((ROOT / "data/contests_2025_crosswalked" / entry["file"]).read_text(encoding="utf-8"))["rows"]
        for row in rows:
            source = norm(row.get("county"))
            if (" - " not in source or source in aliases
                    or year in bridge.get("allowed_years", {}).get(source, [])):
                continue
            county = source.split(" - ", 1)[0]
            split_scopes = {scope for scope, whole in whole_by_scope.items() if county not in whole}
            if not split_scopes:
                continue
            node = by_name[(year, source)]
            node["votes_across_contests"] += int(row.get("total_votes") or 0)
            node["contest_rows"] += 1
            node["contests"].append(entry["file"])
            node["split_county_scopes"].update(split_scopes)
    entries = [{"year": year, "source_precinct": source,
                "county": source.split(" - ", 1)[0],
                "has_emergency_label": "EMERGENCY" in source,
                **{key: sorted(value) if isinstance(value, set) else value for key, value in node.items()}}
               for (year, source), node in by_name.items()]
    entries.sort(key=lambda row: (-row["votes_across_contests"], row["year"], row["source_precinct"]))
    report = {"method": "Unmatched source names in counties split by at least one staged district plan; vote counts sum contest rows and must not be interpreted as unique voters.",
              "summary": {"year_name_pairs": len(entries),
                          "contest_rows": sum(row["contest_rows"] for row in entries),
                          "votes_across_contests": sum(row["votes_across_contests"] for row in entries),
                          "emergency_year_name_pairs": sum(row["has_emergency_label"] for row in entries),
                          "emergency_votes_across_contests": sum(row["votes_across_contests"] for row in entries
                                                                 if row["has_emergency_label"]),
                          "other_year_name_pairs": sum(not row["has_emergency_label"] for row in entries),
                          "other_votes_across_contests": sum(row["votes_across_contests"] for row in entries
                                                             if not row["has_emergency_label"])},
              "entries": entries}
    if args.write:
        (staging / "fallback_review_2010_2024.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    for row in entries[:10]:
        print(f"{row['year']} {row['source_precinct']}: {row['votes_across_contests']} votes in {row['contest_rows']} contests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
