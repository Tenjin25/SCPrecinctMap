"""Keep unchanged state-house districts identical across line vintages."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LINES_2022 = ROOT / "data" / "tileset" / "sc_state_house_2022_census.geojson"
LINES_2024 = ROOT / "data" / "tileset" / "sc_state_house_2024_lines_tileset.geojson"
UNCHANGED_DISTRICTS = {"082"}


def district_id(feature: dict) -> str:
    return str(feature.get("properties", {}).get("SLDLST", "")).zfill(3)


def main() -> None:
    with LINES_2022.open(encoding="utf-8") as handle:
        lines_2022 = json.load(handle)
    with LINES_2024.open(encoding="utf-8") as handle:
        lines_2024 = json.load(handle)

    canonical = {
        district_id(feature): feature["geometry"]
        for feature in lines_2024.get("features", [])
        if district_id(feature) in UNCHANGED_DISTRICTS
    }
    if UNCHANGED_DISTRICTS - canonical.keys():
        raise RuntimeError("Unchanged district missing from 2024 geometry")

    updated = set()
    for feature in lines_2022.get("features", []):
        district = district_id(feature)
        if district in canonical:
            feature["geometry"] = canonical[district]
            updated.add(district)
    if UNCHANGED_DISTRICTS - updated:
        raise RuntimeError("Unchanged district missing from 2022 geometry")

    with LINES_2022.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(lines_2022, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")


if __name__ == "__main__":
    main()
