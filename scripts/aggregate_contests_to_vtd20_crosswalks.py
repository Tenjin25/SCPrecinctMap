#!/usr/bin/env python3
import argparse
import json
import os
import re
from collections import OrderedDict


VOTE_FIELDS = ("dem_votes", "rep_votes", "other_votes")

# These counties adopted the current precinct layer after the November 2024
# election, so their 2024 rows must be translated through the VTD20 crosswalk.
POST_2024_PLAN_COUNTIES = {"BEAUFORT", "CLARENDON", "LANCASTER", "LAURENS"}

# Current-plan spellings for 2024 election labels that do not normalize to an
# RFA display key. Keep these separate from historical aliases, whose direction
# can intentionally differ for older precinct plans.
CURRENT_2024_ALIAS_TARGETS_RAW = {
    "ANDERSON - MT AIRY": "Anderson - Mt. Airy",
    "CHESTER - LANDSFORD": "Chester - Lando/Landsford",
    "CHESTER - LANDO/LANSFORD": "Chester - Lando/Landsford",
    "EDGEFIELD - MERRIWEATHER NO.1": "Edgefield - Merriwether No. 1",
    "EDGEFIELD - MERRIWEATHER NO.2": "Edgefield - Merriwether No. 2",
    "EDGEFIELD - TRENTON NO.1": "Edgefield - Trenton",
    "EDGEFIELD - TRENTON NO.2": "Edgefield - Trenton 2",
    "HORRY - JERNIGANS XROADS": "Horry - Jernigans X Roads",
    "HORRY - JERIGANS X ROADS": "Horry - Jernigans X Roads",
    "JASPER - OKATIE": "Jasper - Okatie 1",
    "KERSHAW - CAMDEN NO. 2 3": "Kershaw - Camden No. 2",
    "LANCASTER - MCILWAIN": "Lancaster - McLlwain",
    "MARLBORO - E BENNETTSVILLE": "Marlboro - East Bennettsville",
    "SUMTER - ST. JOHN": "Sumter - St. John",
    "SUMTER - ST.JOHN": "Sumter - St. John",
    "UNION - MONARCH": "Union - Monarch, Box 1",
}

def norm(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 .\-]", "", str(value or ""))
    cleaned = re.sub(r"\b(?:NO\.?|NUMBER)\s*(?=\d)", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\bI\b", "1", cleaned, flags=re.I)
    cleaned = re.sub(r"\bII\b", "2", cleaned, flags=re.I)
    cleaned = re.sub(r"\bIII\b", "3", cleaned, flags=re.I)
    cleaned = re.sub(r"\bIV\b", "4", cleaned, flags=re.I)
    cleaned = re.sub(r"\bV\b", "5", cleaned, flags=re.I)
    return re.sub(r"\s+", " ", cleaned).strip().upper()


CURRENT_2024_ALIAS_TARGETS = {
    norm(source): target for source, target in CURRENT_2024_ALIAS_TARGETS_RAW.items()
}


def margin_color(signed_pct: float) -> str:
    if abs(signed_pct) < 0.001:
        return "#f0f0f0"
    party = "R" if signed_pct > 0 else "D"
    absp = abs(signed_pct)
    colors = [
        (40, "R", "#67000d"),
        (30, "R", "#a50f15"),
        (20, "R", "#cb181d"),
        (10, "R", "#ef3b2c"),
        (5, "R", "#fc8a6a"),
        (0, "R", "#fcbba1"),
        (0, "D", "#c6dbef"),
        (5, "D", "#9ecae1"),
        (10, "D", "#6baed6"),
        (20, "D", "#4292c6"),
        (30, "D", "#2171b5"),
        (40, "D", "#08519c"),
    ]
    best = "#f0f0f0"
    for thresh, side, color in sorted(colors, reverse=True, key=lambda item: item[0]):
        if side == party and absp >= thresh:
            best = color
            break
    return best


def split_integer_by_weights(total: int, weights: list[float]) -> list[int]:
    total_i = int(total or 0)
    if total_i <= 0 or not weights:
        return [0 for _ in weights]
    wsum = sum(float(w) for w in weights)
    if wsum <= 0:
        return [0 for _ in weights]
    normalized = [float(w) / wsum for w in weights]
    raw = [total_i * w for w in normalized]
    parts = [int(x) for x in raw]
    remainder = total_i - sum(parts)
    if remainder > 0:
        order = sorted(range(len(raw)), key=lambda i: (raw[i] - int(raw[i]), -i), reverse=True)
        for i in order[:remainder]:
            parts[i] += 1
    return parts


def load_precinct_display_by_norm(path: str) -> dict[str, str]:
    with open(path, encoding="utf-8") as fh:
        gj = json.load(fh) or {}
    out: dict[str, str] = {}
    for feat in gj.get("features", []) or []:
        props = (feat or {}).get("properties") or {}
        pn = norm(props.get("precinct_norm") or "")
        if not pn:
            continue
        display = str(props.get("precinct_display_name") or "").strip()
        if not display:
            county = str(props.get("county_nam") or "").strip()
            prec = str(props.get("precinct_full_name") or props.get("prec_id") or "").strip()
            display = f"{county} - {prec}".strip()
        if display:
            out[pn] = display
    return out


def load_aliases(path: str, display_by_norm: dict[str, str]) -> dict[str, str]:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh) or {}
    out: dict[str, str] = {}
    for src, dst in raw.items():
        if not isinstance(src, str) or not isinstance(dst, str) or src.startswith("_"):
            continue
        nsrc = norm(src)
        ndst = norm(dst)
        if nsrc and ndst:
            out[nsrc] = display_by_norm.get(ndst, str(dst).strip())
    return out


