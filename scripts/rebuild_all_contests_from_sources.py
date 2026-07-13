#!/usr/bin/env python3
"""Rebuild source-exact statewide contest slices for every covered election year."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from collections import OrderedDict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
import build_data  # noqa: E402


SOURCE_FILES = {
    2006: "openelections-data-sc/2006/20061107__sc__general__precinct.csv",
    2008: "20081104__sc__general__precinct__from_elstats_search.csv",
    2010: "20101102__sc__general__precinct__from_elstats_search.csv",
    2012: "20121106__sc__general__precinct__from_elstats_search.csv",
    2014: "20141104__sc__general__precinct__from_elstats_search.csv",
    2016: "openelections-data-sc/2016/20161108__sc__general__precinct.csv",
    2018: "openelections-data-sc/2018/20181106__sc__general__precinct.csv",
    2020: "openelections-data-sc/2020/20201103__sc__general__precinct.csv",
    2022: "openelections-data-sc/2022/20221108__sc__general__precinct.csv",
    2024: "@repo:data/20241105__sc__general__precinct_complete.csv",
}
VOTE_FIELDS = ("dem_votes", "rep_votes", "other_votes")


def vote_bucket() -> dict:
    return {"dem_votes": 0, "rep_votes": 0, "other_votes": 0, "dem_candidate": "", "rep_candidate": ""}


def add_vote(bucket: dict, party: str, votes: int, candidate: str) -> None:
    normalized = build_data.normalize_party(party)
    if normalized == "DEM":
        bucket["dem_votes"] += votes
        if candidate and not bucket["dem_candidate"]:
            bucket["dem_candidate"] = candidate
    elif normalized == "REP":
        bucket["rep_votes"] += votes
        if candidate and not bucket["rep_candidate"]:
            bucket["rep_candidate"] = candidate
    else:
        bucket["other_votes"] += votes


def existing_candidate_defaults(contests_dir: Path) -> dict[tuple[int, str], tuple[str, str]]:
    out = {}
    for path in contests_dir.glob("*.json"):
        if path.name in {"manifest.json", "source_integrity.json"}:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        year = int(payload.get("year") or 0)
        contest = str(payload.get("contest_type") or "").strip()
        dem = rep = ""
        for row in payload.get("rows") or []:
            dem = dem or str((row or {}).get("dem_candidate") or "").strip()
            rep = rep or str((row or {}).get("rep_candidate") or "").strip()
            if dem and rep:
                break
        if year and contest:
            out[(year, contest)] = (dem, rep)
    return out


def county_display_names(precincts_path: Path) -> dict[str, str]:
    payload = json.loads(precincts_path.read_text(encoding="utf-8"))
    out = {}
    for feature in payload.get("features") or []:
        county = str(((feature or {}).get("properties") or {}).get("county_nam") or "").strip()
        if county:
            out[build_data.normalize(county)] = county
    return out


def make_row(key: str, values: dict, defaults: tuple[str, str]) -> dict:
    dem = int(values.get("dem_votes") or 0)
    rep = int(values.get("rep_votes") or 0)
    other = int(values.get("other_votes") or 0)
    total = dem + rep + other
    margin = rep - dem
    margin_pct = round(margin / total * 100, 4) if total else 0
    return {
        "county": key,
        "dem_votes": dem,
        "rep_votes": rep,
        "other_votes": other,
        "total_votes": total,
        "dem_candidate": defaults[0] or values.get("dem_candidate") or "",
        "rep_candidate": defaults[1] or values.get("rep_candidate") or "",
        "margin": margin,
        "margin_pct": margin_pct,
        "winner": "R" if margin > 0 else ("D" if margin < 0 else "T"),
        "color": build_data.margin_color(margin_pct),
    }


def resolve_source(source_root: Path, spec: str) -> Path:
    if spec.startswith("@repo:"):
        return REPO_ROOT / spec.split(":", 1)[1]
    return source_root / spec


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", default="Data/_tmpdata")
    parser.add_argument("--years", nargs="*", type=int, default=sorted(SOURCE_FILES))
    parser.add_argument("--contests-dir", default="data/contests")
    parser.add_argument("--precincts", default="data/Voting_Precincts.geojson")
    args = parser.parse_args()

    source_root = Path(args.source_root)
    if not source_root.is_absolute():
        source_root = REPO_ROOT / source_root
    contests_dir = REPO_ROOT / args.contests_dir
    contests_dir.mkdir(parents=True, exist_ok=True)
    county_names = county_display_names(REPO_ROOT / args.precincts)
    candidate_defaults = existing_candidate_defaults(contests_dir)
    integrity = {"sources": [], "contests": []}

    for year in args.years:
        if year not in SOURCE_FILES:
            raise SystemExit(f"No configured source for {year}")
        source_path = resolve_source(source_root, SOURCE_FILES[year])
        if not source_path.exists():
            raise SystemExit(f"Missing source for {year}: {source_path}")
        raw_bytes = source_path.read_bytes()
        with source_path.open(encoding="utf-8-sig", newline="") as fh:
            raw_rows = list(csv.DictReader(fh))
        integrity["sources"].append({
            "year": year,
            "file": source_path.name,
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "rows": len(raw_rows),
        })

        by_contest: dict[str, list[dict]] = {}
        for row in raw_rows:
            office = str(row.get("office") or "").strip().lower()
            contest = build_data.OFFICE_MAP.get(office)
            if contest and contest not in build_data.SKIP_DISTRICT_OFFICES:
                by_contest.setdefault(contest, []).append(row)

        for contest, rows in sorted(by_contest.items()):
            explicit_counties = {
                build_data.normalize(row.get("county") or "")
                for row in rows
                if str(row.get("county") or "").strip() and not str(row.get("precinct") or "").strip()
            }
            county_votes: OrderedDict[str, dict] = OrderedDict()
            precinct_votes: OrderedDict[tuple[str, str], dict] = OrderedDict()
            nongeo_votes = 0
            for source in rows:
                county_raw = str(source.get("county") or "").strip()
                if not county_raw:
                    continue
                county_norm = build_data.normalize(county_raw)
                county = county_names.get(county_norm, county_raw.title())
                precinct = str(source.get("precinct") or "").strip()
                votes = int(source.get("votes") or 0)
                party = str(source.get("party") or "")
                candidate = str(source.get("candidate") or "").strip()
                if (county_norm in explicit_counties and not precinct) or county_norm not in explicit_counties:
                    add_vote(county_votes.setdefault(county, vote_bucket()), party, votes, candidate)
                if precinct:
                    if build_data.is_non_geo(precinct):
                        nongeo_votes += votes
                    else:
                        label = build_data.normalize_precinct_label(precinct)
                        add_vote(precinct_votes.setdefault((county, label), vote_bucket()), party, votes, candidate)

            defaults = candidate_defaults.get((year, contest), ("", ""))
            county_rows = [make_row(county, values, defaults) for county, values in sorted(county_votes.items())]
            precinct_rows = [
                make_row(f"{county} - {precinct}", values, defaults)
                for (county, precinct), values in sorted(precinct_votes.items())
            ]
            payload = {"year": year, "contest_type": contest, "rows": county_rows + precinct_rows}
            out_path = contests_dir / f"{contest}_{year}.json"
            out_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            integrity["contests"].append({
                "year": year,
                "contest_type": contest,
                "source_rows": len(rows),
                "county_rows": len(county_rows),
                "geographic_precinct_rows": len(precinct_rows),
                "county_votes": sum(row["total_votes"] for row in county_rows),
                "geographic_precinct_votes": sum(row["total_votes"] for row in precinct_rows),
                "non_geographic_votes": nongeo_votes,
            })
            print(f"{contest}_{year}: {len(county_rows)} counties + {len(precinct_rows)} geographic precincts")

    priority = getattr(build_data, "_PRIORITY", {})
    manifest = []
    for path in contests_dir.glob("*.json"):
        if path.name in {"manifest.json", "source_integrity.json"} or path.name.endswith("_from_7131_list.json"):
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        year = int(payload.get("year") or 0)
        contest = str(payload.get("contest_type") or "").strip()
        if year and contest and isinstance(payload.get("rows"), list):
            manifest.append({"year": year, "contest_type": contest, "file": path.name, "rows": len(payload["rows"])})
    manifest.sort(key=lambda item: (-item["year"], priority.get(item["contest_type"], 99)))
    (contests_dir / "manifest.json").write_text(json.dumps({"files": manifest}, separators=(",", ":")), encoding="utf-8")
    (contests_dir / "source_integrity.json").write_text(json.dumps(integrity, indent=2) + "\n", encoding="utf-8")
    print(f"rebuilt {len(integrity['contests'])} contest slices from {len(integrity['sources'])} source files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
