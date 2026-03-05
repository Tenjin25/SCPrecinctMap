import argparse
import csv
import json
import os
import re
from collections import OrderedDict


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 .\-]", "", str(s or ""))).strip().upper()


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
        (0, "T", "#f0f0f0"),
        (0, "D", "#c6dbef"),
        (5, "D", "#9ecae1"),
        (10, "D", "#6baed6"),
        (20, "D", "#4292c6"),
        (30, "D", "#2171b5"),
        (40, "D", "#08519c"),
        (999, "D", "#08306b"),
    ]
    best = "#f0f0f0"
    for thresh, p, color in sorted(colors, reverse=True, key=lambda x: x[0]):
        if p == party and absp >= thresh:
            best = color
            break
    return best


def load_precinct_display_by_norm(voting_precincts_geojson_path: str) -> dict[str, str]:
    with open(voting_precincts_geojson_path, encoding="utf-8") as fh:
        gj = json.load(fh) or {}
    out: dict[str, str] = {}
    for f in gj.get("features", []) or []:
        p = (f or {}).get("properties") or {}
        pn = norm(p.get("precinct_norm") or "")
        if not pn:
            continue
        county = str(p.get("county_nam") or "").strip()
        prec = str(p.get("prec_id") or "").strip()
        display = f"{county} - {prec}".strip()
        if display:
            out[pn] = display
    return out


