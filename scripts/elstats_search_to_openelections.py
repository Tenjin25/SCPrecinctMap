import argparse
import csv
import json
import os
import re
from collections import defaultdict


CHANNEL_MAP = {
    "election day": "election_day",
    "early voting": "early_voting",
    "absentee": "absentee_by_mail",
    "absentee by mail": "absentee_by_mail",
    "failsafe provisional": "failsafe_provisional",
    "provisional": "provisional",
    "failsafe": "failsafe",
}

CHANNEL_ORDER = [
    "early_voting",
    "failsafe_provisional",
    "provisional",
    "failsafe",
    "absentee_by_mail",
    "election_day",
]

SKIP_CANDIDATES = {
    "total votes cast",
    "total ballots cast",
    "overvotes/undervotes",
}

OFFICE_MAP = {
    "president of the united states": "President",
    "u.s. senate": "U.S. Senate",
    "u.s. house": "U.S. House",
}

PARTY_MAP = {
    "democratic": "DEM",
    "republican": "REP",
    "libertarian": "LIB",
    "green": "GRN",
    "constitution": "CON",
    "working families": "WFP",
}


def norm_token(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 .\-]", "", str(s or ""))).strip().upper()


def norm_precinct_name(s: str) -> str:
    # Slightly more permissive than norm_token: keep apostrophes for matching where present.
    raw = (s or "").strip()
    raw = raw.replace("’", "'").replace("`", "'")
    return raw.upper().strip()


def load_precinct_to_county_index(voting_precincts_geojson_path: str) -> dict[str, str | None]:
    """
    Map normalized precinct name -> county name, or None if ambiguous.
    Uses the polygon source as the authority for "which county does this precinct name belong to?"
    """
    with open(voting_precincts_geojson_path, encoding="utf-8") as fh:
        gj = json.load(fh)

    hits: dict[str, set[str]] = defaultdict(set)
    for f in gj.get("features", []):
        p = (f or {}).get("properties") or {}
        county = str(p.get("county_nam") or "").strip()
        prec = str(p.get("prec_id") or "").strip()
        if not (county and prec):
            continue
        hits[norm_precinct_name(prec)].add(county)

    out: dict[str, str | None] = {}
    for k, counties in hits.items():
        if len(counties) == 1:
            out[k] = next(iter(counties))
        else:
            out[k] = None
    return out


def normalize_office(office_name: str) -> str:
    s = (office_name or "").strip()
    if not s:
        return ""
    key = s.lower().strip()
    return OFFICE_MAP.get(key, s)


def normalize_party(party_name: str) -> str:
    s = (party_name or "").strip()
    if not s:
        return ""
    key = s.lower().strip()
    if key in PARTY_MAP:
        return PARTY_MAP[key]
    # Fallback: "Independent" -> "IND", "Nonpartisan" -> "NONPARTISAN", etc.
    return re.sub(r"[^A-Za-z0-9]", "", s.upper())[:12]


def channel_col(vote_channel: str) -> str:
    s = (vote_channel or "").strip().lower()
    return CHANNEL_MAP.get(s, "election_day")


