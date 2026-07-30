#!/usr/bin/env python3
"""Download Census TIGER 2007 FE Census-2000 VTD shapefiles for South Carolina."""

from __future__ import annotations

import argparse
import os
import time
import urllib.error
import urllib.request


COUNTIES = {
    "001": "Abbeville",
    "003": "Aiken",
    "005": "Allendale",
    "007": "Anderson",
    "009": "Bamberg",
    "011": "Barnwell",
    "013": "Beaufort",
    "015": "Berkeley",
    "017": "Calhoun",
    "019": "Charleston",
    "021": "Cherokee",
    "023": "Chester",
    "025": "Chesterfield",
    "027": "Clarendon",
    "029": "Colleton",
    "031": "Darlington",
    "033": "Dillon",
    "035": "Dorchester",
    "037": "Edgefield",
    "039": "Fairfield",
    "041": "Florence",
    "043": "Georgetown",
    "045": "Greenville",
    "047": "Greenwood",
    "049": "Hampton",
    "051": "Horry",
    "053": "Jasper",
    "055": "Kershaw",
    "057": "Lancaster",
    "059": "Laurens",
    "061": "Lee",
    "063": "Lexington",
    "065": "McCormick",
    "067": "Marion",
    "069": "Marlboro",
    "071": "Newberry",
    "073": "Oconee",
    "075": "Orangeburg",
    "077": "Pickens",
    "079": "Richland",
    "081": "Saluda",
    "083": "Spartanburg",
    "085": "Sumter",
    "087": "Union",
    "089": "Williamsburg",
    "091": "York",
}

BASE_URL = "https://www2.census.gov/geo/tiger/TIGER2007FE/45_SOUTH_CAROLINA"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out-dir",
        default="work/crosswalk_inputs/tiger2007fe_vtd00",
        help="Directory for the 46 county VTD00 zip files",
    )
    parser.add_argument("--force", action="store_true", help="Redownload existing files")
    args = parser.parse_args()

    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)
    downloaded = 0
    reused = 0
    for county_fips, county_name in COUNTIES.items():
        county_id = f"45{county_fips}"
        filename = f"fe_2007_{county_id}_vtd00.zip"
        destination = os.path.join(out_dir, filename)
        if os.path.exists(destination) and os.path.getsize(destination) > 0 and not args.force:
            reused += 1
            continue
        url = f"{BASE_URL}/{county_id}_{county_name}/{filename}"
        print(f"download {url}")
        request = urllib.request.Request(url, headers={"User-Agent": "SCPrecinctMap/1.0"})
        for attempt, delay in enumerate((0, 2, 5, 15), start=1):
            if delay:
                time.sleep(delay)
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    with open(destination, "wb") as output:
                        output.write(response.read())
                break
            except urllib.error.HTTPError as exc:
                if exc.code != 429 or attempt == 4:
                    raise
        time.sleep(0.2)
        downloaded += 1

    print(f"ready: {len(COUNTIES)} files ({downloaded} downloaded, {reused} reused) -> {out_dir}")


if __name__ == "__main__":
    main()
