#!/usr/bin/env python3
import argparse
import json
import os
import re
from collections import Counter, defaultdict

import aggregate_contests_to_vtd20_crosswalks as agg


NON_GEO_PATTERNS = [
    r"\bABSENTEE\b",
    r"\bEMERGENCY\b",
    r"\bFAILSAFE\b",
    r"\bPROVISIONAL\b",
    r"\bCHALLENGED\b",
    r"\bCENTRAL COUNT\b",
    r"\bCURBSIDE\b",
    r"\bOVERSEAS\b",
    r"\bUOCAVA\b",
]


def is_likely_non_geo(name: str) -> bool:
    n = agg.norm(name)
    return any(re.search(pattern, n) for pattern in NON_GEO_PATTERNS)


def main() -> None:
    ap = argparse.ArgumentParser(description="Report unmatched precinct rows under the current 2025 crosswalk stack.")
    ap.add_argument("--base", default=".")
    ap.add_argument("--out", default="data/crosswalk/current_crosswalk_unmatched_report.json")
    args = ap.parse_args()

    base = os.path.abspath(args.base)
    contests_dir = os.path.join(base, "data", "contests")
    display_by_norm = agg.load_precinct_display_by_norm(os.path.join(base, "data", "Voting_Precincts.geojson"))
    aliases = agg.load_aliases(os.path.join(base, "precinct_aliases.json"), display_by_norm)
    weighted = {
        "vtd20_current": agg.load_weighted_splits(os.path.join(base, "data/crosswalk/vtd20_to_2025_vote_weight_splits.json"), display_by_norm),
        "legacy_name": agg.load_weighted_splits(os.path.join(base, "data/crosswalk/legacy_name_to_2025_vote_weight_splits.json"), display_by_norm),
        "vtd00_chain": agg.load_weighted_splits(os.path.join(base, "data/crosswalk/vtd00_to_vtd10_to_2025_vote_weight_splits.json"), display_by_norm),
        "vtd10": agg.load_weighted_splits(os.path.join(base, "data/crosswalk/vtd10_to_2025_vote_weight_splits.json"), display_by_norm),
    }

    manifest = json.load(open(os.path.join(contests_dir, "manifest.json"), encoding="utf-8"))
    by_name = Counter()
    by_file = {}
    samples = defaultdict(list)

    for entry in manifest.get("files") or []:
        file_name = entry.get("file")
        if not file_name:
            continue
        payload = json.load(open(os.path.join(contests_dir, file_name), encoding="utf-8"))
        year = int(payload.get("year") or 0)
        sources = []
        if year <= 2008:
            sources.extend([("legacy_name", weighted["legacy_name"]), ("vtd00_chain", weighted["vtd00_chain"])])
        if year <= 2012:
            sources.append(("vtd10", weighted["vtd10"]))
        if 2012 < year <= 2022:
            sources.append(("vtd20_current", weighted["vtd20_current"]))
        fallback_sources = []
        if year <= 2024 and not any(label == "vtd20_current" for label, _ in sources):
            fallback_sources.append(("vtd20_current_fallback", weighted["vtd20_current"]))
        if year <= 2016 and not any(label == "vtd10" for label, _ in sources):
            fallback_sources.append(("vtd10_fallback", weighted["vtd10"]))
        if year <= 2016 and not any(label == "legacy_name" for label, _ in sources):
            fallback_sources.append(("legacy_name_fallback", weighted["legacy_name"]))

        unmatched = []
        non_geo = []
        for row in payload.get("rows") or []:
            key = str((row or {}).get("county") or "").strip()
            if " - " not in key:
                continue
            mapped, source = agg.remap_precinct_row(
                row,
                display_by_norm=display_by_norm,
                aliases=aliases,
                weighted_sources=sources,
                fallback_weighted_sources=fallback_sources,
            )
            if source == "unmatched":
                item = {
                    "name": key,
                    "total_votes": int((row or {}).get("total_votes") or 0),
                }
                if is_likely_non_geo(key):
                    non_geo.append(item)
                else:
                    unmatched.append(item)
                    by_name[key] += 1
                    if len(samples[key]) < 5:
                        samples[key].append(file_name)
        by_file[file_name] = {
            "year": year,
            "contest_type": payload.get("contest_type") or "",
            "unmatched_geo_count": len(unmatched),
            "unmatched_non_geo_count": len(non_geo),
            "top_unmatched_geo": sorted(unmatched, key=lambda r: (-r["total_votes"], r["name"]))[:25],
            "top_unmatched_non_geo": sorted(non_geo, key=lambda r: (-r["total_votes"], r["name"]))[:15],
        }

    report = {
        "summary": {
            "unique_unmatched_geo_names": len(by_name),
            "total_unmatched_geo_file_hits": sum(by_name.values()),
        },
        "top_unmatched_geo_names": [
            {"name": name, "file_hits": count, "sample_files": samples[name]}
            for name, count in by_name.most_common(100)
        ],
        "files": by_file,
    }
    os.makedirs(os.path.dirname(os.path.join(base, args.out)), exist_ok=True)
    with open(os.path.join(base, args.out), "w", encoding="utf-8", newline="") as fh:
        json.dump(report, fh, indent=2)
        fh.write("\n")
    print(json.dumps(report["summary"], indent=2))
    for row in report["top_unmatched_geo_names"][:25]:
        print(f"{row['file_hits']:>3} {row['name']} :: {', '.join(row['sample_files'])}")


if __name__ == "__main__":
    main()
