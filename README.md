# The Palmetto Explorer (successor to SCPrecinctMap)

The Palmetto Explorer is an interactive South Carolina election atlas built as a single-page web app.
It is the successor project to the original SCPrecinctMap release.

Its user experience is intentionally inspired by the NC Election Atlas UI, then adapted for South Carolina boundaries, contests, and workflows.

## Recent Updates (April 2026)

- **County focus panel teardown + facelift (selected-county experience):**
  - Rebuilt the selected-county hierarchy to read like a premium election desk:
    1) **At a glance** (winner + margin + contest/year)
    2) One dominant summary card with vote-share bar + key context
    3) **Why it votes this way** (short causal explainer)
    4) Confidence + statewide comparison + supporting facts (subordinate)
    5) Deep detail (trajectory/census/trends/buckets) behind a single expandable section
  - **Placement + layout parity with `NCMap.html`:** the county explainer now lives in the always-on right-side focus panel (vote counter), tucked into the `Trend` block (not a separate sidebar), so the focus card → trend rhythm matches the NC layout while still showing the editorial context immediately.
  - Added a plain-English **county archetype system** (region membership + growth context + competitiveness) to keep the story readable.
    - Examples: “Charleston-area growth county”, “Grand Strand tourism & retiree county”, “Fast-growing GOP exurb”, “Black Belt Democratic base”.
    - The archetype is *not* a decorative badge; it is used to drive the “Why it votes this way” framing.
  - Added a restrained **confidence meter** (Low / Medium / High) based on:
    - margin size (bigger margin → higher confidence)
    - recent movement and flips (big shift or a recent flip → lower confidence)
    - multi-cycle volatility (after trend history loads, repeated flips reduce confidence further)
  - Added an immediate **Compared with South Carolina** line so the county is legible in statewide context within ~3 seconds.
  - Reduced cognitive load by collapsing deeper material (vote breakdown, trajectory snapshot, trend history, census insight, non-geographic buckets) into one expandable “deep dive” section.
  - Styling goal: calmer, sharper, more editorial, less “stacked sections competing for attention”.

## Recent Updates (March 2026)

- Added statewide precinct QA workflow for alias-driven and overlap-driven fixes across years.
- Added county click-to-zoom on `county-fill` selection.
- Added viewport quick stats (`Viewing N precincts`) under the fly-to search UI.
- Improved centroid readability in dense areas with zoom-based radius scaling.
- Improved label legibility with stronger halos, including county and district label layers.
- Added county trajectory callouts with horizontal trend arrows (Democratic shifts point left; Republican shifts point right).
- Added County Census Insight cards using U.S. Census county population estimates (`data/CO-EST2025-POP-45.csv`, March 2026 release).
- Added `Census Check` cards that connect Census growth since 2020 to election movement (reinforcing vs realigning vs mixed), with compact evidence lines, flip callouts, and a confidence tag.
- Added utility scripts for statewide mismatch rollups, VTD10->VTD20 overlap exports, and backfills from OpenElections CSVs.

## What This Project Does

- Renders South Carolina election results on an interactive map.
- Supports county, congressional, state house, and state senate views.
- Colors counties/districts by contest margin and provides quick contest switching.
- Supports precinct overlays for deeper local detail.
- Includes comparison modes (`Margins`, `Winners`, `Shift`, `Flips`) for election analysis.
- Includes mobile-first controls so the map remains usable on smaller touch devices.

## County Trajectory and Census Insights

When you click a county, the right-side focus panel can show three related interpretation cards (in this order):

- **Trajectory:** A political trend summary based on election results across cycles. Trend arrows are horizontal and directional (Democratic shifts point left; Republican shifts point right).
- **Census Check:** A lightweight bridge between population growth/decline (since 2020) and election movement (since ~2020 and long-run), labeled as `Reinforcing`, `Realigning`, or `Mixed impact`.
- **County Census Insight:** A quick cross-check using U.S. Census county population estimates (Vintage 2025, April 1, 2020 to July 1, 2025).

`Census Check` includes a short “receipt” of evidence lines (population change, recent shift, optional flip, and a county-type label like metro/coastal/rural). It also includes a confidence tag, and it tries to avoid overcalling “realignment” off a single-cycle blip in stronghold/lean counties unless other signals (like a flip or clear trend reversal) support it. Jasper County is treated as a narrow exception when its Census growth is extreme (“hyper-growth”).

### Trajectory labels

The trajectory status headline is built from three parts:

- **Trajectory type:** `Durable`, `Reinforcing`, `Emerging`, `Realigned`
- **Side:** `Republican`, `Democratic`, or `Competitive`
- **Position:** `Edge`, `Lean`, `Stronghold` (or `Battleground` when the latest margin is within ~5 points)

