"""Apply explicit House result composition and line-version invariants."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTESTS = ROOT / "data" / "contests"
DISTRICTS = ROOT / "data" / "district_contests"
CANONICAL = DISTRICTS / "state_house_president_2024.json"
LINES_2022 = DISTRICTS / "state_house_2022_lines" / "state_house_president_2024_2022_lines.json"


def result_map(payload: dict) -> dict:
    return payload["general"]["results"]


def main() -> None:
    with (CONTESTS / "president_2024.json").open(encoding="utf-8") as handle:
        precinct_rows = json.load(handle)["rows"]
    with CANONICAL.open(encoding="utf-8") as handle:
        canonical = json.load(handle)
    with LINES_2022.open(encoding="utf-8") as handle:
        lines_2022 = json.load(handle)

    by_name = {row.get("county"): row for row in precinct_rows}
    newberry = by_name["Newberry"]
    dreher = by_name["Lexington - Dreher Island"]
    dem_votes = newberry["dem_votes"] + dreher["dem_votes"]
    rep_votes = newberry["rep_votes"] + dreher["rep_votes"]
    other_votes = newberry["other_votes"] + dreher["other_votes"]
    total_votes = dem_votes + rep_votes + other_votes
    margin = rep_votes - dem_votes
    hd40 = {
        "dem_votes": dem_votes,
        "rep_votes": rep_votes,
        "other_votes": other_votes,
        "total_votes": total_votes,
        "dem_candidate": newberry["dem_candidate"],
        "rep_candidate": newberry["rep_candidate"],
        "margin": margin,
        "margin_pct": round(margin / total_votes * 100, 4),
        "winner": "R" if margin > 0 else ("D" if margin < 0 else "T"),
        "color": "#a50f15",
    }

    canonical_results = result_map(canonical)
    lines_2022_results = result_map(lines_2022)
    canonical_results["40"] = hd40
    lines_2022_results["40"] = hd40
    lines_2022_results["82"] = canonical_results["82"]

    for path, payload in ((CANONICAL, canonical), (LINES_2022, lines_2022)):
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")


if __name__ == "__main__":
    main()
