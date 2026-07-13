"""Apply explicit House result composition and line-version invariants."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTESTS = ROOT / "data" / "contests"
DISTRICTS = ROOT / "data" / "district_contests"
ROOT_PRESIDENT = DISTRICTS / "state_house_president_2024.json"
LINES_2022_DIR = DISTRICTS / "state_house_2022_lines"
LINES_2024_DIR = DISTRICTS / "state_house_2024_lines"
LINES_2024_PRESIDENT = LINES_2024_DIR / "state_house_president_2024_2024_lines.json"
CHANGED_DISTRICTS = {"52", "54", "55", "57", "59", "70", "90", "91", "93", "95", "97", "105"}
UNCHANGED_DISTRICTS = {str(district) for district in range(1, 125)} - CHANGED_DISTRICTS


def result_map(payload: dict) -> dict:
    return payload["general"]["results"]


def read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: dict) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main() -> None:
    with (CONTESTS / "president_2024.json").open(encoding="utf-8") as handle:
        precinct_rows = json.load(handle)["rows"]
    root_president = read_json(ROOT_PRESIDENT)
    lines_2024_president = read_json(LINES_2024_PRESIDENT)

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

    result_map(root_president)["40"] = hd40
    result_map(lines_2024_president)["40"] = hd40
    write_json(ROOT_PRESIDENT, root_president)
    write_json(LINES_2024_PRESIDENT, lines_2024_president)

    synced_files = 0
    synced_records = 0
    for path_2024 in sorted(LINES_2024_DIR.glob("state_house_*_2024_lines.json")):
        path_2022 = LINES_2022_DIR / path_2024.name.replace("_2024_lines.json", "_2022_lines.json")
        if not path_2022.exists():
            continue
        payload_2024 = read_json(path_2024)
        payload_2022 = read_json(path_2022)
        results_2024 = result_map(payload_2024)
        results_2022 = result_map(payload_2022)
        for district in UNCHANGED_DISTRICTS:
            if district not in results_2024 or district not in results_2022:
                raise RuntimeError(f"District {district} missing from {path_2024.name} or {path_2022.name}")
            if results_2022[district] != results_2024[district]:
                results_2022[district] = results_2024[district]
                synced_records += 1
        write_json(path_2022, payload_2022)
        synced_files += 1

    print(f"Synchronized {synced_records} unchanged-district records across {synced_files} files")


if __name__ == "__main__":
    main()
