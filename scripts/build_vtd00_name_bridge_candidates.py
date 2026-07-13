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


def load_result_keys(contests_dir: str, years: set[int]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    manifest_path = os.path.join(contests_dir, "manifest.json")
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh) or {}
    for entry in manifest.get("files") or []:
        try:
            year = int(entry.get("year") or 0)
        except (TypeError, ValueError):
            continue
        if year not in years:
            continue
        path = os.path.join(contests_dir, str(entry.get("file") or ""))
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh) or {}
        for row in payload.get("rows") or []:
            if not isinstance(row, dict):
                continue
            key = str(row.get("county") or "").strip()
            if " - " in key:
                out[norm(key)].add(key)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Build review candidates mapping named legacy result precincts to VTD00 keys.")
    ap.add_argument("--base", default=".", help="Repository root")
    ap.add_argument("--overlap", default="scripts/out/vtd00_to_vtd10_areal_top8.csv")
    ap.add_argument("--contests-dir", default="data/contests")
    ap.add_argument("--years", default="2006,2008")
    ap.add_argument("--out", default="scripts/out/vtd00_name_bridge_candidates_2006_2008.csv")
    args = ap.parse_args()

    base = os.path.abspath(args.base)
    years = {int(y.strip()) for y in args.years.split(",") if y.strip()}
    result_keys = load_result_keys(os.path.join(base, args.contests_dir), years)

    by_target: dict[str, list[dict]] = defaultdict(list)
    target_area_proxy: dict[str, float] = defaultdict(float)
    with open(os.path.join(base, args.overlap), encoding="utf-8", newline="") as fh:
        rd = csv.DictReader(fh)
        for row in rd:
            target_norm = norm(row.get("target_key_norm") or row.get("target_key_display") or "")
            if not target_norm:
                continue
            try:
                area = float(row.get("overlap_area_m2") or 0.0)
                source_share = float(row.get("share_of_source") or 0.0)
            except (TypeError, ValueError):
                area = 0.0
                source_share = 0.0
            if area <= 0 or source_share <= 0:
                continue
            item = {
                "source_vtd00": str(row.get("source_key_display") or "").strip(),
                "source_vtd00_norm": norm(row.get("source_key_norm") or row.get("source_key_display") or ""),
                "target_vtd10": str(row.get("target_key_display") or "").strip(),
                "target_vtd10_norm": target_norm,
                "overlap_area_m2": area,
                "share_of_vtd00": source_share,
            }
            by_target[target_norm].append(item)
            target_area_proxy[target_norm] += area

    rows = []
    for target_norm, names in sorted(result_keys.items()):
        candidates = by_target.get(target_norm) or []
        for cand in candidates:
            target_total = target_area_proxy.get(target_norm) or 0.0
            share_of_target = cand["overlap_area_m2"] / target_total if target_total > 0 else 0.0
            rows.append({
                "result_key_norm": target_norm,
                "result_key_examples": " | ".join(sorted(names)[:4]),
                "candidate_vtd00_key": cand["source_vtd00"],
                "candidate_vtd00_norm": cand["source_vtd00_norm"],
                "matched_vtd10_key": cand["target_vtd10"],
                "share_of_vtd00": f"{cand['share_of_vtd00']:.6f}",
                "share_of_vtd10_proxy": f"{share_of_target:.6f}",
                "overlap_area_m2": f"{cand['overlap_area_m2']:.6f}",
                "confidence": "",
                "decision": "",
            })
    rows.sort(key=lambda r: (r["result_key_norm"], -float(r["share_of_vtd10_proxy"]), -float(r["share_of_vtd00"])))

    # Fill simple confidence labels within each result key.
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["result_key_norm"]].append(row)
    for group in grouped.values():
        if not group:
            continue
        best = float(group[0]["share_of_vtd10_proxy"])
        second = float(group[1]["share_of_vtd10_proxy"]) if len(group) > 1 else 0.0
        if best >= 0.95 and second < 0.05:
            group[0]["confidence"] = "high"
            group[0]["decision"] = "top"
        elif best >= 0.75 and best - second >= 0.25:
            group[0]["confidence"] = "medium"
            group[0]["decision"] = "top"
        else:
            group[0]["confidence"] = "review"
            group[0]["decision"] = "review"
        for row in group[1:]:
            row["confidence"] = "alternative"
            row["decision"] = "alternative"

    out_path = os.path.join(base, args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fieldnames = [
        "result_key_norm",
        "result_key_examples",
        "candidate_vtd00_key",
        "candidate_vtd00_norm",
        "matched_vtd10_key",
        "share_of_vtd00",
        "share_of_vtd10_proxy",
        "overlap_area_m2",
        "confidence",
        "decision",
    ]
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=fieldnames)
        wr.writeheader()
        wr.writerows(rows)

    matched_keys = len(grouped)
    total_keys = len(result_keys)
    high = sum(1 for group in grouped.values() if group and group[0]["confidence"] == "high")
    medium = sum(1 for group in grouped.values() if group and group[0]["confidence"] == "medium")
    review = sum(1 for group in grouped.values() if group and group[0]["confidence"] == "review")
    print(f"wrote {out_path}")
    print(f"result keys: {total_keys}; with candidates: {matched_keys}; no candidates: {total_keys - matched_keys}")
    print(f"top-candidate confidence: high={high} medium={medium} review={review}")


if __name__ == "__main__":
    main()
