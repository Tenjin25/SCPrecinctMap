#!/usr/bin/env python3
"""
Promote stable, approved precinct crosswalk rows into precinct_aliases.json.

Use this to keep year/contest-specific one-off mappings in the CSV while
moving durable name-normalization fixes into aliases.
"""

import argparse
import csv
import json
import os
import re
from collections import defaultdict


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 .\-]", "", str(s or ""))).strip().upper()


def is_approved(v: str) -> bool:
    return (v or "").strip().lower() in {"approved", "true", "1", "yes", "y"}


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Promote stable approved rows from precinct_crosswalk_2024.csv into precinct_aliases.json."
    )
    ap.add_argument("--base", default=os.path.join(os.path.dirname(__file__), ".."), help="Repo root (default: ../)")
    ap.add_argument("--crosswalk", default="precinct_crosswalk_2024.csv", help="Crosswalk CSV relative to base")
    ap.add_argument("--aliases", default="precinct_aliases.json", help="Aliases JSON relative to base")
    ap.add_argument(
        "--include-year-specific",
        action="store_true",
        help="Also promote approved rows with year/contest filters (default: only stable/global rows).",
    )
    ap.add_argument("--write", action="store_true", help="Write updates to aliases JSON")
    args = ap.parse_args()

    base = os.path.abspath(args.base)
    crosswalk_path = os.path.join(base, args.crosswalk)
    aliases_path = os.path.join(base, args.aliases)

    if not os.path.exists(crosswalk_path):
        raise SystemExit(f"Missing {crosswalk_path}")
    if not os.path.exists(aliases_path):
        raise SystemExit(f"Missing {aliases_path}")

    with open(aliases_path, encoding="utf-8") as fh:
        aliases_raw = json.load(fh)
    if not isinstance(aliases_raw, dict):
        raise SystemExit(f"{aliases_path} is not a JSON object")

    # Keep metadata keys (_comment, _format...) untouched.
    alias_pairs: dict[str, str] = {}
    alias_key_by_norm: dict[str, str] = {}
    alias_val_norm_by_key_norm: dict[str, str] = {}
    for k, v in aliases_raw.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        if k.startswith("_"):
            continue
        nk = norm(k)
        nv = norm(v)
        if not (nk and nv):
            continue
        alias_pairs[k] = v
        alias_key_by_norm[nk] = k
        alias_val_norm_by_key_norm[nk] = nv

    # Collect candidate rows keyed by normalized source.
    # Stable rows are either unscoped (year/contest blank) or explicitly scope=stable/global/alias.
    stable_scope_values = {"stable", "global", "alias", "year_agnostic", "all"}
    candidates: dict[str, tuple[str, str, str, str, str]] = {}
    conflicts: dict[str, set[str]] = defaultdict(set)

    with open(crosswalk_path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if not isinstance(row, dict):
                continue
            if not is_approved(str(row.get("status") or "")):
                continue

            src = str(row.get("source_result_key") or "").strip()
            dst = str(row.get("target_polygon_key") or "").strip()
            if not (src and dst):
                continue

            row_year = str(row.get("year") or "").strip()
            row_ct = str(row.get("contest_type") or "").strip()
            row_scope = str(row.get("scope") or "").strip().lower()
            is_stable_scope = row_scope in stable_scope_values
            is_scoped = bool(row_year or row_ct)
            if is_scoped and not (args.include_year_specific or is_stable_scope):
                continue

            nsrc = norm(src)
            ndst = norm(dst)
            if not (nsrc and ndst):
                continue

            prior = candidates.get(nsrc)
            if prior and norm(prior[1]) != ndst:
                conflicts[nsrc].add(prior[1])
                conflicts[nsrc].add(dst)
                continue
            candidates[nsrc] = (src, dst, row_year, row_ct, row_scope)

    additions: list[tuple[str, str]] = []
    skipped_existing = 0
    skipped_conflicting_alias = 0
    for nsrc, (src, dst, _year, _ct, _scope) in sorted(candidates.items(), key=lambda kv: kv[1][0].upper()):
        if nsrc in conflicts:
            continue
        ndst = norm(dst)
        if nsrc in alias_val_norm_by_key_norm:
            if alias_val_norm_by_key_norm[nsrc] == ndst:
                skipped_existing += 1
                continue
            skipped_conflicting_alias += 1
            continue
        additions.append((src, dst))

    print(f"Crosswalk candidates considered: {len(candidates)}")
    print(f"Conflicting crosswalk sources: {len(conflicts)}")
    print(f"Already present in aliases: {skipped_existing}")
    print(f"Conflicting existing aliases: {skipped_conflicting_alias}")
    print(f"Proposed alias additions: {len(additions)}")
    for src, dst in additions:
        print(f"  + {src} -> {dst}")

    if conflicts:
        print("\nConflicts (same source maps to multiple targets):")
        for nsrc, dsts in sorted(conflicts.items()):
            key_display = alias_key_by_norm.get(nsrc, nsrc)
            joined = " | ".join(sorted(dsts))
            print(f"  ! {key_display}: {joined}")

    if not args.write or not additions:
        return

    # Append new entries at the end to keep existing ordering intact.
    for src, dst in additions:
        aliases_raw[src] = dst

    with open(aliases_path, "w", encoding="utf-8", newline="") as fh:
        json.dump(aliases_raw, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"\nUpdated {aliases_path}")


if __name__ == "__main__":
    main()
