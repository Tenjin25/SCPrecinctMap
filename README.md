# The Palmetto Explorer

The Palmetto Explorer is a single-page South Carolina election atlas built with Mapbox GL JS, Turf.js, and static JSON/CSV assets.

It supports county, congressional, state house, and state senate views, plus precinct overlays and multiple analysis modes.

## What You Can Do

- Switch map layers: `Counties`, `Congress`, `State House`, `State Senate`
- Switch analysis modes: `Margins`, `Winners`, `Shift`, `Flips`
- Search and fly to counties, districts, and precincts
- Click counties and districts to open details and zoom to bounds
- Toggle precinct overlays and label visibility
- Use hover tooltips and pinned vote counter summaries

## Recent Updates (March 2026)

- Added click-to-zoom on county selections.
- Added click-to-zoom on congressional, state house, and state senate selections.
- Added viewport precinct quick stats under the fly-to search UI.
- Improved map label legibility and halo behavior.
- Expanded precinct QA and alias tooling for statewide data cleanup.

## Data Snapshot (Current Repository State)

Generated files currently committed include:

- 46 county polygons (`data/census/tl_2020_45_county20.geojson`)
- 2,268 precinct polygons (`data/Voting_Precincts.geojson`)
- 7 congressional districts (`data/tileset/sc_cd118_tileset.geojson`)
- 124 state house districts (`data/tileset/sc_state_house_2022_lines_tileset.geojson`)
- 46 state senate districts (`data/tileset/sc_state_senate_2022_lines_tileset.geojson`)
- 41 county/precinct contest slices (`data/contests/manifest.json`)
- 143 district contest slices (`data/district_contests/manifest.json`)

Manifest year coverage currently spans 2006-2024. Coverage still varies by office and year.

## Stack

- Frontend app: `index.html` (single-file HTML/CSS/JS)
- Mapping: Mapbox GL JS
- Geometry utilities: Turf.js
- CSV parsing in browser: Papa Parse
- Offline build pipeline: `build_data.py`
- Python dependency for base build: `pyshp`

Optional helper scripts also use:

- `geopandas` (spatial overlap scripts)
- additional geospatial stack pulled by geopandas (`shapely`, `pyproj`, etc.)

## Quick Start (Local)

1. Start a static web server from repository root:

```bash
python -m http.server 8080
```

2. Open:

```text
http://localhost:8080
```

Do not open the app with `file://`. The map loads assets with `fetch()`, so it must be served over HTTP.

## Controls and Shortcuts

- `P`: toggle precinct overlay
- `L`: toggle labels
- `S`: toggle sidebar

Main controls:

- Contest search + contest dropdown
- Layer view toggle buttons
- Analysis mode buttons
- Accessibility color toggle
- Label toggle
- Precinct toggle

Click behavior:

- County click: opens county details and zooms to county bounds
- District click (all three district layers): opens district details and zooms to district bounds

## Configuration

Primary runtime config is in `index.html` (`CONFIG` object).

Important paths:

- `CONFIG.paths.contests_dir` -> `./data/contests`
- `CONFIG.paths.district_contests_dir` -> `./data/district_contests`
- county/district geometry and demographics paths

Mapbox token:

- Reads `window.MAPBOX_TOKEN` when present
- Falls back to the literal token in `index.html`

For production or forks, replace this with your own token strategy.

## Project Layout

```text
SCPrecinctMap/
|-- index.html
|-- README.md
|-- build_data.py
|-- precinct_aliases.json
|-- scripts/
|   |-- apply_precinct_aliases_to_slice.py
|   |-- backfill_missing_contest_rows_from_oe_csv.py
|   |-- build_statewide_contest_mismatch_report.py
|   |-- build_vtd10_to_vtd20_overlap_csv.py
|   |-- crossref_crosswalk_with_shapefile.py
|   |-- elstats_search_to_openelections.py
|   |-- generate_alias_suggestions_from_crossref.py
|   |-- precinct_mismatch_report.py
|   `-- spatial_overlap_precinct_suggestions.py
|-- Data/    # source files, raw exports, scratch inputs
`-- data/    # generated assets served by the app
    |-- census/
    |-- tileset/
    |-- contests/
    `-- district_contests/