Meanings (high-level heuristics):

- **Durable:** The county has a sustained advantage for one side across the visible history.
- **Reinforcing:** The county already leaned one way, and recent cycles are pushing it further in that same direction.
- **Emerging:** The county shows a noticeable long-run change (movement over time), but not necessarily a full “column swap” yet.
- **Realigned:** A large long-run shift (and/or a clear recent flip with a meaningful margin) consistent with a true alignment change.

### Momentum line

`Momentum` summarizes the most recent cycle-to-cycle change in margin as adjective-based direction:

- `→ Modest|Building|Strong|Surging Republican momentum`: moved toward Republicans since the previous cycle
- `← Modest|Building|Strong|Surging Democratic momentum`: moved toward Democrats since the previous cycle
- `↔ Steady`: little change since the previous cycle
- `(accelerating)`: recent multi-cycle steps are consistently moving in the same direction

Intensity buckets are based on the absolute point shift: `Modest` (<2), `Building` (2–<4), `Strong` (4–<8), `Surging` (≥8).

The Census insight includes a simple "growth driver" label. These are heuristics meant to keep the text readable, not definitive explanations:

- Coastal metro growth (Charleston): `Charleston`, `Berkeley`, `Dorchester`
- Grand Strand growth (Myrtle Beach): `Horry`, `Georgetown`
- Lowcountry growth (Hilton Head-Savannah corridor): `Beaufort`, `Jasper`
- Major metro spillover (Charlotte): `York`, `Lancaster`, `Chester`
- Cross-border spillover (Augusta): `Aiken`, `Edgefield`
- State-capital metro growth (Columbia): `Richland`, `Lexington`, `Kershaw`
- Upstate metro buildout (Greenville-Spartanburg): `Greenville`, `Spartanburg`, `Pickens`, `Anderson`, `Cherokee`, `Laurens`
- Pee Dee hub growth (Florence corridor): `Florence`, `Darlington`, `Chesterfield`
- Pee Dee population decline: `Dillon`, `Marion`, `Marlboro`
- Coastal growth (fallback coastal bucket): `Colleton`
- Lake-region growth: `Fairfield`, `Greenwood`, `Newberry`, `Oconee`, `Saluda`
- Rural decline: `Allendale`, `Bamberg`, `Barnwell`, `Calhoun`, `Chesterfield`, `Dillon`, `Marlboro`, `Orangeburg`, `Williamsburg`

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

## Live Deployment

This project is served through GitHub Pages:

https://tenjin25.github.io/SCPrecinctMap/

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
|   |-- backfill_missing_contest_rows_from_oe_csv.py
|   |-- build_statewide_contest_mismatch_report.py
|   |-- build_vtd10_to_vtd20_overlap_csv.py
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

Apply precinct aliases/splits across all contest slices:

```powershell
python scripts/apply_precinct_aliases_to_slice.py --all
```

Check likely precinct name mismatches for a contest/year:

```powershell
python scripts/precinct_mismatch_report.py --contest president --year 2024
```

Build statewide mismatch reports (summary, extra rows, missing polygons, and county rollups):

```powershell
python scripts/build_statewide_contest_mismatch_report.py --out-prefix contest_mismatch_summary_post_alias_pass
```

Build a VTD10->VTD20 overlap crosswalk (example for Spartanburg/Lancaster):

```powershell
python scripts/build_vtd10_to_vtd20_overlap_csv.py --source Data/tl_2012_45_vtd10.zip --target data/Voting_Precincts.geojson --counties "Spartanburg,Lancaster" --out scripts/out/vtd10_to_vtd20_overlap_spartanburg_lancaster.csv
```

Backfill missing precinct rows from OpenElections CSV using mismatch output:

```powershell
python scripts/backfill_missing_contest_rows_from_oe_csv.py --year 2022 --contest governor --contest us_senate --mismatch-csv scripts/out/contest_mismatch_missing_polygons_post_alias_pass.csv
```

Convert SC Election Commission export into OpenElections-style format:

```powershell
python scripts/elstats_search_to_openelections.py --input Data/_tmpdata/in.csv --output Data/openelections-data-sc/2024/20241105__sc__general__precinct.csv
```

## Frontend Behavior Summary

- Views: `Counties`, `Congress`, `State House`, `State Senate`
- Analysis modes: `Margins`, `Winners`, `Shift`, `Flips`
- Core tools: contest search/select, precinct toggle, label toggle, color-accessibility toggle, fly-to search
- County click action: open county details and zoom to county bounds
- Precinct quick-stats line: live count of precinct centroids in current viewport
- Label legibility improvements: stronger halos for place/county/district labels
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
