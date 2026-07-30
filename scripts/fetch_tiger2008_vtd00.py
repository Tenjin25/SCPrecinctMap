#!/usr/bin/env python3
"""Download Census TIGER 2008 Census-2000 VTD shapefiles for South Carolina."""

from __future__ import annotations

import argparse
import os
import time
import urllib.error
import urllib.request

from fetch_tiger2007fe_vtd00 import COUNTIES


BASE_URL = "https://www2.census.gov/geo/tiger/TIGER2008/45_SOUTH_CAROLINA"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out-dir",
        default="work/crosswalk_inputs/tiger2008_vtd00",
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
        filename = f"tl_2008_{county_id}_vtd00.zip"
        destination = os.path.join(out_dir, filename)
        if os.path.exists(destination) and os.path.getsize(destination) > 0 and not args.force:
            reused += 1
            continue
        url = f"{BASE_URL}/{county_id}_{county_name}_County/{filename}"
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
