import argparse
import csv
import os
import re
from collections import Counter

import shapefile


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 .\-]", "", str(s or ""))).strip().upper()


def to_int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0


def load_shapefile_lookup(shp_path: str) -> dict[str, dict]:
    r = shapefile.Reader(shp_path)
    field_names = [f[0] for f in r.fields[1:]]
    ix = {k: i for i, k in enumerate(field_names)}

    needed = {"COUNTY_NAM", "PCTNAME", "G24PREDHAR", "G24PRERTRU"}
    missing = sorted(needed - set(ix))
    if missing:
        raise SystemExit(f"Shapefile is missing expected fields: {', '.join(missing)}")

    # Sum every presidential column except DEM/REP as "other".
    pres_cols = [f for f in field_names if f.startswith("G24PRE")]
    other_cols = [f for f in pres_cols if f not in {"G24PREDHAR", "G24PRERTRU"}]

    out: dict[str, dict] = {}
    dup = Counter()
    for rec in r.iterRecords():
        county = str(rec[ix["COUNTY_NAM"]] or "").strip()
        pct = str(rec[ix["PCTNAME"]] or "").strip()
        if not county or not pct:
            continue

        key_norm = norm(f"{county} - {pct}")
        dem = to_int(rec[ix["G24PREDHAR"]])
        rep = to_int(rec[ix["G24PRERTRU"]])
        other = sum(to_int(rec[ix[c]]) for c in other_cols)
        total = dem + rep + other

        if key_norm in out:
            dup[key_norm] += 1
            out[key_norm]["dem"] += dem
            out[key_norm]["rep"] += rep
            out[key_norm]["other"] += other
            out[key_norm]["total"] += total
        else:
            out[key_norm] = {
                "display": f"{county} - {pct}",
                "dem": dem,
                "rep": rep,
                "other": other,
                "total": total,
            }

    if dup:
        print(f"Warning: {len(dup)} duplicate shapefile keys were aggregated.")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Cross-reference precinct_crosswalk CSV with SC 2024 no-splits precinct shapefile keys."
    )
    ap.add_argument(
        "--base",
        default=os.path.join(os.path.dirname(__file__), ".."),
        help="Repo base (default: scripts/..)",
    )
    ap.add_argument(
        "--crosswalk",
        default="precinct_crosswalk_2024.csv",
        help="Crosswalk CSV path relative to base",
    )
    ap.add_argument(
        "--shp",
        default=os.path.join("..", "Data", "sc_2024_gen_prec", "sc_2024_gen_no_splits_prec", "sc_2024_gen_no_splits_prec.shp"),
        help="Shapefile path relative to base (can be absolute)",
    )
    ap.add_argument(
        "--out",
        default=os.path.join("scripts", "out", "crosswalk_shp_crossref_2024.csv"),
        help="Output CSV path relative to base",
    )
    args = ap.parse_args()

    base = os.path.abspath(args.base)
    crosswalk_path = args.crosswalk if os.path.isabs(args.crosswalk) else os.path.join(base, args.crosswalk)
    shp_path = args.shp if os.path.isabs(args.shp) else os.path.abspath(os.path.join(base, args.shp))
    out_path = args.out if os.path.isabs(args.out) else os.path.join(base, args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    if not os.path.exists(crosswalk_path):
        raise SystemExit(f"Missing crosswalk CSV: {crosswalk_path}")
    if not os.path.exists(shp_path):
        raise SystemExit(f"Missing shapefile: {shp_path}")

    shp_lookup = load_shapefile_lookup(shp_path)

    with open(crosswalk_path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
        fieldnames = list(rows[0].keys()) if rows else []

    extra_cols = [
        "source_norm",
        "target_norm",
        "source_exists_in_shp",
        "target_exists_in_shp",
        "source_shp_display",
        "target_shp_display",
        "source_shp_dem",
        "source_shp_rep",
        "source_shp_other",
        "source_shp_total",
        "target_shp_dem",
        "target_shp_rep",
        "target_shp_other",
        "target_shp_total",
    ]
    out_fields = fieldnames + [c for c in extra_cols if c not in fieldnames]

    source_hits = 0
    target_hits = 0
    out_rows = []
    for row in rows:
        source_norm = norm(row.get("source_result_key", ""))
        target_norm = norm(row.get("target_polygon_key", ""))
        src = shp_lookup.get(source_norm)
        tgt = shp_lookup.get(target_norm)

        row["source_norm"] = source_norm
        row["target_norm"] = target_norm
        row["source_exists_in_shp"] = "yes" if bool(src) else "no"
        row["target_exists_in_shp"] = "yes" if bool(tgt) else "no"
        row["source_shp_display"] = (src or {}).get("display", "")
        row["target_shp_display"] = (tgt or {}).get("display", "")
        row["source_shp_dem"] = (src or {}).get("dem", "")
        row["source_shp_rep"] = (src or {}).get("rep", "")
        row["source_shp_other"] = (src or {}).get("other", "")
        row["source_shp_total"] = (src or {}).get("total", "")
        row["target_shp_dem"] = (tgt or {}).get("dem", "")
        row["target_shp_rep"] = (tgt or {}).get("rep", "")
        row["target_shp_other"] = (tgt or {}).get("other", "")
        row["target_shp_total"] = (tgt or {}).get("total", "")

        if src:
            source_hits += 1
        if tgt:
            target_hits += 1
        out_rows.append(row)

    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=out_fields)
        w.writeheader()
        w.writerows(out_rows)

    print(f"Wrote {out_path}")
    print(f"Rows: {len(out_rows)}")
    print(f"Source keys found in shapefile: {source_hits}/{len(out_rows)}")
    print(f"Target keys found in shapefile: {target_hits}/{len(out_rows)}")


if __name__ == "__main__":
    main()
