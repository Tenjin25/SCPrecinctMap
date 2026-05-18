#!/usr/bin/env python3
import csv
import os
import re
from collections import OrderedDict, defaultdict


CONFIDENCE_ORDER = {"low": 1, "medium": 2, "high": 3}


def norm(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 .\-]", "", str(value or ""))
    return re.sub(r"\s+", " ", cleaned).strip().upper()


def _confidence_ok(row_conf: str, min_confidence: str) -> bool:
    if not min_confidence:
        return True
    req = CONFIDENCE_ORDER.get(str(min_confidence).strip().lower())
    if req is None:
        return True
    cur = CONFIDENCE_ORDER.get(str(row_conf).strip().lower())
    if cur is None:
        return False
    return cur >= req


def _display_for_target(target: str, display_by_norm: dict[str, str] | None) -> str:
    n = norm(target)
    if not n:
        return str(target or "").strip()
    if display_by_norm:
        return display_by_norm.get(n, str(target or "").strip())
    return str(target or "").strip()


def load_crosswalk_mappings(
    crosswalk_csv_path: str,
    *,
    year: int | None = None,
    contest_type: str | None = None,
    min_confidence: str = "medium",
    allowed_statuses: set[str] | None = None,
    display_by_norm: dict[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, list[str]], dict[str, list[tuple[str, float]]]]:
    if not crosswalk_csv_path or not os.path.exists(crosswalk_csv_path):
        return {}, {}, {}

    statuses = {s.lower() for s in (allowed_statuses or {"approved"})}

    source_to_targets: dict[str, list[str]] = defaultdict(list)
    with open(crosswalk_csv_path, encoding="utf-8", newline="") as fh:
        rd = csv.DictReader(fh)
        for row in rd:
            if not isinstance(row, dict):
                continue

            row_status = str(row.get("status") or "").strip().lower()
            if statuses and row_status not in statuses:
                continue

            if year is not None:
                try:
                    row_year = int(str(row.get("year") or "").strip())
                except ValueError:
                    continue
                if row_year != int(year):
                    continue

            if contest_type:
                row_contest = str(row.get("contest_type") or "").strip().lower()
                if row_contest != str(contest_type).strip().lower():
                    continue

            if not _confidence_ok(str(row.get("confidence") or ""), min_confidence):
                continue

            src = str(row.get("source_result_key") or "").strip()
            tgt = str(row.get("target_polygon_key") or "").strip()
            if not src or not tgt:
                continue
            nsrc = norm(src)
            if not nsrc:
                continue
            source_to_targets[nsrc].append(tgt)

    aliases: dict[str, str] = {}
    splits: dict[str, list[str]] = {}
    weighted_splits: dict[str, list[tuple[str, float]]] = {}

    for source_norm, raw_targets in source_to_targets.items():
        deduped: list[str] = []
        seen_norm: set[str] = set()
        for t in raw_targets:
            nt = norm(t)
            if not nt or nt in seen_norm:
                continue
            seen_norm.add(nt)
            deduped.append(_display_for_target(t, display_by_norm))
        if not deduped:
            continue
        if len(deduped) == 1:
            aliases[source_norm] = deduped[0]
            continue
        splits[source_norm] = deduped
        w = 1.0 / float(len(deduped))
        weighted_splits[source_norm] = [(d, w) for d in deduped]

    ordered_aliases = OrderedDict(sorted(aliases.items(), key=lambda kv: kv[0]))
    ordered_splits = OrderedDict(sorted(splits.items(), key=lambda kv: kv[0]))
    ordered_weighted = OrderedDict(sorted(weighted_splits.items(), key=lambda kv: kv[0]))
    return dict(ordered_aliases), dict(ordered_splits), dict(ordered_weighted)