```

## Build Pipeline

`build_data.py` is the main offline pipeline. It can run partial steps depending on which inputs exist:

1. Build county GeoJSON
2. Build precinct GeoJSON + centroids
3. Build district GeoJSON outputs
4. Build county/precinct contest slices + manifest
5. Build district contest slices + manifest
6. Build statewide-by-district slices from existing slice data

### Base Prerequisites

```bash
python -m venv .venv
.venv\Scripts\activate
pip install pyshp
```

### Build Command

```bash
python build_data.py
```

### Critical Join Contract

For `data/contests/*.json` rows:

- County rollup row key: `county = "Richland"`
- Precinct row key: `county = "Richland - Forest Acres 1"`

Front-end split logic depends on the `" - "` separator.

## Maintenance Commands

Build generated outputs:

```bash
python build_data.py
```

Apply precinct aliases and split rules across all contest slices:

```powershell
python scripts/apply_precinct_aliases_to_slice.py --all
```

Check likely precinct mismatches for a contest/year:

```powershell
python scripts/precinct_mismatch_report.py --contest president --year 2024
```

Build statewide mismatch reports and county rollups:

```powershell
python scripts/build_statewide_contest_mismatch_report.py --out-prefix contest_mismatch_summary_post_alias_pass
```

Build a VTD10 to VTD20 overlap crosswalk (example counties):

```powershell
python scripts/build_vtd10_to_vtd20_overlap_csv.py --source Data/tl_2012_45_vtd10.zip --target data/Voting_Precincts.geojson --counties "Spartanburg,Lancaster" --out scripts/out/vtd10_to_vtd20_overlap_spartanburg_lancaster.csv
```

Backfill missing rows from OpenElections CSVs:

```powershell
python scripts/backfill_missing_contest_rows_from_oe_csv.py --year 2022 --contest governor --contest us_senate --mismatch-csv scripts/out/contest_mismatch_missing_polygons_post_alias_pass.csv
```

Convert SC Election Commission exports into OpenElections-like precinct CSV:

```powershell
python scripts/elstats_search_to_openelections.py --input Data/_tmpdata/in.csv --output Data/openelections-data-sc/2024/20241105__sc__general__precinct.csv
```

## Troubleshooting

### Contest dropdown does not populate

Checklist:

1. Confirm you are serving over HTTP (`python -m http.server`), not `file://`.
2. In browser devtools Network tab, verify these return `200`:
   - `data/contests/manifest.json`
   - `data/district_contests/manifest.json`
3. Confirm files exist at those paths in your local checkout.
4. Hard refresh after data rebuild (`Ctrl+F5`).

### Console shows `ERR_BLOCKED_BY_CLIENT` for `events.mapbox.com`

This is usually an ad/privacy extension blocking Mapbox telemetry requests. It is typically non-fatal for map rendering.

### `Permissions-Policy` warnings in console

These are commonly third-party/browser policy warnings and are usually not the root cause of app logic issues.

### Script dependency errors

If helper scripts fail with missing modules, install missing Python packages in your virtualenv (for example `pyshp` or `geopandas` depending on script).

## Deployment

This is a static app and can be hosted on:

- GitHub Pages
- Netlify
- Vercel
- S3 + CloudFront
- any static HTTP host

No backend service is required.

## Attribution

- Interaction model inspiration: NC Election Atlas
- South Carolina adaptation and implementation: The Palmetto Explorer project
- Data sources: U.S. Census TIGER/Line geography, OpenElections precinct CSVs, and SC election exports transformed into OpenElections-compatible structures

## Caveats

- Contest availability differs by office/year.
- Historical elections may be shown on newer district lines where needed.
- Precinct naming is not fully standardized across all source systems.
- The repository currently has no explicit `LICENSE` file.
