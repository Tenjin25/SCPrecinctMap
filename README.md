# The Palmetto Explorer

The Palmetto Explorer is an interactive South Carolina election atlas built as a single-page web app.

Its user experience is intentionally inspired by the NC Election Atlas UI, then adapted for South Carolina boundaries, contests, and workflows.

## What This Project Does

- Renders South Carolina election results on an interactive map.
- Supports county, congressional, state house, and state senate views.
- Colors counties/districts by contest margin and provides quick contest switching.
- Supports precinct overlays for deeper local detail.
- Includes comparison modes (`Margins`, `Winners`, `Shift`, `Flips`) for election analysis.
- Includes mobile-first controls so the map remains usable on smaller touch devices.

## Current Data Snapshot

The committed generated data currently includes:

- 46 county polygons (`data/census/tl_2020_45_county20.geojson`)
- 2,268 precinct polygons (`data/Voting_Precincts.geojson`)
- 7 congressional districts (`data/tileset/sc_cd118_tileset.geojson`)
- 124 state house districts (`data/tileset/sc_state_house_2022_lines_tileset.geojson`)
- 46 state senate districts (`data/tileset/sc_state_senate_2022_lines_tileset.geojson`)
- 41 county/precinct contest slice files (`data/contests/manifest.json`)
- 143 district contest slice files (`data/district_contests/manifest.json`)

Coverage varies by office and year. Always check both manifests for the latest available slices.

## Stack

- Frontend app: `index.html` (single-file HTML/CSS/JS application)
- Map rendering: Mapbox GL JS
- Geometry helpers: Turf.js
- CSV parsing in-browser: Papa Parse
- Data build pipeline: `build_data.py`
- Build dependency: Python 3.x + `pyshp`

## Run Locally

1. Start a static web server from repo root:

```bash
python -m http.server 8080
```

2. Open:

```text
http://localhost:8080
```

Do not open `index.html` via `file://` because the app loads JSON/CSV resources with `fetch()`.

## Mapbox Token Setup

Mapbox access token wiring is in `CONFIG.mapboxToken` in `index.html`.

- Uses `window.MAPBOX_TOKEN` if present.
- Otherwise falls back to the token literal currently in `index.html`.

For production or forks, replace with your own token strategy before deployment.

## Project Layout

```text
SCPrecinctMap/
|-- index.html
|-- build_data.py
|-- README.md
|-- precinct_aliases.json
|-- scripts/
|   |-- elstats_search_to_openelections.py
|   |-- precinct_mismatch_report.py
|   |-- apply_precinct_aliases_to_slice.py
|   |-- crossref_crosswalk_with_shapefile.py
|   |-- generate_alias_suggestions_from_crossref.py
|   `-- spatial_overlap_precinct_suggestions.py
|-- Data/                       # source inputs (CSV/shapefile zips, scratch data)
`-- data/                       # generated outputs served by the app
    |-- census/
    |-- tileset/
    |-- contests/
    `-- district_contests/
```

## Data Pipeline

`build_data.py` is the main offline pipeline. It:

1. Builds county and precinct GeoJSON.
2. Builds congressional/state-house/state-senate district GeoJSON.
3. Aggregates precinct election CSV rows into county/precinct contest slices.
4. Builds district-level contest slices and manifests.

### Prerequisites

```bash
python -m venv .venv
.venv\Scripts\activate
pip install pyshp
```

### Build

```bash
python build_data.py
```

### Critical Join Contract

For county/precinct contest slices in `data/contests/*.json`:

- County summary rows use `county = "Richland"`
- Precinct rows use `county = "Richland - Forest Acres 1"`

The front-end split logic depends on the `" - "` separator.

## Common Maintenance Commands

Build all generated outputs:

```bash
python build_data.py
```

Check likely precinct name mismatches for a contest/year:

```powershell
py scripts/precinct_mismatch_report.py --contest president --year 2024
```

Convert SC Election Commission export into OpenElections-style format:

```powershell
py scripts/elstats_search_to_openelections.py --input Data/_tmpdata/in.csv --output Data/openelections-data-sc/2024/20241105__sc__general__precinct.csv
```

## Frontend Behavior Summary

- Views: `Counties`, `Congress`, `State House`, `State Senate`
- Analysis modes: `Margins`, `Winners`, `Shift`, `Flips`
- Core tools: contest search/select, precinct toggle, label toggle, color-accessibility toggle, fly-to search
- Shortcuts: `P` toggles precinct overlay, `L` toggles labels

## Mobile Notes

The current layout includes mobile-specific UI pieces, including:

- Responsive top controls and compact spacing
- Mobile top bar details toggle
- Thumb-reach quick action dock (`Controls` and `Search`)
- Map padding synchronization so overlays do not hide map context

Desktop layout remains available with the full side/control experience.

## Key Data and Config Files

- `index.html`: app UI, rendering logic, and `CONFIG`
- `build_data.py`: primary data build pipeline
- `data/contests/manifest.json`: available county/precinct contests
- `data/district_contests/manifest.json`: available district contest slices
- `precinct_aliases.json`: manual precinct name normalization overrides

## Deployment

This project is static-host friendly:

- GitHub Pages
- Netlify
- Vercel
- S3 + CloudFront
- Any static host that serves the repo root

No backend service is required.

## Attribution

- UI/interaction design baseline: NC Election Atlas (inspiration and interaction model)
- South Carolina adaptation and implementation: The Palmetto Explorer project
- Data sources include U.S. Census TIGER/Line geography files, OpenElections precinct CSVs, and South Carolina election exports transformed into OpenElections-compatible structure where needed

## Known Caveats

- Data availability differs by office/year. Some cycles are partial.
- Historical results may be shown on newer district boundaries depending on available boundary vintages.
- Precinct naming is not always one-to-one across sources; use `precinct_aliases.json` and helper scripts when needed.
- This repository currently has no explicit `LICENSE` file. Add one before broad reuse or redistribution.