def load_weighted_splits(path: str, display_by_norm: dict[str, str]) -> dict[str, list[tuple[str, float]]]:
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh) or {}
    out: dict[str, list[tuple[str, float]]] = {}
    for src, targets in raw.items():
        if not isinstance(src, str) or src.startswith("_") or not isinstance(targets, dict):
            continue
        pairs: list[tuple[str, float]] = []
        for target, weight in targets.items():
            try:
                wf = float(weight)
            except (TypeError, ValueError):
                continue
            if wf <= 0:
                continue
            nt = norm(target)
            if not nt:
                continue
            pairs.append((display_by_norm.get(nt, str(target).strip()), wf))
        total = sum(w for _, w in pairs)
        if total > 0:
            out[norm(src)] = [(target, weight / total) for target, weight in pairs]
    return out


def load_manual_weighted_splits(path: str, display_by_norm: dict[str, str]) -> dict[str, list[tuple[str, float]]]:
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh) or {}
    out: dict[str, list[tuple[str, float]]] = {}
    entries = raw.get("overrides") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        return out
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        src = str(entry.get("source") or "").strip()
        targets = entry.get("targets") or {}
        if not src or not isinstance(targets, dict):
            continue
        pairs: list[tuple[str, float]] = []
        for target, weight in targets.items():
            try:
                wf = float(weight)
            except (TypeError, ValueError):
                continue
            nt = norm(target)
            if wf <= 0 or not nt:
                continue
            pairs.append((display_by_norm.get(nt, str(target).strip()), wf))
        total = sum(w for _, w in pairs)
        if total > 0:
            out[norm(src)] = [(target, weight / total) for target, weight in pairs]
    return out


def finalize_row(row: dict) -> dict:
    dem = int(row.get("dem_votes") or 0)
    rep = int(row.get("rep_votes") or 0)
    other = int(row.get("other_votes") or 0)
    total = dem + rep + other
    row["dem_votes"] = dem
    row["rep_votes"] = rep
    row["other_votes"] = other
    row["total_votes"] = total
    margin = rep - dem
    row["margin"] = margin
    row["margin_pct"] = round((margin / total * 100), 4) if total else 0
    row["winner"] = "R" if margin > 0 else ("D" if margin < 0 else "T")
    row["color"] = margin_color(float(row["margin_pct"]))
    return row


def add_to_bucket(bucket: OrderedDict[str, dict], row: dict) -> None:
    key = str(row.get("county") or "").strip()
    if not key:
        return
    if key not in bucket:
        bucket[key] = dict(row)
        return
    acc = bucket[key]
    for field in VOTE_FIELDS:
        acc[field] = int(acc.get(field) or 0) + int(row.get(field) or 0)
    for field in ("dem_candidate", "rep_candidate"):
        if not acc.get(field) and row.get(field):
            acc[field] = row.get(field)


