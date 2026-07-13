#!/usr/bin/env python3
import argparse
import csv
import json
import os
import re
from collections import Counter


NON_GEO = {
    "ABSENTEE",
    "EMERGENCY",
    "FAILSAFE",
    "PROVISIONAL",
    "FAILSAFE PROVISIONAL",
}


def norm(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 .\-]", "", str(value or ""))
    return re.sub(r"\s+", " ", cleaned).strip().upper()


def load_official_2008(path: str) -> set[str]:
    if not path or not os.path.exists(path):
        return set()
    out: set[str] = set()
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            county = str(row.get("county") or "").strip()
            precinct = str(row.get("precinct") or "").strip()
            if not county or not precinct:
                continue
            if norm(precinct) in NON_GEO:
                continue
            out.add(norm(f"{county} - {precinct}"))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Build friendly legacy result-name weighted splits via reviewed VTD00 bridge candidates.")
    ap.add_argument("--base", default=".", help="Repository root")
    ap.add_argument("--bridge", default="scripts/out/vtd00_name_bridge_candidates_2006_2008.csv")
    ap.add_argument("--vtd00-chain-weights", default="scripts/out/vtd00_to_vtd10_to_vtd20_vote_weight_splits.json")
    ap.add_argument("--official-2008", default="scripts/out/scvotes_enr_precinct_names_2008.csv")
    ap.add_argument("--out-json", default="scripts/out/legacy_name_to_vtd20_vote_weight_splits.json")
    ap.add_argument("--out-csv", default="scripts/out/legacy_name_bridge_summary.csv")
    ap.add_argument("--include-review", action="store_true", help="Also emit review-confidence rows into the JSON weights")
    args = ap.parse_args()

    base = os.path.abspath(args.base)
    with open(os.path.join(base, args.vtd00_chain_weights), encoding="utf-8") as fh:
        vtd00_weights = json.load(fh) or {}

    official_2008 = load_official_2008(os.path.join(base, args.official_2008))

    weighted: dict[str, dict[str, float]] = {
        "_comment": "Auto-generated friendly legacy result-name weights via VTD00 bridge candidates.",
        "_source_bridge": os.path.abspath(os.path.join(base, args.bridge)),
        "_source_vtd00_chain_weights": os.path.abspath(os.path.join(base, args.vtd00_chain_weights)),
        "_include_review": bool(args.include_review),
    }
    summary_rows: list[dict] = []
    counts = Counter()

    with open(os.path.join(base, args.bridge), encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("decision") not in {"top", "review"}:
                continue
            result_key = str(row.get("result_key_examples") or row.get("result_key_norm") or "").split(" | ")[0].strip()
            vtd00_key = str(row.get("candidate_vtd00_key") or "").strip()
            confidence = str(row.get("confidence") or "").strip()
            if not result_key or not vtd00_key:
                continue
            source_weights = vtd00_weights.get(vtd00_key)
            official = norm(result_key) in official_2008
            emit = confidence in {"high", "medium"} or (args.include_review and confidence == "review")
            if emit and isinstance(source_weights, dict) and source_weights:
                weighted[result_key] = source_weights
                status = "emitted"
            elif not source_weights:
                status = "missing_vtd00_weight"
            else:
                status = "held_for_review"

            counts[(confidence, status)] += 1
            summary_rows.append({
                "result_key": result_key,
                "candidate_vtd00_key": vtd00_key,
                "confidence": confidence,
                "decision": row.get("decision") or "",
                "official_2008_name": "yes" if official else "no",
                "share_of_vtd00": row.get("share_of_vtd00") or "",
                "share_of_vtd10_proxy": row.get("share_of_vtd10_proxy") or "",
                "targets": len(source_weights) if isinstance(source_weights, dict) else 0,
                "status": status,
            })

    os.makedirs(os.path.dirname(os.path.join(base, args.out_json)), exist_ok=True)
    with open(os.path.join(base, args.out_json), "w", encoding="utf-8", newline="") as fh:
        json.dump(weighted, fh, indent=2)
        fh.write("\n")

    with open(os.path.join(base, args.out_csv), "w", encoding="utf-8", newline="") as fh:
        fieldnames = [
            "result_key",
            "candidate_vtd00_key",
            "confidence",
            "decision",
            "official_2008_name",
            "share_of_vtd00",
            "share_of_vtd10_proxy",
            "targets",
            "status",
        ]
        wr = csv.DictWriter(fh, fieldnames=fieldnames)
        wr.writeheader()
        wr.writerows(summary_rows)

    emitted = sum(1 for k in weighted if not k.startswith("_"))
    print(f"wrote {args.out_json} ({emitted} weighted friendly names)")
    print(f"wrote {args.out_csv} ({len(summary_rows)} bridge summary rows)")
    for key, value in sorted(counts.items()):
        print(f"{key[0]} {key[1]}: {value}")


if __name__ == "__main__":
    main()
