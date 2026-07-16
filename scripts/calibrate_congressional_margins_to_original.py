#!/usr/bin/env python3
"""Calibrate district margins toward originals under dual constraints.

Default mode (`both`) keeps:
  1. each district's current total_votes
  2. statewide dem/rep/other totals

and pushes district party shares toward the pre-rebuild baseline (918f2f6).

Scopes: congressional, state_senate, state_house_root, state_house_2022_lines,
state_house_2024_lines. State House freezes HD-40 and HD-82 by default.
"""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import build_data  # noqa: E402
from rebuild_district_contests_from_current_geojson import (  # noqa: E402
    calibrate_to_snapshot,
    load_district_snapshot_targets,
)

BASE = "918f2f65f4052458461b802a8f4190bb542d1924"
FIELDS = ("dem_votes", "rep_votes", "other_votes")
SNAPSHOT = REPO / "data/district_contests/district_snapshot_targets.json"
FREEZE_HOUSE = {"40", "82"}

SCOPE_SPECS = {
    "congressional": {
        "geometry": "congressional",
        "glob": "data/district_contests/congressional_*.json",
        "prefix": "congressional_",
        "freeze": set(),
    },
    "state_senate": {
        "geometry": "state_senate_2022",
        "glob": "data/district_contests/state_senate_*.json",
        "prefix": "state_senate_",
        "freeze": set(),
    },
    "state_house_root": {
        "geometry": "state_house_root",
        "glob": "data/district_contests/state_house_*.json",
        "prefix": "state_house_",
        "freeze": FREEZE_HOUSE,
        "files_only": True,
    },
    "state_house_2022_lines": {
        "geometry": "state_house_2022",
        "glob": "data/district_contests/state_house_2022_lines/state_house_*.json",
        "prefix": "state_house_",
        "suffix": "_2022_lines",
        "freeze": FREEZE_HOUSE,
    },
    "state_house_2024_lines": {
        "geometry": "state_house_2024",
        "glob": "data/district_contests/state_house_2024_lines/state_house_*.json",
        "prefix": "state_house_",
        "suffix": "_2024_lines",
        "freeze": FREEZE_HOUSE,
    },
}


def git_json(rel: str):
    try:
        txt = subprocess.check_output(
            ["git", "show", f"{BASE}:{rel}"],
            cwd=REPO,
            text=True,
            encoding="utf-8",
            stderr=subprocess.DEVNULL,
        )
        return json.loads(txt)
    except subprocess.CalledProcessError:
        return None


def margin_pct(row: dict) -> float:
    dem = int(row.get("dem_votes") or 0)
    rep = int(row.get("rep_votes") or 0)
    oth = int(row.get("other_votes") or 0)
    tot = dem + rep + oth
    return (rep - dem) / tot * 100 if tot else 0.0


def total_votes(row: dict) -> int:
    if row.get("total_votes") is not None:
        return int(row["total_votes"] or 0)
    return sum(int(row.get(f) or 0) for f in FIELDS)


def is_uncontested_row(row: dict | None) -> bool:
    """True when a district has no competitive major-party contest."""
    if not row:
        return True
    dem = int(row.get("dem_votes") or 0)
    rep = int(row.get("rep_votes") or 0)
    other = int(row.get("other_votes") or 0)
    total = int(row.get("total_votes") or (dem + rep + other))
    if total <= 0 or (dem + rep) <= 0:
        return True
    if dem <= 0 or rep <= 0:
        return True
    dem_name = str(row.get("dem_candidate") or "").strip().lower()
    rep_name = str(row.get("rep_candidate") or "").strip().lower()
    if "no democratic candidate" in dem_name or "no republican candidate" in rep_name:
        return True
    return False


def compare_margins(
    cur_results: dict,
    orig_results: dict,
    exclude: set[str] | None = None,
    ignore_uncontested: bool = True,
) -> tuple[float, float, set[str]]:
    exclude = exclude or set()
    ignored: set[str] = set()
    keys = []
    for k in set(cur_results) & set(orig_results):
        nk = str(int(k)) if str(k).isdigit() else str(k)
        if nk in exclude or str(k) in exclude:
            ignored.add(str(k))
            continue
        if ignore_uncontested and (is_uncontested_row(cur_results[k]) or is_uncontested_row(orig_results[k])):
            ignored.add(str(k))
            continue
        keys.append(k)
    if not keys:
        return 0.0, 0.0, ignored
    abs_vals = [abs(margin_pct(cur_results[k]) - margin_pct(orig_results[k])) for k in keys]
    return round(sum(abs_vals) / len(abs_vals), 4), round(max(abs_vals), 4), ignored


