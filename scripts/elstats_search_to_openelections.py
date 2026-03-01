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


def precinct_lookup_keys(name: str) -> list[str]:
    """
    Generate a small set of normalized lookup variants for polygon matching.
    Handles common differences like periods (St. vs St), leading zeros (01 vs 1),
    and case.
    """
    raw = (name or "").strip()
    if not raw:
        return []
    s = raw.replace("’", "'").replace("`", "'").upper().strip()
    out = []
    seen = set()

    def add(v: str) -> None:
        v = (v or "").strip().upper()
        if not v:
            return
        if v in seen:
            return
        seen.add(v)
        out.append(v)

    add(s)
    add(s.replace(".", ""))  # "ST. ANDREWS" -> "ST ANDREWS"

    # Strip leading zeros from standalone numeric tokens: "01" -> "1"
    def strip_zeros(txt: str) -> str:
        toks = []
        for tok in txt.split():
            if re.fullmatch(r"0+\d+", tok):
                toks.append(str(int(tok)))
            else:
                toks.append(tok)
        return " ".join(toks)

    add(strip_zeros(s))
    add(strip_zeros(s.replace(".", "")))

    # Normalize common abbreviations.
    add(re.sub(r"\bST\b", "ST.", s))
    add(re.sub(r"\bMT\b", "MT.", s))
    add(re.sub(r"\bST\.\b", "ST", s))
    add(re.sub(r"\bMT\.\b", "MT", s))

    return out


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


def county_signal_from_district(district_type: str, district_name: str) -> str:
    """
    Try to infer a county name from district metadata. This is intentionally conservative:
    we only use district types that are typically county-scoped.
    """
    dt = (district_type or "").strip().lower()
    dn = (district_name or "").strip()
    if not (dt and dn):
        return ""

    # Focus on county-scoped district types.
    if "county council" in dt or dt == "county" or "school district" in dt:
        dn = re.sub(r"\s*\{dt\}.*$", "", dn).strip()
        dn = re.sub(r"\s+county\s*$", "", dn, flags=re.IGNORECASE).strip()
        m = re.match(r"^([A-Za-z][A-Za-z'.\- ]{0,40})", dn)
        if not m:
            return ""
        cand = m.group(1).strip()
        # Drop tiny/obviously non-county tokens.
        if len(cand) < 3:
            return ""
        # Trim trailing punctuation.
        cand = cand.strip(" .,-")
        return cand
    return ""


def build_division_id_to_county(rows: list[dict], precinct_to_county: dict[str, str | None]) -> dict[str, str | None]:
    """
    Build division_id -> county inference map. Useful for non-geographic precinct buckets like
    "Absentee" / "Failsafe" where the county isn't in the division_name.
    """
    cands: dict[str, set[str]] = defaultdict(set)

    for row in rows:
        div_id = (row.get("division_id") or "").strip()
        if not div_id:
            continue
        div_type = (row.get("division_type") or "").strip().lower()
        div_name = (row.get("division_name") or "").strip()
        if not div_name:
            continue

        if div_type == "county":
            cands[div_id].add(div_name)
            continue

        if div_type != "precinct":
            continue

        # Prefer polygon-derived county if division_name matches a unique precinct in shapes.
        cn = None
        for k in precinct_lookup_keys(div_name):
            cn = precinct_to_county.get(k)
            if cn:
                break
        if cn:
            cands[div_id].add(cn)
            continue

        # Fall back to district metadata for county-scoped district types.
        sig = county_signal_from_district(row.get("district_type") or "", row.get("district_name") or "")
        if sig:
            cands[div_id].add(sig)

    out: dict[str, str | None] = {}
    for div_id, s in cands.items():
        if len(s) == 1:
            out[div_id] = next(iter(s))
        else:
            out[div_id] = None
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

        # Read all rows up-front once so we can infer division_id -> county.
        with open(in_path, encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            all_rows = list(reader)

        div_id_to_county = build_division_id_to_county(all_rows, prec_to_county)

        # Accumulate: (county, precinct, office, district, candidate, party) -> channel votes dict
        acc: dict[tuple[str, str, str, str, str, str], dict[str, int]] = {}
        meta = {"date": "", "election_type": "", "rows_in": 0, "rows_out": 0, "precinct_rows_skipped_no_county": 0}

        for row in all_rows:
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

            division_type = (row.get("division_type") or "").strip().lower()
            division_name = (row.get("division_name") or "").strip()
            if not division_name:
                continue

            county = ""
            precinct = ""

            if division_type == "county":
                # County-level rows are the safest way to keep totals correct when the export includes
                # non-geographic buckets that can't be attributed to a single county (e.g., "Absentee 1").
                county = division_name.strip()
                precinct = ""
            elif division_type == "precinct":
                precinct = division_name.strip()

                # Best-effort county inference:
                #  1) polygon index by precinct name (authoritative)
                #  2) division_id mapping via district metadata (for non-geo buckets like Absentee/Failsafe)
                cn = None
                for k in precinct_lookup_keys(precinct):
                    cn = prec_to_county.get(k)
                    if cn:
                        break
                if cn:
                    county = cn
                else:
                    div_id = (row.get("division_id") or "").strip()
                    cn2 = div_id_to_county.get(div_id) if div_id else None
                    if cn2:
                        county = cn2
                    else:
                        meta["precinct_rows_skipped_no_county"] += 1
                        continue
            else:
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