def remap_precinct_row(
    row: dict,
    *,
    display_by_norm: dict[str, str],
    aliases: dict[str, str],
    weighted_sources: list[tuple[str, dict[str, list[tuple[str, float]]]]],
    fallback_weighted_sources: list[tuple[str, dict[str, list[tuple[str, float]]]]] | None = None,
    prefer_direct_match: bool = False,
) -> tuple[list[dict], str]:
    key = str(row.get("county") or "").strip()
    nk = norm(key)
    def apply_weighted(label: str, weighted: dict[str, list[tuple[str, float]]]) -> tuple[list[dict], str] | None:
        if nk in weighted:
            pairs = weighted[nk]
            targets = [target for target, _ in pairs]
            weights = [weight for _, weight in pairs]
            split_votes = {field: split_integer_by_weights(int(row.get(field) or 0), weights) for field in VOTE_FIELDS}
            out = []
            for idx, target in enumerate(targets):
                nr = dict(row)
                nr["county"] = target
                for field in VOTE_FIELDS:
                    nr[field] = split_votes[field][idx]
                out.append(finalize_row(nr))
            return out, f"weighted_{label}"
        return None

    for label, weighted in weighted_sources:
        result = apply_weighted(label, weighted)
        if result:
            return result
    if prefer_direct_match and nk in display_by_norm:
        nr = dict(row)
        nr["county"] = display_by_norm[nk]
        return [finalize_row(nr)], "direct"
    if nk in aliases:
        nr = dict(row)
        nr["county"] = aliases[nk]
        return [finalize_row(nr)], "alias"
    if nk in display_by_norm:
        nr = dict(row)
        nr["county"] = display_by_norm[nk]
        return [finalize_row(nr)], "direct"
    for label, weighted in fallback_weighted_sources or []:
        result = apply_weighted(label, weighted)
        if result:
            return result
    return [finalize_row(dict(row))], "unmatched"