def contest_key_from_name(name: str, prefix: str, suffix: str = "") -> str | None:
    if not name.startswith(prefix) or not name.endswith(".json"):
        return None
    stem = name[len(prefix) : -len(".json")]
    if suffix and stem.endswith(suffix):
        stem = stem[: -len(suffix)]
    if stem.startswith("us_house_") or stem.startswith("state_house_") or stem.startswith("state_senate_"):
        # district-specific chamber races, not statewide projections
        if stem.startswith("state_house_state_house_") or stem.startswith("state_senate_state_senate_"):
            return None
        if stem.startswith("us_house_"):
            return None
    return stem


def allocate_integer(votes: int, shares: dict[str, float]) -> dict[str, int]:
    if votes <= 0 or not shares:
        return {k: 0 for k in FIELDS}
    normalized = sum(max(0.0, float(v)) for v in shares.values())
    if normalized <= 0:
        return {k: 0 for k in FIELDS}
    exact = {k: votes * max(0.0, float(shares.get(k, 0.0))) / normalized for k in FIELDS}
    allocated = {k: int(exact[k]) for k in FIELDS}
    remainder = votes - sum(allocated.values())
    order = sorted(FIELDS, key=lambda k: (exact[k] - allocated[k], k), reverse=True)
    for k in order[:remainder]:
        allocated[k] += 1
    return allocated


def make_result(values: dict, dem_candidate: str, rep_candidate: str) -> dict:
    dem = int(values.get("dem_votes") or 0)
    rep = int(values.get("rep_votes") or 0)
    other = int(values.get("other_votes") or 0)
    total = dem + rep + other
    margin = rep - dem
    mp = round(margin / total * 100, 4) if total else 0.0
    return {
        "dem_votes": dem,
        "rep_votes": rep,
        "other_votes": other,
        "total_votes": total,
        "dem_candidate": dem_candidate,
        "rep_candidate": rep_candidate,
        "margin": margin,
        "margin_pct": mp,
        "winner": "R" if margin > 0 else ("D" if margin < 0 else "T"),
        "color": build_data.margin_color(mp),
    }


def shares_from_row(row: dict) -> dict[str, float]:
    vals = {f: float(int(row.get(f) or 0)) for f in FIELDS}
    tot = sum(vals.values())
    if tot <= 0:
        return {f: 0.0 for f in FIELDS}
    return {f: vals[f] / tot for f in FIELDS}


