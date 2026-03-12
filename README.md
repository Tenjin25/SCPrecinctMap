# South Carolina Precinct Election Map

An interactive, single-page election results map for South Carolina built with **Mapbox GL JS**. The map supports four geographic views — counties, congressional districts, state house districts, and state senate districts — and renders precinct-level coloring at high zoom levels.

---

## Table of Contents

1. [Features](#features)
2. [Architecture Overview](#architecture-overview)
3. [Project Structure](#project-structure)
4. [Data Sources](#data-sources)
5. [Data Pipeline](#data-pipeline)
6. [Available Contests](#available-contests)
7. [Mapbox Setup](#mapbox-setup)
8. [Running Locally](#running-locally)
9. [Rebuilding Data](#rebuilding-data)
10. [Adding New Election Years](#adding-new-election-years)
11. [Precinct Aliases](#precinct-aliases)
12. [Helper Scripts](#helper-scripts)
13. [Known Limitations](#known-limitations)
14. [Deployment](#deployment)
15. [Key Files Reference](#key-files-reference)
16. [Recent Changes (March 2026)](#recent-changes-march-2026)

---

## Features

- **Four map views**: counties, congressional districts (CD-118), state house (124 districts), state senate (46 districts)
- **Precinct overlay**: individual precinct polygon coloring at zoom ≥ 8.5
- **20 county/precinct contest slices** covering general elections 2008–2024
- **10 district contest slices** for US House, State House, and State Senate races
- **Color scale**: 14-step red/blue gradient keyed to R–D margin percentage
- **Hover tooltip**: candidate names, vote totals, and margin for any hovered feature
- **Contest panel**: dropdown to switch contests; color-coded legend bar

---

## Recent Changes (March 2026)

- Synced county click behavior and selected-county results/trend presentation with NC map logic in `index.html`.
- Updated winner/lead labeling so summary text uses **candidate + margin** while lead text uses **party + margin** (`R+` / `D+`).
- Fixed county-level presidential margin handling to use contest JSON margin values in county detail and trend context.
- Added a minimize control next to Clear in the selected-results panel flow.
- Standardized candidate display cleanup to remove presidential running mates (for example 2020 now shows ticket leads instead of full tickets) across vote counter and candidate lookup paths.

---

## Architecture Overview

The project is split into two distinct phases:

```
Phase 1 – Offline data build (Python)
  TIGER shapefiles   ──► build_data.py ──► GeoJSON boundary files
  OpenElections CSVs ──────────────────► Contest JSON slices

Phase 2 – Runtime rendering (browser)
  GeoJSON + Contest JSONs ──► Mapbox GL JS ──► Interactive map
```

### Data build (offline, one-time or per election cycle)

`build_data.py` is the only build step. It reads shapefiles and election CSVs from `Data/` and writes everything the browser needs into `data/`. No GDAL, geopandas, or Node.js toolchain is required — the only dependency is `pyshp`.

### Front-end rendering (browser)

`index.html` is a self-contained single-page application. On load it:

1. Reads `CONFIG` at the top of the file for token, tileset URLs, and defaults.
2. Fetches `data/contests/manifest.json` and `data/district_contests/manifest.json` to build the contest dropdown.
3. Loads the requested contest slice (e.g. `data/contests/president_2024.json`) and calls `applyCountyContest()`.
4. `applyCountyContest()` iterates every row; rows without `" - "` in the `county` key color the **county fill layer**, rows with `" - "` are split to color the **precinct overlay layer**.
5. Switching the map view (counties / CD / state house / state senate) swaps which Mapbox layers are visible and re-applies the current contest data.

### Boundary delivery modes

| Mode | How to enable | Notes |
|---|---|---|
| **Local GeoJSON** (default) | Leave `url` fields empty in `CONFIG.tilesets` | Works immediately; fine for development |
| **Mapbox tilesets** | Populate `url` fields in `CONFIG.tilesets` | Faster at scale; requires Mapbox Studio upload |

---

## Project Structure

```
SCPrecinctMap/
├── index.html                  # Single-page app (Mapbox GL JS, all logic inline)
├── build_data.py               # Data pipeline – run once to generate all JSON
│
├── Data/                       # Source data (not served; input only)
│   ├── census/
│   │   ├── tl_2020_45_county20/        # SC county shapefile (46 features)
│   │   ├── tl_2020_45_vtd20/           # SC VTD/precinct shapefile (2268 features)
│   │   ├── tl_2022_45_cd118.zip        # Congressional districts (7 features)
│   │   ├── tl_2024_45_sldl.zip         # State House districts (124 features)
│   │   └── tl_2024_45_sldu.zip         # State Senate districts (46 features)
│   └── openelections-data-sc/          # OpenElections precinct CSVs by year
│       ├── 2008/20081104__sc__general__precinct.csv
│       ├── 2016/20161108__sc__general__precinct.csv
│       ├── 2018/20181106__sc__general__precinct.csv
│       ├── 2020/20201103__sc__general__precinct.csv
│       ├── 2022/20221108__sc__general__precinct.csv
│       └── 2024/20241105__sc__general__precinct.csv
│
└── data/                       # Generated output (served to the browser)
    ├── census/
    │   └── tl_2020_45_county20.geojson         # 46 county polygons
    ├── tileset/
    │   ├── sc_cd118_tileset.geojson             # 7 congressional districts
    │   ├── sc_state_house_2022_lines_tileset.geojson   # 124 house districts
    │   └── sc_state_senate_2022_lines_tileset.geojson  # 46 senate districts
    ├── Voting_Precincts.geojson                 # 2268 VTD polygons (with county_nam, precinct_norm)
    ├── precinct_centroids.geojson               # 2268 centroid points
    ├── contests/
    │   ├── manifest.json                        # 20-entry index of county/precinct slices
    │   └── <contest_type>_<year>.json           # one file per contest
    └── district_contests/
        ├── manifest.json                        # 10-entry index of district slices
        └── <scope>_<contest_type>_<year>.json   # one file per district contest
```

---

## Data Sources

| Dataset | Source | Notes |
|---|---|---|
| County boundaries | [TIGER/Line 2020](https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.html) – `tl_2020_45_county20` | FIPS 45, WGS84 |
| VTD/Precinct boundaries | TIGER/Line 2020 – `tl_2020_45_vtd20` | 2268 voting districts |
| Congressional districts | TIGER/Line 2022 – `tl_2022_45_cd118` | 118th Congress, 7 districts |
| State House districts | TIGER/Line 2024 – `tl_2024_45_sldl` | 124 districts, `SLDLST` key |
| State Senate districts | TIGER/Line 2024 – `tl_2024_45_sldu` | 46 districts, `SLDUST` key |
| Election results | [OpenElections](https://openelections.net/) SC precinct-level general election CSVs | 2008, 2016–2024 |

> **Precinct name matching**: OpenElections precinct labels match TIGER VTD `NAME20` field at ~99% after normalization. The ~1% unmatched entries are non-geographic buckets (FAILSAFE, PROVISIONAL, ABSENTEE, etc.) which are intentionally excluded from the precinct overlay.

If you still see **mismatched/missing precincts** (e.g., different punctuation, split/merged precincts, or typos), add a manual override in `precinct_aliases.json` and rebuild. You can generate a ranked mismatch report with:

```powershell
py scripts/precinct_mismatch_report.py --contest president --year 2024
```

---

## Data Pipeline

All GeoJSON and contest JSON files are generated by `build_data.py`. The pipeline requires only Python 3.11+ and `pyshp` — no GDAL or geopandas needed.

### Steps executed in order

| Step | Function | Output |
|---|---|---|
| 1 | `build_county_geojson()` | `data/census/tl_2020_45_county20.geojson` |
| 2 | `build_precinct_geojson()` | `data/Voting_Precincts.geojson`, `data/precinct_centroids.geojson` |
| 3 | `build_district_geojson()` | Three GeoJSONs in `data/tileset/` |
| 4 | `build_election_data()` | `data/contests/*.json` + `manifest.json` |
| 5 | `build_district_contests()` | `data/district_contests/*.json` + `manifest.json` |

### Contest slice schema (`data/contests/<contest_type>_<year>.json`)

```json
{
  "year": 2024,
  "contest_type": "president",
  "rows": [
    {
      "county": "Richland",
      "dem_votes": 98200,
      "rep_votes": 41000,
      "other_votes": 1500,
      "total_votes": 140700,
      "dem_candidate": "Kamala Harris",
      "rep_candidate": "Donald Trump",
      "margin": -57200,
      "margin_pct": -40.6534,
      "winner": "D",
      "color": "#08306b"
    },
    {
      "county": "Richland - Forest Acres 1",
      ...
    }
  ]
}
```

County-level rows come first (key = `"Richland"`). Precinct-level rows follow (key = `"Richland - Precinct Name"`). The JS uses the `" - "` separator to route each row to the correct map layer.

### District slice schema (`data/district_contests/<scope>_<contest_type>_<year>.json`)

```json
{
  "general": {
    "results": {
      "1": {
        "dem_votes": 180000,
        "rep_votes": 90000,
        "other_votes": 3000,
        "total_votes": 273000,
        "dem_candidate": "Joe Cunningham",
        "rep_candidate": "Nancy Mace",
        "margin": -90000,
        "margin_pct": -32.967,
        "winner": "D",
        "color": "#08519c"
      },
      "2": { ... }
    }
  },
  "meta": { "match_coverage_pct": 100 }
}
```

District keys are integer-string (no leading zeros), matching the `int(CD118FP)` / `int(SLDLST)` / `int(SLDUST)` values from the shapefiles.

### Color scale

Margin is calculated as `(rep_votes − dem_votes) / total_votes × 100`. Positive = Republican win.

| Range | Republican | Democratic |
|---|---|---|
| ≥ 40 % | `#67000d` | `#08306b` |
| ≥ 30 % | `#a50f15` | `#2171b5` |
| ≥ 20 % | `#cb181d` | `#4292c6` |
| ≥ 10 % | `#ef3b2c` | `#6baed6` |
| ≥ 5 %  | `#fc8a6a` | `#9ecae1` |
| < 5 %  | `#fcbba1` | `#c6dbef` |
| Tie    | `#f0f0f0` | — |

---

## Available Contests

### County / Precinct contests (`data/contests/`)

| Year | Contests |
|---|---|
| 2024 | President |
| 2022 | Governor, U.S. Senate, Attorney General, Secretary of State, State Treasurer, Comptroller General, Commissioner of Agriculture |
| 2020 | President, U.S. Senate |
| 2018 | Governor, Attorney General, Secretary of State, State Treasurer, Comptroller General, Commissioner of Agriculture |
| 2016 | President, U.S. Senate |
| 2008 | President, U.S. Senate *(~29 of 46 counties covered)* |

### District contests (`data/district_contests/`)

| Year | Scope | Districts |
|---|---|---|
| 2018 | Congressional (US House) | 7 |
| 2018 | State House | 123 |
| 2020 | Congressional (US House) | 7 |
| 2020 | State House | 124 |
| 2020 | State Senate | 46 |
| 2022 | Congressional (US House) | 7 |
| 2022 | State House | 124 |
| 2024 | Congressional (US House) | 7 |
| 2024 | State Senate | 46 |
| 2024 | State House | 2 *(partial — source CSV coverage)* |

---

## Mapbox Setup

The app can render boundaries either from **local GeoJSON** (default, works immediately) or from **Mapbox tilesets** (faster at scale, requires upload).

### Local GeoJSON mode (default)

`CONFIG.tilesets.enabled` is set to `true` but the `url` fields are empty strings — the app falls back to loading the local GeoJSON files under `data/`.

The app still needs a **Mapbox public token** for the basemap style (`mapbox://styles/mapbox/light-v11`), but the repo intentionally does **not** commit tokens.

Set it once per browser:

```js
localStorage.setItem('MAPBOX_TOKEN', 'pk.<your token>');
location.reload();
```

If you open the app without a token, it will show a prompt and save to `localStorage.MAPBOX_TOKEN`.

> **Security**: Never paste a live token into a public issue, chat message, or commit. If you accidentally expose one, rotate or revoke it immediately in the [Mapbox access tokens dashboard](https://account.mapbox.com/access-tokens/).

### Tileset mode (optional, better performance)

If you want to host the boundary layers as Mapbox tilesets:

1. Upload each GeoJSON to Mapbox Studio as a new tileset:
   - `data/census/tl_2020_45_county20.geojson` → source layer name `tl_2020_45_county20`
   - `data/tileset/sc_cd118_tileset.geojson` → source layer name `sc_cd118_tileset`
   - `data/tileset/sc_state_house_2022_lines_tileset.geojson` → source layer `sc_state_house_2022_lines_til`
   - `data/tileset/sc_state_senate_2022_lines_tileset.geojson` → source layer `sc_state_senate_2022_lines_ti`
   - `data/Voting_Precincts.geojson` *(large — use [Mapbox Tilesets CLI](https://github.com/mapbox/tilesets-cli) for this one)*

2. Copy the tileset IDs (format: `mapbox://yourusername/tilesetid`) into the `CONFIG.tilesets.sources` block in `index.html`:

```js
tilesets: {
  enabled: true,
  sources: {
    counties:     { url: 'mapbox://yourusername/your-counties-tileset',     sourceLayer: 'tl_2020_45_county20' },
    districts:    { url: 'mapbox://yourusername/your-cd118-tileset',        sourceLayer: 'sc_cd118_tileset' },
    state_house:  { url: 'mapbox://yourusername/your-house-tileset',        sourceLayer: 'sc_state_house_2022_lines_til' },
    state_senate: { url: 'mapbox://yourusername/your-senate-tileset',       sourceLayer: 'sc_state_senate_2022_lines_ti' },
  }
}
```

> **Note**: The Mapbox token in `index.html` is a public token scoped to this project. If you fork this repo, replace it with your own at `CONFIG.mapboxToken`.

---

## Running Locally

The app is a static single-page application — no build step or server framework is needed.

### Option 1: VS Code Live Server

Install the [Live Server extension](https://marketplace.visualstudio.com/items?itemName=ritwickdey.LiveServer), right-click `index.html` → **Open with Live Server**.

### Option 2: Python http.server

```bash
cd SCPrecinctMap
python -m http.server 8080
# open http://localhost:8080
```

### Option 3: Node serve

```bash
npx serve .
```

> **Do not** open `index.html` directly via `file://` — browsers block the `fetch()` calls for the data files under CORS restrictions.

---

## Rebuilding Data

### Prerequisites

```bash
# Inside the project directory
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux
pip install pyshp
```

### Run the pipeline

```bash
python build_data.py
```

Full output:

```
=== County GeoJSON ===
  wrote  data\census\tl_2020_45_county20.geojson
  46 county features

=== Precinct GeoJSON ===
  wrote  data\Voting_Precincts.geojson
  2268 precinct features

=== Precinct Centroids ===
  wrote  data\precinct_centroids.geojson
  2268 centroid points

=== District GeoJSON ===
  wrote  data\tileset\sc_cd118_tileset.geojson
  wrote  data\tileset\sc_state_house_2022_lines_tileset.geojson
  wrote  data\tileset\sc_state_senate_2022_lines_tileset.geojson

=== County/Precinct Contest JSONs ===
  ... (20 contest files)

=== District Contest JSONs ===
  ... (10 district files)

Build complete.
```

---

## Adding New Election Years

1. Download the OpenElections precinct CSV for the new general election from [openelections.net](https://openelections.net/results/#sc) and place it in `Data/openelections-data-sc/<year>/`.

2. Add an entry to `ELECTION_FILES` in `build_data.py`:

```python
ELECTION_FILES = {
    ...
    2026: os.path.join(DATA_SRC, 'openelections-data-sc', '2026',
                       '20261103__sc__general__precinct.csv'),
}
```

3. Re-run `python build_data.py`. The new contest slices will be generated automatically and added to both manifests.

> The pipeline automatically detects which offices appear in the CSV. County/precinct contest types are controlled by `OFFICE_MAP`; district contest types by `DISTRICT_OFFICE_MAP`.

---

## Precinct Aliases

OpenElections precinct names match TIGER `NAME20` field after normalization at ~99%. The remaining ~1% are either:

- **Non-geographic buckets** — FAILSAFE, PROVISIONAL, ABSENTEE, etc. (intentionally excluded).
- **True mismatches** — split/merged precincts, punctuation differences, or typographical discrepancies between OpenElections and the shapefile.

For true mismatches, add a manual override to `precinct_aliases.json`:

```json
{
  "AIKEN - BREEZY NO. 87": "AIKEN - BREEZY HILL",
  "ANDERSON - BARKERS CREEK-MCADAMS": "ANDERSON - BARKERS CREEK"
}
```

- **Key**: the normalized `"COUNTY - PRECINCT"` string as it appears in the OpenElections CSV.
- **Value**: the correct `"COUNTY - PRECINCT"` string matching the shapefile `NAME20` field.

Rebuild after editing: `python build_data.py`.

### Identifying mismatches

Use `scripts/precinct_mismatch_report.py` to rank unmatched precincts by closest fuzzy match:

```powershell
# Run from the project root
py scripts/precinct_mismatch_report.py --contest president --year 2024
```

The report shows each unmatched election row alongside the top-3 closest shapefile polygon names, making it easy to decide whether to add an alias or treat it as a non-geographic bucket.

---

## Helper Scripts

### `_inspect.py`

Ad-hoc inspection script used during development. Reads a sample of OpenElections CSV rows for selected years and offices to verify field formats (`district`, `county`, `precinct`) before adjusting the pipeline.

```powershell
py _inspect.py
```

### `scripts/precinct_mismatch_report.py`

Generates a ranked mismatch report comparing contest JSON precinct keys against the GeoJSON polygon set. Uses `difflib.SequenceMatcher` for fuzzy candidate suggestions.

```
usage: precinct_mismatch_report.py [-h] --contest CONTEST --year YEAR
                                   [--precincts PRECINCTS]
                                   [--aliases ALIASES]

options:
  --contest   Contest type (e.g. president, governor, us_senate)
  --year      Election year (e.g. 2024)
  --precincts Path to Voting_Precincts.geojson  (default: data/Voting_Precincts.geojson)
  --aliases   Path to precinct_aliases.json     (default: precinct_aliases.json)
```

### `scripts/elstats_search_to_openelections.py`

Converts SC Election Commission (`elstats`) precinct-level CSV exports — which use a different channel/row layout — into the OpenElections CSV format expected by `build_data.py`. Useful for election years not yet published by OpenElections.

```powershell
py scripts/elstats_search_to_openelections.py ^
    --input  Data/_tmpdata/20241105__sc__general__precinct__from_elstats.csv ^
    --output Data/openelections-data-sc/2024/20241105__sc__general__precinct.csv
```

Key behaviors:
- Maps voting channels (Early Voting, Absentee, Failsafe, etc.) to OpenElections column names.
- Normalizes office strings to match `OFFICE_MAP` in `build_data.py`.
- Drops non-candidate rows such as "Total Votes Cast" and "Overvotes/Undervotes".

---

## Known Limitations

| Limitation | Details |
|---|---|
| **2008 coverage** | Only ~29 of 46 counties have precinct-level data in OpenElections for 2008; the rest show county-level totals only. |
| **2024 State House** | The 2024 state house source CSV covers only 2 of 124 districts. Use 2022 State House data for full coverage. |
| **Precinct boundaries are 2020 vintage** | The VTD shapefile is `tl_2020_45_vtd20`. Precincts that were split, merged, or renamed after 2020 may not match OpenElections data for 2022+ elections without aliases. |
| **District boundaries reflect latest redistricting** | Congressional uses 118th Congress lines; State House/Senate use 2024 TIGER files. Results from cycles before redistricting are applied to current district lines. |
| **No runoff or primary data** | Only general election precinct CSVs are included. |
| **`file://` protocol blocked** | The app uses `fetch()` for data files, which browsers block under the `file://` protocol. Always serve via HTTP (see [Running Locally](#running-locally)). |

---

## Deployment

### GitHub Pages (static hosting)

Because `index.html` is a self-contained SPA with no server-side logic, it deploys directly to any static host.

1. Ensure `data/` (generated output) is committed to the repo.
2. In your GitHub repository settings → Pages → select **Branch: main**, **Folder: / (root)**.
3. GitHub Pages will serve `index.html` at `https://<username>.github.io/<repo>/`.
4. Set your Mapbox token via `localStorage` in the browser after the first visit (see [Mapbox Setup](#mapbox-setup)).

### Any other static host (Netlify, Vercel, S3, etc.)

Deploy the project root as-is. No build command is required; the output of `build_data.py` is pre-generated static JSON. The only runtime requirement is HTTPS or localhost (for `fetch()` CORS).

---

## Key Files Reference

| File | Purpose |
|---|---|
| [index.html](index.html) | Entire front-end app — Mapbox GL JS, CSS, and all rendering logic |
| [build_data.py](build_data.py) | Python data pipeline — shapefile → GeoJSON, CSV → contest JSON |
| [data/contests/manifest.json](data/contests/manifest.json) | Index of all county/precinct contest slices |
| [data/district_contests/manifest.json](data/district_contests/manifest.json) | Index of all district contest slices |
| [data/Voting_Precincts.geojson](data/Voting_Precincts.geojson) | 2268 SC VTD polygons with `precinct_norm` join key |
| [data/precinct_centroids.geojson](data/precinct_centroids.geojson) | Centroid points for label placement |
| [data/census/tl_2020_45_county20.geojson](data/census/tl_2020_45_county20.geojson) | 46 SC county polygons |
| [data/tileset/sc_cd118_tileset.geojson](data/tileset/sc_cd118_tileset.geojson) | 7 congressional district polygons |
| [data/tileset/sc_state_house_2022_lines_tileset.geojson](data/tileset/sc_state_house_2022_lines_tileset.geojson) | 124 state house district polygons |
| [data/tileset/sc_state_senate_2022_lines_tileset.geojson](data/tileset/sc_state_senate_2022_lines_tileset.geojson) | 46 state senate district polygons |