def extract_yyyymmdd(election_date: str) -> str:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", (election_date or "").strip())
    if not m:
        return ""
    return f"{m.group(1)}{m.group(2)}{m.group(3)}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Convert ELSTATS elstats_search_*.csv to an OpenElections-style precinct CSV.")
    ap.add_argument("inputs", nargs="+", help="Input ELSTATS CSV(s)")
    ap.add_argument("--polygons", default=os.path.join("scprecinctmap-gh", "data", "Voting_Precincts.geojson"),
                    help="Voting precinct polygons GeoJSON (for county inference)")
    ap.add_argument("--out-dir", default=os.path.join("Data", "_tmpdata"),
                    help="Output directory (default: Data/_tmpdata)")
    ap.add_argument("--state", default="sc", help="State postal (default: sc)")
    ap.add_argument("--election-type", default="general", help="Election type segment in filename (default: general)")
    args = ap.parse_args()

    if not os.path.exists(args.polygons):
        raise SystemExit(f"Missing polygons file: {args.polygons}")
    os.makedirs(args.out_dir, exist_ok=True)

    prec_to_county = load_precinct_to_county_index(args.polygons)

    for in_path in args.inputs:
        if not os.path.exists(in_path):
            print(f"SKIP missing input: {in_path}")
            continue

        # Accumulate: (county, precinct, office, district, candidate, party) -> channel votes dict
        acc: dict[tuple[str, str, str, str, str, str], dict[str, int]] = {}
        meta = {"date": "", "election_type": "", "rows_in": 0, "rows_out": 0, "precinct_rows_skipped_no_county": 0}

        with open(in_path, encoding="utf-8", newline="") as fh:
            r = csv.DictReader(fh)
            for row in r:
                meta["rows_in"] += 1

                election_date = (row.get("election_date") or "").strip()
                if election_date and not meta["date"]:
                    meta["date"] = election_date

                office_raw = (row.get("office_name") or "").strip()
                office = normalize_office(office_raw)
                if not office:
                    continue

                cand_raw = (row.get("candidate_name") or "").strip()
                if not cand_raw:
                    continue
                if cand_raw.strip().lower() in SKIP_CANDIDATES:
                    continue

                division_type = (row.get("division_type") or "").strip()
                division_name = (row.get("division_name") or "").strip()
                if not division_name:
                    continue

                county = ""
                precinct = ""
                if division_type.lower() == "county":
                    county = division_name.strip()
                    precinct = ""
                else:
                    precinct = division_name.strip()
                    # Best-effort county inference: (1) polygon index by precinct name, (2) "CountyName ..." prefix.
                    cn = prec_to_county.get(norm_precinct_name(precinct))
                    if cn:
                        county = cn
                    else:
                        # Try "COUNTYNAME ..." prefix pattern (works for "Abbeville No. 01" etc).
                        first = precinct.split(" ", 1)[0].strip()
                        if first and first[0].isalpha():
                            county = first
                            # If we used the prefix as county, keep full precinct label as-is (matches 2024 style).
                        else:
                            meta["precinct_rows_skipped_no_county"] += 1
                            continue

                district_type = (row.get("district_type") or "").strip().lower()
                district_name = (row.get("district_name") or "").strip()
                district = ""
                if district_type and district_type not in {"state", "county"}:
                    # Congressional District / State House District / State Senate District, etc.
                    district = district_name

                party = normalize_party(row.get("candidate_party_name") or "")
                votes = int(float(row.get("votes") or 0) or 0)
                vc = channel_col(row.get("vote_channel") or "")

                key = (county, precinct, office, district, cand_raw, party)
                node = acc.get(key)
                if not node:
                    node = {c: 0 for c in CHANNEL_ORDER}
                    acc[key] = node
                node[vc] = int(node.get(vc, 0) + votes)

        yyyymmdd = extract_yyyymmdd(meta["date"])
        if not yyyymmdd:
            # Fallback: derive from first row by reading again (rare).
            yyyymmdd = "unknown_date"

        out_name = f"{yyyymmdd}__{args.state}__{args.election_type}__precinct__from_elstats_search.csv"
        out_path = os.path.join(args.out_dir, out_name)

        with open(out_path, "w", newline="", encoding="utf-8") as out:
            w = csv.writer(out)
            w.writerow(["county", "precinct", "office", "district", "candidate", "party", "votes", *CHANNEL_ORDER])
            for (county, precinct, office, district, candidate, party), channels in sorted(acc.items()):
                total = sum(int(channels.get(c, 0) or 0) for c in CHANNEL_ORDER)
                meta["rows_out"] += 1
                w.writerow([
                    county,
                    precinct,
                    office,
                    district,
                    candidate,
                    party,
                    total,
                    *[int(channels.get(c, 0) or 0) for c in CHANNEL_ORDER],
                ])

        print(f"Wrote {out_path}")
        print(f"  in={meta['rows_in']:,} rows | out={meta['rows_out']:,} rows | skipped_precinct_no_county={meta['precinct_rows_skipped_no_county']:,}")


if __name__ == "__main__":
    main()

