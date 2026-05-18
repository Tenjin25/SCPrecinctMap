#!/usr/bin/env python3
import argparse
import csv
import json
import os
import re
from collections import defaultdict


def norm(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 .\-]", "", str(value or ""))
    return re.sub(r"\s+", " ", cleaned).strip().upper()


def load_crosswalk_multi_targets(crosswalk_path: str, year: int, contest_type: str) -> dict[str, list[str]]:
    grouped: dict[str, set[str]] = defaultdict(set)
    with open(crosswalk_path, encoding="utf-8", newline="") as fh:
        rd = csv.DictReader(fh)
        for row in rd:
            if int(str(row.get("year") or 0)) != int(year):
                continue
            if str(row.get("contest_type") or "").strip().lower() != str(contest_type).strip().lower():
                continue
            if str(row.get("status") or "").strip().lower() != "approved":
                continue
            src = str(row.get("source_result_key") or "").strip()
            tgt = str(row.get("target_polygon_key") or "").strip()
            if not src or not tgt:
                continue
            grouped[src].add(tgt)

    out: dict[str, list[str]] = {}
    for src, tgts in grouped.items():
        if len(tgts) > 1:
            out[src] = sorted(tgts)
    return out


def load_cvap_lookup(cvap_json_path: str, field: str) -> dict[str, float]:
    with open(cvap_json_path, encoding="utf-8") as fh:
        payload = json.load(fh) or {}
    by_prec = (payload.get("by_precinct_norm") or {}) if isinstance(payload, dict) else {}
    out: dict[str, float] = {}
    for k, v in by_prec.items():
        if not isinstance(k, str) or not isinstance(v, dict):
            continue
        try:
            amount = float(v.get(field) or 0.0)
        except (TypeError, ValueError):
            amount = 0.0
        if amount > 0:
            out[norm(k)] = amount
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Build weighted precinct split map from approved crosswalk + precinct CVAP.")
    ap.add_argument("--base", default=".", help="Repo base directory")
    ap.add_argument("--crosswalk", default="precinct_crosswalk_2024.csv", help="Crosswalk CSV path relative to base")
    ap.add_argument("--cvap-json", default="Data/sc_cvap_2024_by_precinct_norm.json", help="Precinct CVAP JSON path relative to base")
    ap.add_argument("--year", type=int, default=2024, help="Election year")
    ap.add_argument("--contest", default="president", help="Contest type")
    ap.add_argument("--cvap-field", default="CVAP_TOT24", help="CVAP field name from CVAP JSON")
    ap.add_argument("--out", default="precinct_weighted_splits_2024.json", help="Weighted splits JSON output path relative to base")
    args = ap.parse_args()

    base = os.path.abspath(args.base)
    crosswalk_path = os.path.join(base, args.crosswalk)
    cvap_path = os.path.join(base, args.cvap_json)
    out_path = os.path.join(base, args.out)

    multi = load_crosswalk_multi_targets(crosswalk_path, args.year, args.contest)
    cvap = load_cvap_lookup(cvap_path, args.cvap_field)

    weighted: dict[str, dict[str, float]] = {}
    fallback_equal = 0
    used_cvap = 0

    for src, targets in sorted(multi.items()):
        weights: list[float] = []
        for tgt in targets:
            weights.append(float(cvap.get(norm(tgt), 0.0)))

        total = sum(weights)
        mapping: dict[str, float] = {}
        if total > 0:
            used_cvap += 1
            for tgt, w in zip(targets, weights):
                mapping[norm(tgt)] = round(float(w) / total, 6)
        else:
            fallback_equal += 1
            equal = 1.0 / float(len(targets))
            for tgt in targets:
                mapping[norm(tgt)] = round(equal, 6)

        # normalize after rounding drift
        s = sum(mapping.values())
        if s > 0:
            for k in list(mapping.keys()):
                mapping[k] = float(mapping[k]) / s
        weighted[src] = mapping

    payload = {
        "_comment": "Auto-generated weighted split mapping from approved crosswalk multi-target rows and precinct CVAP.",
        "_source_crosswalk": os.path.relpath(crosswalk_path, base).replace("\\", "/"),
        "_source_cvap": os.path.relpath(cvap_path, base).replace("\\", "/"),
        "_year": int(args.year),
        "_contest_type": str(args.contest),
        "_cvap_field": str(args.cvap_field),
        "_stats": {
            "multi_source_count": len(multi),
            "used_cvap_count": used_cvap,
            "equal_fallback_count": fallback_equal,
        },
    }
    payload.update(weighted)

    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")

    print(f"wrote {out_path}")
    print(f"multi_source_count={len(multi)} used_cvap_count={used_cvap} equal_fallback_count={fallback_equal}")


if __name__ == "__main__":
    main()
