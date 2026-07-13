#!/usr/bin/env python3
import argparse
import csv
import json
import os
import re
from collections import OrderedDict, defaultdict


def norm(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 .\-]", "", str(value or ""))
    return re.sub(r"\s+", " ", cleaned).strip().upper()


def load_second_stage(path: str) -> dict[str, dict[str, float]]:
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh) or {}
    out: dict[str, dict[str, float]] = {}
    for src, targets in raw.items():
        if not isinstance(src, str) or src.startswith("_") or not isinstance(targets, dict):
            continue
        cleaned: dict[str, float] = {}
        for target, weight in targets.items():
            try:
                wf = float(weight)
            except (TypeError, ValueError):
                continue
            if wf > 0:
                cleaned[norm(target)] = cleaned.get(norm(target), 0.0) + wf
        total = sum(cleaned.values())
        if total > 0:
            out[norm(src)] = {target: weight / total for target, weight in cleaned.items()}
    return out


def load_display_by_norm(precincts_path: str) -> dict[str, str]:
    if not precincts_path or not os.path.exists(precincts_path):
        return {}
    with open(precincts_path, encoding="utf-8") as fh:
        gj = json.load(fh) or {}
    out = {}
    for feat in gj.get("features", []) or []:
        props = (feat or {}).get("properties") or {}
        key = norm(props.get("precinct_norm") or "")
        if not key:
            continue
        display = str(props.get("precinct_display_name") or "").strip()
        if not display:
            county = str(props.get("county_nam") or "").strip()
            prec = str(props.get("precinct_full_name") or props.get("prec_id") or "").strip()
            display = f"{county} - {prec}".strip()
        if display:
            out[key] = display
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Compose A->B areal shares with B->C weight JSON into A->C vote weights.")
    ap.add_argument("--first-csv", required=True, help="First-stage overlap CSV with source_key_* and target_key_* columns")
    ap.add_argument("--second-json", required=True, help="Second-stage weighted split JSON")
    ap.add_argument("--out", required=True, help="Output composed weighted split JSON")
    ap.add_argument("--precincts", default="data/Voting_Precincts.geojson", help="Final target precinct GeoJSON for display names")
    ap.add_argument("--label", default="", help="Human-readable composed source label")
    args = ap.parse_args()

    second = load_second_stage(args.second_json)
    display_by_norm = load_display_by_norm(args.precincts)
    composed: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    source_display: dict[str, str] = {}
    missing_mid = 0
    first_rows = 0

    with open(args.first_csv, encoding="utf-8", newline="") as fh:
        rd = csv.DictReader(fh)
        for row in rd:
            first_rows += 1
            src_norm = norm(row.get("source_key_norm") or row.get("source_key_display") or "")
            mid_norm = norm(row.get("target_key_norm") or row.get("target_key_display") or "")
            if not src_norm or not mid_norm:
                continue
            try:
                share = float(row.get("share_of_source") or 0.0)
            except (TypeError, ValueError):
                share = 0.0
            if share <= 0:
                continue
            source_display.setdefault(src_norm, str(row.get("source_key_display") or src_norm).strip())
            targets = second.get(mid_norm)
            if not targets:
                missing_mid += 1
                continue
            for final_norm, final_weight in targets.items():
                composed[src_norm][final_norm] += share * final_weight

    payload = OrderedDict()
    payload["_comment"] = "Auto-generated chained vote weights by composing areal crosswalk stages."
    payload["_source_first_csv"] = os.path.abspath(args.first_csv)
    payload["_source_second_json"] = os.path.abspath(args.second_json)
    payload["_source_label"] = args.label

    multi_target_sources = 0
    max_targets = 0
    for src_norm in sorted(composed):
        targets = composed[src_norm]
        total = sum(targets.values())
        if total <= 0:
            continue
        max_targets = max(max_targets, len(targets))
        if len(targets) > 1:
            multi_target_sources += 1
        ordered = OrderedDict()
        for target_norm, weight in sorted(targets.items(), key=lambda kv: (-kv[1], kv[0])):
            display = display_by_norm.get(target_norm, target_norm)
            ordered[display] = round(weight / total, 8)
        drift = round(1.0 - sum(ordered.values()), 8)
        if ordered and drift:
            first_key = next(iter(ordered))
            ordered[first_key] = round(ordered[first_key] + drift, 8)
        payload[source_display.get(src_norm, src_norm)] = ordered

    payload["_stats"] = {
        "first_stage_rows": first_rows,
        "source_count": len([k for k in payload.keys() if not str(k).startswith("_")]),
        "multi_target_source_count": multi_target_sources,
        "max_targets_per_source": max_targets,
        "missing_mid_stage_rows": missing_mid,
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")

    print(f"wrote {args.out}")
    print(json.dumps(payload["_stats"], indent=2))


if __name__ == "__main__":
    main()
