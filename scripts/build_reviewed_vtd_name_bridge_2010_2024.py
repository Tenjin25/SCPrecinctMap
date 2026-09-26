#!/usr/bin/env python3
"""Build reviewed 2010–2024 election-name to VTD10 geographic weights.

Only explicitly reviewed county-qualified name pairs are included. Each pair
is applied only in the years listed below.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from aggregate_contests_to_vtd20_crosswalks import norm


ROOT = Path(__file__).resolve().parents[1]
REVIEWED = {
    "RICHLAND - OAK POINTE": {"vtd10": "RICHLAND - OAK POINT", "years": [2010]},
    "KERSHAW - RABONS XROADS": {"vtd10": "KERSHAW - RABONS CROSSROADS", "years": list(range(2010, 2025))},
    "MARLBORO - E BENNETTSVILLE": {"vtd10": "MARLBORO - EAST BENNETTSVILLE", "years": list(range(2010, 2025))},
    "MARLBORO - QUICKS XROADS": {"vtd10": "MARLBORO - QUICKS X ROADS", "years": list(range(2010, 2025))},
    "SPARTANBURG - PARK HILLS ELEMENTARY SCHOOL": {
        "vtd10": "SPARTANBURG - PARK HILLS ELEMENTARY", "years": list(range(2010, 2025))
    },
    "UNION - MONARCH": {
        "vtd10": "UNION - MONARCH BOX 1", "vtd10_alternates": ["UNION - MONARCH BOX 2"], "years": [2022]
    },
    "SPARTANBURG - WOODRUFF ARMORY DRIVE": {
        "vtd10": "SPARTANBURG - WOODRUFF ARMORY DRIVE FIRE STATIONS", "years": [2012]
    },
    "AIKEN - BREEZY HILL 50": {
        "vtd10": "AIKEN - BREEZY HILL", "years": [2012], "min_overlay_coverage": 0.99
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data/crosswalk/reviewed_vtd_name_bridge_2010_2024.json")
    args = parser.parse_args()
    vtd = {norm(key): shares for key, shares in json.loads((
        ROOT / "data/crosswalk/vtd10_to_2025_vote_weight_splits.json"
    ).read_text(encoding="utf-8")).items()}
    manifest = json.loads((ROOT / "data/contests_2025_crosswalked/manifest.json").read_text(encoding="utf-8"))
    contests = [entry for entry in manifest["files"] if 2010 <= int(entry["year"]) <= 2024]
    current = {norm(feature["properties"].get("precinct_norm")) for feature in json.loads((
        ROOT / "data/Voting_Precincts.geojson"
    ).read_text(encoding="utf-8"))["features"]}
    overlay = {}
    with (ROOT / "data/crosswalk/vtd10_to_2025_areal_top8.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            overlay.setdefault(norm(row["source_key_norm"]), []).append(row)
    weights = {}
    allowed_years = {}
    evidence = []
    for source, config in REVIEWED.items():
        target = config["vtd10"]
        if source in current:
            raise ValueError(f"Historical name already has current geometry: {source}")
        shares = vtd[target]
        clean = {norm(key): float(value) for key, value in shares.items()}
        if not clean or any(key not in current or key.split(" - ", 1)[0] != source.split(" - ", 1)[0]
                            for key in clean) or abs(sum(clean.values()) - 1) > 1e-5:
            raise ValueError(f"Invalid VTD10-to-current weights for {target}")
        overlaps = overlay[target]
        if (sum(float(row["share_of_source"]) for row in overlaps) < config.get("min_overlay_coverage", 0.998)
                or {norm(row["target_key_norm"]) for row in overlaps} != set(clean)):
            raise ValueError(f"Incomplete or mismatched VTD10 overlay for {target}")
        for alternate in config.get("vtd10_alternates", []):
            alternate_weights = {norm(key): float(value) for key, value in vtd[alternate].items()}
            alternate_overlaps = overlay[alternate]
            if (alternate_weights != clean or
                    sum(float(row["share_of_source"]) for row in alternate_overlaps) < 0.998 or
                    {norm(row["target_key_norm"]) for row in alternate_overlaps} != set(clean)):
                raise ValueError(f"Conflicting Monarch VTD10 destination: {alternate}")
        found = []
        for entry in contests:
            if int(entry["year"]) not in config["years"]:
                continue
            rows = json.loads((ROOT / "data/contests_2025_crosswalked" / entry["file"]).read_text(encoding="utf-8"))["rows"]
            for row in rows:
                if norm(row.get("county")) == source:
                    found.append({"year": int(entry["year"]), "contest": entry["file"],
                                  "votes": int(row.get("total_votes") or 0)})
        if not found:
            raise ValueError(f"No 2010 election rows for {source}")
        weights[source] = clean
        allowed_years[source] = sorted({row["year"] for row in found})
        evidence.append({"source": source, "vtd10_name": target,
                         "vtd10_alternates": config.get("vtd10_alternates", []),
                         "vtd10_geoid": overlaps[0]["source_id"],
                         "vtd10_overlay_share": round(sum(float(row["share_of_source"]) for row in overlaps), 6),
                         "vtd10_to_current_file": "data/crosswalk/vtd10_to_2025_vote_weight_splits.json",
                         "vtd10_overlay_file": "data/crosswalk/vtd10_to_2025_areal_top8.csv",
                         "review_note": ("Both named Monarch VTD10 polygons map entirely to the same current precinct."
                                         if config.get("vtd10_alternates") else
                                         "Reviewed county-qualified abbreviation or spelling variant; VTD10 overlay gives the current precinct footprint."),
                         "election_rows": found})
    output = {"schema": "reviewed_2010_2024_vtd_name_bridge.v1", "weights": weights,
              "allowed_years": allowed_years, "evidence": evidence}
    path = ROOT / args.out
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"mappings": len(weights), "election_rows": sum(len(x["election_rows"]) for x in evidence),
                      "votes": sum(row["votes"] for x in evidence for row in x["election_rows"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