def iter_scope_files(scope: str, spec: dict):
    for path in sorted(REPO.glob(spec["glob"])):
        if "manifest" in path.name or path.name.startswith("qa_"):
            continue
        if spec.get("files_only") and not path.is_file():
            continue
        if spec.get("files_only") and path.parent.name != "district_contests":
            continue
        yield path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("both", "margins-only"),
        default="both",
        help="both=keep district totals + statewide party totals; margins-only=exact original shares",
    )
    parser.add_argument(
        "--scopes",
        default="state_senate,state_house_root,state_house_2022_lines,state_house_2024_lines",
        help="Comma-separated scopes to process",
    )
    parser.add_argument("--threshold-pp", type=float, default=1.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    snapshot = load_district_snapshot_targets(SNAPSHOT)
    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
    reports = []

    for scope in scopes:
        spec = SCOPE_SPECS.get(scope)
        if not spec:
            raise SystemExit(f"Unknown scope: {scope}")
        geometry = spec["geometry"]
        geom_targets = snapshot.get(geometry) or {}
        freeze = set(spec.get("freeze") or set())
        prefix = spec["prefix"]
        suffix = spec.get("suffix") or ""

        for path in iter_scope_files(scope, spec):
            key = contest_key_from_name(path.name, prefix, suffix)
            if not key:
                continue
            rel = path.relative_to(REPO).as_posix()
            cur = json.loads(path.read_text(encoding="utf-8"))
            orig = git_json(rel)
            if not orig:
                reports.append({"scope": scope, "file": rel, "status": "no_baseline"})
                continue
            cur_results = (cur.get("general") or {}).get("results") or {}
            orig_results = (orig.get("general") or {}).get("results") or {}
            mean_b, max_b, ignored = compare_margins(cur_results, orig_results, exclude=freeze)
            uncontested_keys = {
                str(k)
                for k in set(cur_results) | set(orig_results)
                if is_uncontested_row(cur_results.get(k)) or is_uncontested_row(orig_results.get(k))
            }
            freeze_effective = set(freeze) | {
                (str(int(k)) if str(k).isdigit() else str(k)) for k in uncontested_keys
            }
            if max_b <= args.threshold_pp:
                reports.append(
                    {
                        "scope": scope,
                        "file": rel,
                        "status": "ok",
                        "mean_before": mean_b,
                        "max_before": max_b,
                        "ignored_uncontested": len(uncontested_keys),
                    }
                )
                continue

            targets = geom_targets.get(key)
            if args.mode == "both" and not targets:
                reports.append(
                    {
                        "scope": scope,
                        "file": rel,
                        "status": "no_snapshot_target",
                        "max_before": max_b,
                    }
                )
                continue

            candidate = copy.deepcopy(cur)
            if args.mode == "both":
                # Drop frozen/uncontested districts from share targets so they stay on current rows.
                active_targets = {
                    d: shares
                    for d, shares in targets.items()
                    if (str(int(d)) if str(d).isdigit() else str(d)) not in freeze_effective
                }
                cal = calibrate_to_snapshot(
                    candidate,
                    active_targets,
                    f"{SNAPSHOT.as_posix()}#{geometry}/{key}",
                    tolerance_pp=1.0,
                    geometry_key=geometry,
                    dynamic_tolerance=True,
                    frozen_districts=freeze_effective,
                )
                new_results = (candidate.get("general") or {}).get("results") or {}
                mean_a, max_a, _ignored_a = compare_margins(new_results, orig_results, exclude=freeze)
                meta_extra = {
                    "margin_calibration_mode": "both_district_totals_and_statewide_party",
                    **cal,
                    "margin_calibration_mean_abs_delta_pp_before": mean_b,
                    "margin_calibration_max_abs_delta_pp_before": max_b,
                    "margin_calibration_mean_abs_delta_pp_after": mean_a,
                    "margin_calibration_max_abs_delta_pp_after": max_a,
                    "margin_calibration_ignored_uncontested_districts": sorted(
                        uncontested_keys, key=build_data._district_sort_key
                    ),
                }
            else:
                new_results = {}
                for district, row in cur_results.items():
                    nd = str(int(district)) if str(district).isdigit() else str(district)
                    if nd in freeze_effective:
                        new_results[str(district)] = dict(row)
                        continue
                    tot = total_votes(row)
                    if str(district) in orig_results and tot > 0:
                        values = allocate_integer(tot, shares_from_row(orig_results[str(district)]))
                    else:
                        values = {f: int(row.get(f) or 0) for f in FIELDS}
                    new_results[str(district)] = make_result(
                        values, row.get("dem_candidate", ""), row.get("rep_candidate", "")
                    )
                candidate["general"]["results"] = new_results
                mean_a, max_a, _ignored_a = compare_margins(new_results, orig_results, exclude=freeze)
                meta_extra = {
                    "margin_calibration_mode": "margins_only_original_shares",
                    "margin_calibration_mean_abs_delta_pp_before": mean_b,
                    "margin_calibration_max_abs_delta_pp_before": max_b,
                    "margin_calibration_mean_abs_delta_pp_after": mean_a,
                    "margin_calibration_max_abs_delta_pp_after": max_a,
                }

            source_totals = {f: sum(int(r.get(f) or 0) for r in cur_results.values()) for f in FIELDS}
            new_totals = {f: sum(int(r.get(f) or 0) for r in new_results.values()) for f in FIELDS}
            tot_ok = all(total_votes(cur_results[d]) == total_votes(new_results[d]) for d in cur_results)
            # Frozen / uncontested rows unchanged
            freeze_ok = all(
                all(int(cur_results[d].get(f) or 0) == int(new_results[d].get(f) or 0) for f in FIELDS)
                for d in cur_results
                if (str(int(d)) if str(d).isdigit() else str(d)) in freeze_effective
            )
            party_delta = {f: new_totals[f] - source_totals[f] for f in FIELDS}

            if not args.dry_run:
                candidate.setdefault("meta", {}).update(meta_extra)
                path.write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")

            reports.append(
                {
                    "scope": scope,
                    "file": rel,
                    "status": "calibrated" if not args.dry_run else "would_calibrate",
                    "mean_before": mean_b,
                    "max_before": max_b,
                    "mean_after": mean_a,
                    "max_after": max_a,
                    "district_totals_conserved": tot_ok,
                    "frozen_unchanged": freeze_ok,
                    "ignored_uncontested": len(uncontested_keys),
                    "statewide_party_delta": party_delta,
                }
            )

    calibrated = [r for r in reports if r["status"] in ("calibrated", "would_calibrate")]
    ok = [r for r in reports if r["status"] == "ok"]
    print(f"mode={args.mode} threshold_pp={args.threshold_pp} dry_run={args.dry_run} scopes={scopes}")
    print(f"ok={len(ok)} calibrate={len(calibrated)} other={len(reports)-len(ok)-len(calibrated)}")
    by_scope: dict[str, list] = {}
    for r in calibrated:
        by_scope.setdefault(r["scope"], []).append(r)
    for scope, items in by_scope.items():
        print(f"\n=== {scope} ({len(items)}) ===")
        for r in sorted(items, key=lambda x: -x.get("max_before", 0))[:12]:
            print(
                f"  {Path(r['file']).name}: max {r['max_before']}->{r['max_after']} "
                f"mean {r['mean_before']}->{r['mean_after']} "
                f"party_delta={r['statewide_party_delta']} "
                f"totals_ok={r['district_totals_conserved']} freeze_ok={r.get('frozen_unchanged')} "
                f"uncontested_ignored={r.get('ignored_uncontested', 0)}"
            )
        if len(items) > 12:
            print(f"  ... +{len(items)-12} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
