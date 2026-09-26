#!/usr/bin/env python3
"""Stage conservative 2008 result-name to VTD10-to-current precinct weights.

A name is bridged only when its normalized locality is unique in its county,
and one 2008 VTD accounts for at least 90% of the named VTD10 area's overlap.
The 2008 VTD is geographic corroboration; the named VTD10's current-precinct
weights determine the allocation. Ambiguous names remain on county fallback.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from aggregate_contests_to_vtd20_crosswalks import norm


ROOT = Path(__file__).resolve().parents[1]
VENUE_WORDS = {"ELEMENTARY", "MIDDLE", "SCHOOL", "FIRE", "STATION", "BAPTIST", "METHODIST",
               "CHURCH", "CENTER", "RECREATION", "HIGH", "SENIOR", "TOWN", "HALL", "COMMUNITY",
               "UNITED", "FIRST", "MEMORIAL", "UMC", "JR"}


def locality(name: str) -> str:
    tokens = re.findall(r"[A-Z0-9]+", name.upper())
    return " ".join(token for token in tokens if token not in VENUE_WORDS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data/crosswalk/legacy_2008_vtd_name_bridge_weights.json")
    parser.add_argument("--review-csv", default="data/crosswalk/legacy_2008_vtd_name_bridge_review.csv")
    args = parser.parse_args()
    rows = json.loads((ROOT / "data/contests_2025_crosswalked/president_2008.json").read_text(encoding="utf-8"))["rows"]
    features = json.loads((ROOT / "data/Voting_Precincts.geojson").read_text(encoding="utf-8"))["features"]
    current_keys = {norm(feature["properties"].get("precinct_norm")) for feature in features}
    vtd_weights = {norm(key): value for key, value in json.loads(
        (ROOT / "data/crosswalk/vtd10_to_2025_vote_weight_splits.json").read_text(encoding="utf-8")
    ).items()}

    by_target: dict[str, list[dict]] = defaultdict(list)
    target_names: dict[tuple[str, str], set[str]] = defaultdict(set)
    with (ROOT / "data/crosswalk/vtd00_2008_to_vtd10_areal_top8.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            key = norm(row["target_key_display"])
            by_target[key].append(row)
            target_names[(norm(row["target_county_name"]), locality(row["target_precinct"]))].add(key)

    old_names: dict[tuple[str, str], set[str]] = defaultdict(set)
    unmatched = []
    for row in rows:
        key = norm(row.get("county"))
        if " - " not in key or key in current_keys:
            continue
        county, name = key.split(" - ", 1)
        old_names[(county, locality(name))].add(key)
        unmatched.append(row)

    weights = {}
    evidence = []
    review = []
    rejects = Counter()
    for row in unmatched:
        old_key = norm(row["county"])
        county, name = old_key.split(" - ", 1)
        stem = locality(name)
        candidates = target_names.get((county, stem), set())
        if len(old_names[(county, stem)]) != 1 or len(candidates) != 1 or len(stem) < 5:
            rejects["ambiguous_or_no_unique_name"] += 1
            suggestions = sorted(
                ((difflib.SequenceMatcher(None, stem, target_stem).ratio(), target)
                 for (target_county, target_stem), keys in target_names.items()
                 if target_county == county for target in keys), reverse=True
            )[:3]
            review.append({"source_precinct": old_key, "source_vote_count": int(row.get("total_votes") or 0),
                           "reason": "ambiguous_or_no_unique_name",
                           "candidate_vtd10_names": [{"name": target, "name_similarity": round(score, 4)}
                                                     for score, target in suggestions]})
            continue
        target = next(iter(candidates))
        overlaps = by_target[target]
        total_area = sum(float(item["overlap_area_m2"]) for item in overlaps)
        top = max(overlaps, key=lambda item: float(item["overlap_area_m2"]))
        dominant = float(top["overlap_area_m2"]) / total_area if total_area else 0
        if dominant < 0.9:
            rejects["weak_2008_vtd_overlap"] += 1
            review.append({"source_precinct": old_key, "source_vote_count": int(row.get("total_votes") or 0),
                           "reason": "weak_2008_vtd_overlap", "candidate_vtd10_names": [{
                               "name": target, "name_similarity": 1.0,
                               "dominant_2008_vtd_overlap_share": round(dominant, 6)}]})
            continue
        raw = vtd_weights.get(target)
        if not isinstance(raw, dict) or not raw:
            rejects["missing_vtd10_current_weights"] += 1
            review.append({"source_precinct": old_key, "source_vote_count": int(row.get("total_votes") or 0),
                           "reason": "missing_vtd10_current_weights", "candidate_vtd10_names": [{"name": target}]})
            continue
        resolved = {norm(k): float(v) for k, v in raw.items() if norm(k) in current_keys and float(v) > 0}
        total = sum(resolved.values())
        if total < 0.999:
            rejects["unresolved_current_target"] += 1
            review.append({"source_precinct": old_key, "source_vote_count": int(row.get("total_votes") or 0),
                           "reason": "unresolved_current_target", "candidate_vtd10_names": [{"name": target}]})
            continue
        weights[old_key] = {key: value / total for key, value in resolved.items()}
        evidence.append({"source_precinct": old_key, "vtd10_name": target,
                         "dominant_2008_vtd": top["source_key_display"],
                         "dominant_2008_vtd_overlap_share": round(dominant, 6),
                         "source_vote_count": int(row.get("total_votes") or 0)})

    payload = {"schema": "legacy_2008_vtd_name_bridge.v1",
               "method": "Unique locality-name match to VTD10, corroborated by >=90% dominant 2008 VTD overlap; VTD10-to-current weights allocate votes.",
               "weights": dict(sorted(weights.items())), "evidence": sorted(evidence, key=lambda item: item["source_precinct"]),
               "review_only": sorted(review, key=lambda item: (-item["source_vote_count"], item["source_precinct"])),
               "qa": {"bridged_rows": len(weights), "bridged_votes": sum(item["source_vote_count"] for item in evidence),
                      "unmatched_rows_examined": len(unmatched), "review_only_rows": len(review),
                      "review_only_votes": sum(item["source_vote_count"] for item in review),
                      "rejections": dict(rejects)}}
    path = ROOT / args.out
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    review_path = ROOT / args.review_csv
    review_path.parent.mkdir(parents=True, exist_ok=True)
    with review_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["source_precinct", "source_vote_count", "reason",
                                                    "candidate_vtd10_name", "name_similarity",
                                                    "dominant_2008_vtd_overlap_share"])
        writer.writeheader()
        for item in payload["review_only"]:
            top = (item.get("candidate_vtd10_names") or [{}])[0]
            writer.writerow({"source_precinct": item["source_precinct"],
                             "source_vote_count": item["source_vote_count"], "reason": item["reason"],
                             "candidate_vtd10_name": top.get("name", ""),
                             "name_similarity": top.get("name_similarity", ""),
                             "dominant_2008_vtd_overlap_share": top.get("dominant_2008_vtd_overlap_share", "")})
    print(json.dumps(payload["qa"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
