import argparse
import csv
import difflib
import os
import re
from collections import defaultdict

import shapefile


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 .\-]", "", str(s or ""))).strip().upper()


def precinct_part(key: str) -> str:
    s = str(key or "")
    if " - " in s:
        return s.split(" - ", 1)[1].strip()
    return s.strip()


def load_shapefile_by_county(shp_path: str) -> dict[str, list[str]]:
    r = shapefile.Reader(shp_path)
    field_names = [f[0] for f in r.fields[1:]]
    ix = {k: i for i, k in enumerate(field_names)}
    needed = {"COUNTY_NAM", "PCTNAME"}
    missing = sorted(needed - set(ix))
    if missing:
        raise SystemExit(f"Shapefile is missing expected fields: {', '.join(missing)}")

    by_county: dict[str, list[str]] = defaultdict(list)
    seen: set[str] = set()
    for rec in r.iterRecords():
        county = str(rec[ix["COUNTY_NAM"]] or "").strip()
        pct = str(rec[ix["PCTNAME"]] or "").strip()
        if not county or not pct:
            continue
        display = f"{county} - {pct}"
        nd = norm(display)
        if nd in seen:
            continue
        seen.add(nd)
        by_county[norm(county)].append(display)
    return by_county


def best_targets(target_key: str, county_candidates: list[str], limit: int = 3) -> list[tuple[str, float]]:
    tgt = norm(precinct_part(target_key))
    scored = []
    for cand in county_candidates:
        c = norm(precinct_part(cand))
        score = difflib.SequenceMatcher(None, tgt, c).ratio()
        scored.append((cand, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate alias and target-fix suggestions from crosswalk shapefile cross-reference output."
    )
    ap.add_argument(
        "--base",
        default=os.path.join(os.path.dirname(__file__), ".."),
        help="Repo base (default: scripts/..)",
    )
    ap.add_argument(
        "--crossref",
        default=os.path.join("scripts", "out", "crosswalk_shp_crossref_2024.csv"),
        help="Crossref CSV path relative to base",
    )
    ap.add_argument(
        "--shp",
        default=os.path.join("..", "Data", "sc_2024_gen_prec", "sc_2024_gen_no_splits_prec", "sc_2024_gen_no_splits_prec.shp"),
        help="Shapefile path relative to base (can be absolute)",
    )
    ap.add_argument(
        "--out-alias",
        default=os.path.join("scripts", "out", "alias_suggestions_from_crossref_2024.csv"),
        help="Alias suggestion output CSV path relative to base",
    )
    ap.add_argument(
        "--out-target",
        default=os.path.join("scripts", "out", "crosswalk_target_suggestions_2024.csv"),
        help="Crosswalk target suggestion output CSV path relative to base",
    )
    ap.add_argument("--min-score", default="0.60", help="Minimum top fuzzy score to emit target suggestion rows")
    ap.add_argument(
        "--emit-identity-aliases",
        action="store_true",
        help="Also emit alias_from -> alias_to rows even when source already equals shapefile display",
    )
    args = ap.parse_args()

    base = os.path.abspath(args.base)
    crossref_path = args.crossref if os.path.isabs(args.crossref) else os.path.join(base, args.crossref)
    shp_path = args.shp if os.path.isabs(args.shp) else os.path.abspath(os.path.join(base, args.shp))
    out_alias = args.out_alias if os.path.isabs(args.out_alias) else os.path.join(base, args.out_alias)
    out_target = args.out_target if os.path.isabs(args.out_target) else os.path.join(base, args.out_target)
    min_score = float(args.min_score)

    if not os.path.exists(crossref_path):
        raise SystemExit(f"Missing crossref CSV: {crossref_path}")
    if not os.path.exists(shp_path):
        raise SystemExit(f"Missing shapefile: {shp_path}")

    os.makedirs(os.path.dirname(out_alias), exist_ok=True)
    os.makedirs(os.path.dirname(out_target), exist_ok=True)

    by_county = load_shapefile_by_county(shp_path)

    with open(crossref_path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    alias_rows = []
    target_rows = []
    alias_seen = set()

    for r in rows:
        src_ok = (r.get("source_exists_in_shp") or "").strip().lower() == "yes"
        tgt_ok = (r.get("target_exists_in_shp") or "").strip().lower() == "yes"
        if not src_ok or tgt_ok:
            continue

        county = str(r.get("county") or "").strip()
        county_norm = norm(county)
        source_key = str(r.get("source_result_key") or "").strip()
        source_shp_display = str(r.get("source_shp_display") or "").strip()
        target_key = str(r.get("target_polygon_key") or "").strip()

        # Alias suggestion only when source string differs from canonical shp display.
        if source_key and source_shp_display and (
            args.emit_identity_aliases or norm(source_key) != norm(source_shp_display)
        ):
            k = (source_key, source_shp_display)
            if k not in alias_seen:
                alias_seen.add(k)
                alias_rows.append(
                    {
                        "county": county,
                        "alias_from": source_key,
                        "alias_to": source_shp_display,
                        "reason": "source_exists_in_shp_target_missing",
                    }
                )

        cands = by_county.get(county_norm, [])
        if not cands:
            continue
        top = best_targets(target_key, cands, limit=3)
        if not top:
            continue
        best_disp, best_score = top[0]
        if best_score < min_score:
            continue

        target_rows.append(
            {
                "county": county,
                "source_result_key": source_key,
                "current_target_polygon_key": target_key,
                "source_shp_display": source_shp_display,
                "suggested_target_1": top[0][0] if len(top) > 0 else "",
                "score_1": f"{top[0][1]:.4f}" if len(top) > 0 else "",
                "suggested_target_2": top[1][0] if len(top) > 1 else "",
                "score_2": f"{top[1][1]:.4f}" if len(top) > 1 else "",
                "suggested_target_3": top[2][0] if len(top) > 2 else "",
                "score_3": f"{top[2][1]:.4f}" if len(top) > 2 else "",
            }
        )

    with open(out_alias, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["county", "alias_from", "alias_to", "reason"])
        w.writeheader()
        w.writerows(alias_rows)

    with open(out_target, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=[
                "county",
                "source_result_key",
                "current_target_polygon_key",
                "source_shp_display",
                "suggested_target_1",
                "score_1",
                "suggested_target_2",
                "score_2",
                "suggested_target_3",
                "score_3",
            ],
        )
        w.writeheader()
        w.writerows(target_rows)

    print(f"Wrote {out_alias} ({len(alias_rows)} rows)")
    print(f"Wrote {out_target} ({len(target_rows)} rows, min_score={min_score:.2f})")


if __name__ == "__main__":
    main()
