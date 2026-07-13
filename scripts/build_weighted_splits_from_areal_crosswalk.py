#!/usr/bin/env python3
import argparse
import csv
import json
import os
from collections import OrderedDict, defaultdict


def _as_float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Convert projected-area overlap crosswalk CSV rows into vote-weight split JSON."
    )
    ap.add_argument("--crosswalk", required=True, help="Areal crosswalk CSV from build_legacy_vtd_overlap_pro.py")
    ap.add_argument("--out", required=True, help="Output weighted split JSON")
    ap.add_argument("--source-label", default="", help="Human-readable source/vintage label for metadata")
    ap.add_argument(
        "--min-share",
        type=float,
        default=0.0,
        help="Drop target overlaps below this source share before renormalizing.",
    )
    args = ap.parse_args()

    grouped: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    display_source: dict[str, str] = {}
    display_target: dict[str, str] = {}

    with open(args.crosswalk, encoding="utf-8", newline="") as fh:
        rd = csv.DictReader(fh)
        for row in rd:
            src_norm = str(row.get("source_key_norm") or "").strip()
            tgt_norm = str(row.get("target_key_norm") or "").strip()
            if not src_norm or not tgt_norm:
                continue
            share = _as_float(row.get("share_of_source"))
            if share <= 0 or share < args.min_share:
                continue
            grouped[src_norm][tgt_norm] += share
            display_source.setdefault(src_norm, str(row.get("source_key_display") or src_norm).strip())
            display_target.setdefault(tgt_norm, str(row.get("target_key_display") or tgt_norm).strip())

    payload = OrderedDict()
    payload["_comment"] = (
        "Auto-generated from projected-area overlap crosswalk. Weights are normalized "
        "share-of-source geometry and can be used to split source precinct votes."
    )
    payload["_source_crosswalk"] = os.path.abspath(args.crosswalk)
    payload["_source_label"] = args.source_label
    payload["_min_share"] = args.min_share

    source_count = 0
    multi_target_count = 0
    max_targets = 0
    dropped_empty_count = 0

    for src_norm in sorted(grouped):
        raw_targets = grouped[src_norm]
        total = sum(raw_targets.values())
        if total <= 0:
            dropped_empty_count += 1
            continue

        source_count += 1
        max_targets = max(max_targets, len(raw_targets))
        if len(raw_targets) > 1:
            multi_target_count += 1

        targets = OrderedDict()
        for tgt_norm, share in sorted(raw_targets.items(), key=lambda kv: (-kv[1], kv[0])):
            target_name = display_target.get(tgt_norm, tgt_norm)
            targets[target_name] = round(float(share) / float(total), 8)

        # Remove rounding drift from the largest target.
        drift = round(1.0 - sum(targets.values()), 8)
        if targets and drift:
            first_key = next(iter(targets))
            targets[first_key] = round(targets[first_key] + drift, 8)

        payload[display_source.get(src_norm, src_norm)] = targets

    payload["_stats"] = {
        "source_count": source_count,
        "multi_target_source_count": multi_target_count,
        "max_targets_per_source": max_targets,
        "dropped_empty_source_count": dropped_empty_count,
    }

    out_dir = os.path.dirname(os.path.abspath(args.out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")

    print(f"wrote {args.out}")
    print(
        "source_count={source_count} multi_target_source_count={multi_target_count} "
        "max_targets_per_source={max_targets}".format(
            source_count=source_count,
            multi_target_count=multi_target_count,
            max_targets=max_targets,
        )
    )


if __name__ == "__main__":
    main()
