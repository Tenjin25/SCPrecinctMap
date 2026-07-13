#!/usr/bin/env python3
"""Rebuild statewide-by-district contest files from current precinct overlap weights."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
import build_data  # noqa: E402
from scripts.aggregate_contests_to_vtd20_crosswalks import norm  # noqa: E402


OUTPUTS = (
    ("congressional", Path("data/district_contests"), "congressional", "", None),
    ("state_house_2022", Path("data/district_contests"), "state_house", "", None),
    ("state_senate_2022", Path("data/district_contests"), "state_senate", "", None),
    ("state_house_2022", Path("data/district_contests/state_house_2022_lines"), "state_house", "", "2022_lines"),
    ("state_house_2024", Path("data/district_contests/state_house_2024_lines"), "state_house", "", "2024_lines"),
)
FIELDS = ("dem_votes", "rep_votes", "other_votes")


def allocate_integer(votes: int, shares: dict[str, float]) -> dict[str, int]:
    if votes <= 0 or not shares:
        return {}
    normalized_total = sum(float(value) for value in shares.values())
    exact = {key: votes * float(value) / normalized_total for key, value in shares.items()}
    allocated = {key: int(value) for key, value in exact.items()}
    remainder = votes - sum(allocated.values())
    order = sorted(exact, key=lambda key: (exact[key] - allocated[key], key), reverse=True)
    for key in order[:remainder]:
        allocated[key] += 1
    return allocated


def make_result(values: dict, dem_candidate: str, rep_candidate: str) -> dict:
    dem = int(values.get("dem_votes") or 0)
    rep = int(values.get("rep_votes") or 0)
    other = int(values.get("other_votes") or 0)
    total = dem + rep + other
    margin = rep - dem
    margin_pct = round(margin / total * 100, 4) if total else 0
    return {
        "dem_votes": dem,
        "rep_votes": rep,
        "other_votes": other,
        "total_votes": total,
        "dem_candidate": dem_candidate,
        "rep_candidate": rep_candidate,
        "margin": margin,
        "margin_pct": margin_pct,
        "winner": "R" if margin > 0 else ("D" if margin < 0 else "T"),
        "color": build_data.margin_color(margin_pct),
    }


def rebuild_one(contest: dict, weights: dict[str, dict[str, float]], scope: str, district_file: str) -> tuple[dict, dict]:
    precinct_rows = [row for row in contest.get("rows") or [] if " - " in str((row or {}).get("county") or "")]
    mapped = []
    fallback = []
    county_district_votes: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in precinct_rows:
        key = str(row.get("county") or "")
        shares = weights.get(norm(key))
        if shares:
            mapped.append((row, shares))
            county = norm(key.split(" - ", 1)[0])
            total = int(row.get("total_votes") or 0)
            for district, share in shares.items():
                county_district_votes[county][district] += total * float(share)
        else:
            fallback.append(row)

    assignments = list(mapped)
    fallback_votes = 0
    for row in fallback:
        county = norm(str(row.get("county") or "").split(" - ", 1)[0])
        district_totals = county_district_votes.get(county) or {}
        total = sum(district_totals.values())
        if total <= 0:
            raise SystemExit(f"No district fallback shares for {row.get('county')} in {scope}")
        shares = {district: value / total for district, value in district_totals.items()}
        assignments.append((row, shares))
        fallback_votes += int(row.get("total_votes") or 0)

    by_district: dict[str, dict] = defaultdict(lambda: {field: 0 for field in FIELDS})
    dem_candidate = rep_candidate = ""
    for row, shares in assignments:
        dem_candidate = dem_candidate or str(row.get("dem_candidate") or "")
        rep_candidate = rep_candidate or str(row.get("rep_candidate") or "")
        for field in FIELDS:
            for district, votes in allocate_integer(int(row.get(field) or 0), shares).items():
                by_district[district][field] += votes

    results = {
        district: make_result(values, dem_candidate, rep_candidate)
        for district, values in sorted(by_district.items(), key=lambda item: build_data._district_sort_key(item[0]))
    }
    source_totals = {field: sum(int(row.get(field) or 0) for row in precinct_rows) for field in FIELDS}
    output_totals = {field: sum(result[field] for result in results.values()) for field in FIELDS}
    deltas = {field: output_totals[field] - source_totals[field] for field in FIELDS}
    if any(deltas.values()):
        raise SystemExit(f"District vote conservation failure for {contest.get('contest_type')}_{contest.get('year')} {scope}: {deltas}")
    meta = {
        "match_coverage_pct": round(len(mapped) / len(precinct_rows) * 100, 4) if precinct_rows else 0,
        "precinct_rows_total": len(precinct_rows),
        "precinct_rows_overlap_weighted": len(mapped),
        "precinct_rows_county_share_fallback": len(fallback),
        "precinct_votes_county_share_fallback": fallback_votes,
        "district_lines_file": district_file,
        "precinct_lines_file": "data/Voting_Precincts.geojson",
        "assignment_method": "current_precinct_polygon_overlap_with_county_share_fallback",
        "conservation_delta": deltas,
    }
    return {"general": {"results": results}, "meta": meta}, meta


def rebuild_manifest(directory: Path, line_suffix: str, district_lines: str | None) -> None:
    entries = []
    for path in directory.glob("*.json"):
        if path.name.startswith(("manifest", "qa_", "current_geojson_qa")):
            continue
        stem = path.stem
        if line_suffix and stem.endswith(line_suffix):
            stem = stem[: -len(line_suffix)]
        parts = stem.split("_")
        if parts[:2] in (["state", "house"], ["state", "senate"]):
            scope = "_".join(parts[:2]); contest = "_".join(parts[2:-1])
        else:
            scope = parts[0]; contest = "_".join(parts[1:-1])
        try:
            year = int(parts[-1])
        except ValueError:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = len(((payload.get("general") or {}).get("results") or {}))
        entry = {"scope": scope, "contest_type": contest, "year": year, "file": path.name, "rows": rows}
        if district_lines:
            entry["district_lines"] = district_lines
        entries.append(entry)
    entries.sort(key=lambda item: (-item["year"], item["scope"], item["contest_type"]))
    (directory / "manifest.json").write_text(json.dumps({"files": entries}, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contests", default="data/contests_2025_crosswalked")
    parser.add_argument("--crosswalk", default="data/crosswalk/current_precinct_to_district_weights.json")
    parser.add_argument("--precincts", default="data/Voting_Precincts.geojson")
    args = parser.parse_args()
    contest_dir = REPO_ROOT / args.contests
    manifest = json.loads((contest_dir / "manifest.json").read_text(encoding="utf-8"))
    crosswalk = json.loads((REPO_ROOT / args.crosswalk).read_text(encoding="utf-8"))
    precincts = json.loads((REPO_ROOT / args.precincts).read_text(encoding="utf-8"))
    key_aliases = {}
    for feature in precincts.get("features") or []:
        props = (feature or {}).get("properties") or {}
        precinct_norm = norm(props.get("precinct_norm") or "")
        county = str(props.get("county_nam") or "").strip()
        if not precinct_norm:
            continue
        for value in (
            props.get("precinct_norm"),
            props.get("precinct_display_name"),
            f"{county} - {str(props.get('precinct_full_name') or '').strip()}",
            f"{county} - {str(props.get('prec_id') or '').strip()}",
        ):
            alias = norm(value)
            if alias:
                key_aliases[alias] = precinct_norm
    qa = []
    rebuilt_dirs = set()
    for scope_key, out_rel, prefix, suffix, district_lines in OUTPUTS:
        scope_data = crosswalk["scopes"][scope_key]
        # Contest rows use friendly display labels while the geometry crosswalk
        # stores Fiscal Affairs precinct_norm keys. Normalize both sides with
        # the same election-key routine before joining them.
        canonical_weights = {norm(key): value for key, value in scope_data["weights"].items()}
        weights = {
            alias: canonical_weights[precinct_norm]
            for alias, precinct_norm in key_aliases.items()
            if precinct_norm in canonical_weights
        }
        out_dir = REPO_ROOT / out_rel
        out_dir.mkdir(parents=True, exist_ok=True)
        rebuilt_dirs.add((out_dir, suffix, district_lines))
        for entry in manifest.get("files") or []:
            contest = json.loads((contest_dir / entry["file"]).read_text(encoding="utf-8"))
            payload, meta = rebuild_one(contest, weights, scope_key, scope_data["district_file"])
            name = f"{prefix}_{entry['contest_type']}_{entry['year']}{suffix}.json"
            (out_dir / name).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            qa.append({"scope": scope_key, "contest_type": entry["contest_type"], "year": entry["year"], "file": name, **meta})
    for directory, suffix, district_lines in rebuilt_dirs:
        if district_lines:
            for stale in directory.glob(f"*_{district_lines}.json"):
                stale.unlink()
            for stale in directory.glob("manifest_*_lines.json"):
                stale.unlink()
        rebuild_manifest(directory, suffix, district_lines)
    qa_path = REPO_ROOT / "data/district_contests/current_geojson_qa.json"
    qa_path.write_text(json.dumps({"files": qa}, indent=2) + "\n", encoding="utf-8")
    print(f"rebuilt {len(qa)} statewide-by-district files from current precinct geometry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
