"""Apply the certified 2026 district recount totals after precinct aggregation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
RECOUNT_FILE = REPO_ROOT / "data/2026_certified_recounts.json"
TARGET_FILES = {
    "U.S. House": {"congressional_us_house_2026.json"},
    "State House": {
        "state_house_state_house_2026.json",
        "state_house_state_house_2026_2024_lines.json",
    },
}


def load_certification() -> dict:
    return json.loads(RECOUNT_FILE.read_text(encoding="utf-8"))


def apply_certified_recounts(
    filename: str, payload: dict, margin_color: Callable[[float], str]
) -> list[str]:
    """Update only direct race rows; precinct-derived overlays remain untouched."""
    certification = load_certification()
    results = (payload.get("general") or {}).get("results") or {}
    applied = []
    for recount in certification["certified_recounts"]:
        if filename not in TARGET_FILES[recount["office"]]:
            continue
        district = recount["district"]
        if district not in results:
            raise ValueError(f"{filename}: missing certified recount district {district}")
        row = results[district]
        dem = int(recount["dem_votes"])
        rep = int(recount["rep_votes"])
        other = int(recount["other_votes"])
        total = dem + rep + other
        margin = rep - dem
        margin_pct = round(margin / total * 100, 4) if total else 0
        row.update({
            "dem_votes": dem,
            "rep_votes": rep,
            "other_votes": other,
            "total_votes": total,
            "margin": margin,
            "margin_pct": margin_pct,
            "winner": "R" if margin > 0 else ("D" if margin < 0 else "T"),
            "color": margin_color(margin_pct),
        })
        applied.append(district)
    return applied