def load_aliases(aliases_path: str, display_by_norm: dict[str, str]) -> dict[str, str]:
    if not aliases_path or not os.path.exists(aliases_path):
        return {}
    with open(aliases_path, encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        if k.startswith("_"):
            continue
        nk = norm(k)
        nv = norm(v)
        if not (nk and nv):
            continue
        out[nk] = display_by_norm.get(nv, v.strip())
    return out


def load_crosswalk_map(
    crosswalk_path: str,
    display_by_norm: dict[str, str],
    year: int | None = None,
    contest_type: str | None = None,
) -> dict[str, list[str]]:
    if not crosswalk_path or not os.path.exists(crosswalk_path):
        return {}

    ct_filter = (contest_type or "").strip().lower()
    y_filter = str(year or "").strip()

    def is_approved(v: str) -> bool:
        return str(v or "").strip().lower() in {"approved", "true", "1", "yes", "y"}

    out: dict[str, list[str]] = {}
    seen_targets: dict[str, set[str]] = {}

    try:
        with open(crosswalk_path, encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                if not isinstance(row, dict):
                    continue
                if not is_approved(row.get("status")):
                    continue

                row_year = str(row.get("year") or "").strip()
                row_ct = str(row.get("contest_type") or "").strip().lower()
                if row_year and y_filter and row_year != y_filter:
                    continue
                if row_ct and ct_filter and row_ct != ct_filter:
                    continue

                src = str(row.get("source_result_key") or row.get("source_precinct") or "").strip()
                dst = str(row.get("target_polygon_key") or row.get("target_precinct") or "").strip()
                if not (src and dst):
                    continue

                nsrc = norm(src)
                ndst = norm(dst)
                if not (nsrc and ndst):
                    continue

                dst_display = display_by_norm.get(ndst, dst)
                dst_norm = norm(dst_display)
                if not dst_norm:
                    continue

                if nsrc not in out:
                    out[nsrc] = []
                    seen_targets[nsrc] = set()
                if dst_norm in seen_targets[nsrc]:
                    continue
                out[nsrc].append(dst_display)
                seen_targets[nsrc].add(dst_norm)
    except Exception:
        return {}

    return out


def split_crosswalk_aliases(crosswalk_map: dict[str, list[str]] | None) -> tuple[dict[str, str], dict[str, list[str]]]:
    crosswalk_map = crosswalk_map or {}
    one_to_one: dict[str, str] = {}
    one_to_many: dict[str, list[str]] = {}

    for src, targets in crosswalk_map.items():
        clean = [t for t in (targets or []) if isinstance(t, str) and t.strip()]
        if len(clean) == 1:
            one_to_one[src] = clean[0]
        elif len(clean) > 1:
            one_to_many[src] = clean

    return one_to_one, one_to_many


def merge_rows(rows: list[dict], contest_type: str) -> list[dict]:
    """
    Merge duplicate precinct rows that share the same 'county' key (after aliasing).
    County-level rows (no ' - ') are left as-is except for de-duping by exact key.
    """
    out: list[dict] = []
    county_seen: dict[str, dict] = OrderedDict()
    precinct_seen: dict[str, dict] = OrderedDict()

    for r in rows or []:
        if not isinstance(r, dict):
            continue
        key = str(r.get("county") or "").strip()
        if not key:
            continue
        is_precinct = " - " in key
        bucket = precinct_seen if is_precinct else county_seen
        if key not in bucket:
            bucket[key] = dict(r)
            continue

        # Merge vote totals; keep first non-empty candidate labels.
        acc = bucket[key]
        for field in ("dem_votes", "rep_votes", "other_votes", "total_votes"):
            acc[field] = int(acc.get(field) or 0) + int(r.get(field) or 0)
        for field in ("dem_candidate", "rep_candidate"):
            if not acc.get(field) and r.get(field):
                acc[field] = r.get(field)

    # Recompute derived fields for merged nodes.
    def finalize(node: dict) -> dict:
        dem = int(node.get("dem_votes") or 0)
        rep = int(node.get("rep_votes") or 0)
        other = int(node.get("other_votes") or 0)
        total = dem + rep + other
        node["total_votes"] = total
        margin = rep - dem
        node["margin"] = margin
        mpct = round(margin / total * 100, 4) if total else 0
        node["margin_pct"] = mpct
        node["winner"] = "R" if margin > 0 else ("D" if margin < 0 else "T")
        node["color"] = margin_color(mpct)
        return node

    for _, r in county_seen.items():
        out.append(finalize(r))
    for _, r in precinct_seen.items():
        out.append(finalize(r))
    return out


def update_manifest(manifest_path: str, contest_type: str, year: int, rows_count: int) -> None:
    if not os.path.exists(manifest_path):
        return
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            payload = json.load(fh) or {}
        files = payload.get("files")
        if not isinstance(files, list):
            return
        changed = False
        for entry in files:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("contest_type") or "") != contest_type:
                continue
            if int(entry.get("year") or 0) != int(year):
                continue
            if int(entry.get("rows") or 0) != int(rows_count):
                entry["rows"] = int(rows_count)
                changed = True
        if not changed:
            return
        tmp = manifest_path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        os.replace(tmp, manifest_path)
    except Exception:
        return


def main():
    ap = argparse.ArgumentParser(description="Apply precinct_aliases.json to an existing contest slice and merge duplicates.")
    ap.add_argument("--base", default=".", help="Repo base directory (default: .)")
    ap.add_argument("--contest", default="president", help="Contest type (e.g. president)")
    ap.add_argument("--year", default="2024", help="Election year (e.g. 2024)")
    ap.add_argument("--all", action="store_true", help="Process all files in data/contests (ignores --contest/--year)")
    ap.add_argument("--aliases", default="precinct_aliases.json", help="Alias JSON path relative to base")
    ap.add_argument("--crosswalk", default="precinct_crosswalk_2024.csv", help="Crosswalk CSV path relative to base")
    ap.add_argument("--no-crosswalk", action="store_true", help="Disable crosswalk application")
    args = ap.parse_args()

    base = os.path.abspath(args.base)

    voting_precincts = os.path.join(base, "data", "Voting_Precincts.geojson")
    aliases_path = os.path.join(base, args.aliases)
    crosswalk_path = os.path.join(base, args.crosswalk)
    manifest_path = os.path.join(base, "data", "contests", "manifest.json")

    if not os.path.exists(voting_precincts):
        raise SystemExit(f"Missing {voting_precincts}")

    display_by_norm = load_precinct_display_by_norm(voting_precincts)
    aliases = load_aliases(aliases_path, display_by_norm)

    contests_dir = os.path.join(base, "data", "contests")
    if not os.path.isdir(contests_dir):
        raise SystemExit(f"Missing {contests_dir}")

    def parse_contest_and_year(filename: str) -> tuple[str, int] | tuple[None, None]:
        if not filename.endswith(".json"):
            return None, None
        if filename == "manifest.json":
            return None, None
        stem = filename[:-5]
        i = stem.rfind("_")
        if i <= 0:
            return None, None
        contest = stem[:i]
        year_s = stem[i + 1 :]
        try:
            year = int(year_s)
        except ValueError:
            return None, None
        if year < 1800 or year > 3000:
            return None, None
        return contest, year

    def process_one(slice_path: str, contest: str, year: int) -> tuple[int, int, int]:
        with open(slice_path, encoding="utf-8") as fh:
            payload = json.load(fh) or {}
        rows = payload.get("rows") or []

        crosswalk_map = {}
        if not args.no_crosswalk:
            crosswalk_map = load_crosswalk_map(
                crosswalk_path=crosswalk_path,
                display_by_norm=display_by_norm,
                year=year,
                contest_type=contest,
            )
        crosswalk_aliases, crosswalk_split_map = split_crosswalk_aliases(crosswalk_map)

        effective_aliases = dict(aliases)
        effective_aliases.update(crosswalk_aliases)

        remapped = 0
        split_rows = 0
        out_rows: list[dict] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            key = str(r.get("county") or "")
            if " - " not in key:
                out_rows.append(r)
                continue

            nk = norm(key)
            mapped_key = effective_aliases.get(nk, key)
            if mapped_key != key:
                remapped += 1

            split_targets = crosswalk_split_map.get(norm(mapped_key)) or crosswalk_split_map.get(nk) or []
            if split_targets:
                split_rows += len(split_targets)
                for t in split_targets:
                    rr = dict(r)
                    rr["county"] = t
                    out_rows.append(rr)
                continue

            rr = dict(r)
            rr["county"] = mapped_key
            out_rows.append(rr)

        merged_rows = merge_rows(out_rows, contest)
        payload["rows"] = merged_rows

        tmp = slice_path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        os.replace(tmp, slice_path)

        update_manifest(manifest_path, contest, year, len(merged_rows))
        return remapped, split_rows, len(merged_rows)

    if not args.all:
        contest = str(args.contest).strip()
        year = int(str(args.year).strip())
        slice_path = os.path.join(contests_dir, f"{contest}_{year}.json")
        if not os.path.exists(slice_path):
            raise SystemExit(f"Missing {slice_path}")
        remapped, split_rows, rows_out = process_one(slice_path, contest, year)
        print(f"Updated {slice_path}")
        print(f"Remapped rows: {remapped}")
        print(f"Expanded split rows: {split_rows}")
        print(f"Rows after merge: {rows_out}")
        return

    total_files = 0
    total_remapped = 0
    total_split_rows = 0
    for fn in sorted(os.listdir(contests_dir)):
        contest, year = parse_contest_and_year(fn)
        if not contest:
            continue
        slice_path = os.path.join(contests_dir, fn)
        remapped, split_rows, _ = process_one(slice_path, contest, year)
        total_files += 1
        total_remapped += remapped
        total_split_rows += split_rows

    print(f"Updated files: {total_files}")
    print(f"Remapped rows: {total_remapped}")
    print(f"Expanded split rows: {total_split_rows}")


if __name__ == "__main__":
    main()