def process_contest(path: str, args, display_by_norm, aliases, weighted_by_year) -> tuple[dict, dict]:
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh) or {}
    year = int(payload.get("year") or 0)
    contest_type = str(payload.get("contest_type") or os.path.basename(path).rsplit("_", 1)[0])
    weighted_sources: list[tuple[str, dict[str, list[tuple[str, float]]]]] = []
    if year <= int(args.vtd00_chain_year_max):
        weighted_sources.append(("manual_historical", weighted_by_year.get("manual_historical") or {}))
        weighted_sources.append(("legacy_name", weighted_by_year.get("legacy_name") or {}))
        weighted_sources.append(("vtd00_chain", weighted_by_year.get("vtd00_chain") or {}))
    if year <= int(args.legacy_year_max):
        if ("manual_historical", weighted_by_year.get("manual_historical") or {}) not in weighted_sources:
            weighted_sources.append(("manual_historical", weighted_by_year.get("manual_historical") or {}))
        weighted_sources.append(("vtd10", weighted_by_year.get("vtd10") or {}))
    if int(args.legacy_year_max) < year <= int(args.recent_year_max):
        weighted_sources.append(("vtd20_current", weighted_by_year.get("vtd20_current") or {}))
    fallback_weighted_sources = []
    if year <= 2024 and not any(label == "vtd20_current" for label, _ in weighted_sources):
        fallback_weighted_sources.append(("vtd20_current_fallback", weighted_by_year.get("vtd20_current") or {}))
    if year <= 2016 and not any(label == "vtd10" for label, _ in weighted_sources):
        fallback_weighted_sources.append(("vtd10_fallback", weighted_by_year.get("vtd10") or {}))
    if year <= 2016 and not any(label == "legacy_name" for label, _ in weighted_sources):
        fallback_weighted_sources.append(("legacy_name_fallback", weighted_by_year.get("legacy_name") or {}))
    county_bucket: OrderedDict[str, dict] = OrderedDict()
    precinct_bucket: OrderedDict[str, dict] = OrderedDict()
    stats = {
        "year": year,
        "contest_type": contest_type,
        "source_file": os.path.basename(path),
        "source_rows": len(payload.get("rows") or []),
        "county_rows": 0,
        "precinct_rows": 0,
        "weighted_rows": 0,
        "weighted_vtd20_current_rows": 0,
        "weighted_vtd20_current_fallback_rows": 0,
        "weighted_manual_historical_rows": 0,
        "weighted_legacy_name_rows": 0,
        "weighted_legacy_name_fallback_rows": 0,
        "weighted_vtd00_chain_rows": 0,
        "weighted_vtd10_rows": 0,
        "weighted_vtd10_fallback_rows": 0,
        "weighted_expanded_extra_rows": 0,
        "alias_rows": 0,
        "direct_rows": 0,
        "unmatched_rows": 0,
        "output_rows": 0,
    }

    for row in payload.get("rows") or []:
        if not isinstance(row, dict):
            continue
        key = str(row.get("county") or "").strip()
        if " - " not in key:
            stats["county_rows"] += 1
            add_to_bucket(county_bucket, finalize_row(dict(row)))
            continue
        stats["precinct_rows"] += 1
        county_norm = norm(key.split(" - ", 1)[0])
        row_weighted_sources = weighted_sources
        row_fallback_weighted_sources = fallback_weighted_sources
        if year == 2024 and county_norm in POST_2024_PLAN_COUNTIES:
            row_weighted_sources = [("vtd20_current", weighted_by_year.get("vtd20_current") or {})]
            row_fallback_weighted_sources = []
        row_aliases = aliases
        current_target = CURRENT_2024_ALIAS_TARGETS.get(norm(key)) if year == 2024 else None
        if current_target:
            row_aliases = dict(aliases)
            row_aliases[norm(key)] = display_by_norm.get(norm(current_target), current_target)
        mapped_rows, source = remap_precinct_row(
            row,
            display_by_norm=display_by_norm,
            aliases=row_aliases,
            weighted_sources=row_weighted_sources,
            fallback_weighted_sources=row_fallback_weighted_sources,
            # The complete 2024 OpenElections export already uses the precinct
            # plan in effect for that election. Preserve exact RFA-name matches
            # before consulting aliases for precincts whose labels changed.
            prefer_direct_match=(year == 2024),
        )
        if source.startswith("weighted_"):
            stats["weighted_rows"] += 1
            if source == "weighted_vtd20_current":
                stats["weighted_vtd20_current_rows"] += 1
            elif source == "weighted_vtd20_current_fallback":
                stats["weighted_vtd20_current_fallback_rows"] += 1
            elif source == "weighted_manual_historical":
                stats["weighted_manual_historical_rows"] += 1
            elif source == "weighted_legacy_name":
                stats["weighted_legacy_name_rows"] += 1
            elif source == "weighted_legacy_name_fallback":
                stats["weighted_legacy_name_fallback_rows"] += 1
            elif source == "weighted_vtd00_chain":
                stats["weighted_vtd00_chain_rows"] += 1
            elif source == "weighted_vtd10":
                stats["weighted_vtd10_rows"] += 1
            elif source == "weighted_vtd10_fallback":
                stats["weighted_vtd10_fallback_rows"] += 1
            stats["weighted_expanded_extra_rows"] += max(0, len(mapped_rows) - 1)
        elif source == "alias":
            stats["alias_rows"] += 1
        elif source == "direct":
            stats["direct_rows"] += 1
        else:
            stats["unmatched_rows"] += 1
        for mapped in mapped_rows:
            add_to_bucket(precinct_bucket, mapped)

    out_rows = [finalize_row(row) for row in county_bucket.values()]
    out_rows.extend(finalize_row(row) for row in precinct_bucket.values())
    stats["output_rows"] = len(out_rows)
    return {"year": year, "contest_type": contest_type, "rows": out_rows}, stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Aggregate contest JSON rows onto current precinct keys using areal crosswalk weights.")
    ap.add_argument("--base", default=".", help="Repository root")
    ap.add_argument("--contests-dir", default="data/contests", help="Input contests directory relative to base")
    ap.add_argument("--out-dir", default="data/contests_2025_crosswalked", help="Output directory relative to base")
    ap.add_argument("--recent-year-max", type=int, default=2022, help="Apply VTD20->current weights through this year")
    ap.add_argument("--legacy-year-max", type=int, default=2012, help="Apply VTD10->current weights through this year")
    ap.add_argument("--vtd00-chain-year-max", type=int, default=2008, help="Try chained VTD00->VTD10->current weights through this year")
    ap.add_argument("--weights-vtd20-current", default="data/crosswalk/vtd20_to_2025_vote_weight_splits.json")
    ap.add_argument("--weights-vtd00-chain", default="data/crosswalk/vtd00_to_vtd10_to_2025_vote_weight_splits.json")
    ap.add_argument("--weights-legacy-name", default="data/crosswalk/legacy_name_to_2025_vote_weight_splits.json")
    ap.add_argument("--weights-vtd10", default="data/crosswalk/vtd10_to_2025_vote_weight_splits.json")
    ap.add_argument("--manual-historical-weights", default="data/crosswalk/manual_historical_current_weight_overrides.json")
    ap.add_argument("--aliases", default="precinct_aliases.json")
    ap.add_argument("--precincts", default="data/Voting_Precincts.geojson")
    args = ap.parse_args()

    base = os.path.abspath(args.base)
    contests_dir = os.path.join(base, args.contests_dir)
    out_dir = os.path.join(base, args.out_dir)
    os.makedirs(out_dir, exist_ok=True)

    display_by_norm = load_precinct_display_by_norm(os.path.join(base, args.precincts))
    aliases = load_aliases(os.path.join(base, args.aliases), display_by_norm)
    weighted_by_year = {
        "vtd20_current": load_weighted_splits(os.path.join(base, args.weights_vtd20_current), display_by_norm),
        "manual_historical": load_manual_weighted_splits(os.path.join(base, args.manual_historical_weights), display_by_norm),
        "legacy_name": load_weighted_splits(os.path.join(base, args.weights_legacy_name), display_by_norm),
        "vtd00_chain": load_weighted_splits(os.path.join(base, args.weights_vtd00_chain), display_by_norm),
        "vtd10": load_weighted_splits(os.path.join(base, args.weights_vtd10), display_by_norm),
    }

    manifest_path = os.path.join(contests_dir, "manifest.json")
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh) or {}
    out_manifest = {"files": []}
    qa = []

    for entry in manifest.get("files") or []:
        file_name = str(entry.get("file") or "").strip()
        if not file_name:
            continue
        in_path = os.path.join(contests_dir, file_name)
        if not os.path.exists(in_path):
            continue
        out_payload, stats = process_contest(in_path, args, display_by_norm, aliases, weighted_by_year)
        out_path = os.path.join(out_dir, file_name)
        with open(out_path, "w", encoding="utf-8", newline="") as fh:
            json.dump(out_payload, fh, separators=(",", ":"))
        out_manifest["files"].append({
            "year": out_payload["year"],
            "contest_type": out_payload["contest_type"],
            "file": file_name,
            "rows": len(out_payload["rows"]),
        })
        qa.append(stats)

    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8", newline="") as fh:
        json.dump(out_manifest, fh, separators=(",", ":"))
    qa_name = "qa_2025_crosswalked.json" if "2025" in os.path.basename(out_dir) else "qa_vtd20_crosswalked.json"
    with open(os.path.join(out_dir, qa_name), "w", encoding="utf-8", newline="") as fh:
        json.dump({"stats": qa}, fh, indent=2)
        fh.write("\n")

    print(f"wrote {len(out_manifest['files'])} contest files -> {os.path.relpath(out_dir, base)}")
    for row in qa:
        if row["year"] <= args.legacy_year_max:
            print(
                f"{row['contest_type']}_{row['year']}: "
                f"weighted={row['weighted_rows']} "
                f"(vtd20_current={row['weighted_vtd20_current_rows']},vtd20_fallback={row['weighted_vtd20_current_fallback_rows']},manual_historical={row['weighted_manual_historical_rows']},legacy_name={row['weighted_legacy_name_rows']},legacy_name_fallback={row['weighted_legacy_name_fallback_rows']},vtd00_chain={row['weighted_vtd00_chain_rows']},vtd10={row['weighted_vtd10_rows']},vtd10_fallback={row['weighted_vtd10_fallback_rows']}) "
                f"alias={row['alias_rows']} "
                f"direct={row['direct_rows']} unmatched={row['unmatched_rows']} "
                f"out_rows={row['output_rows']}"
            )


if __name__ == "__main__":
    main()
