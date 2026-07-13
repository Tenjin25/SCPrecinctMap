# The Palmetto Explorer (successor to SCPrecinctMap)

The Palmetto Explorer is an interactive South Carolina election atlas built as a single-page web app.
It is the successor project to the original SCPrecinctMap release.

Its user experience is intentionally inspired by the NC Election Atlas UI, then adapted for South Carolina boundaries, contests, and workflows.

## Recent Updates (July 2026)

- **VTD20-normalized statewide contest layer:**
  - Added `data/contests_vtd20_crosswalked/` as the front-end statewide contest source.
  - The app now points `CONFIG.paths.contests_dir` at `./data/contests_vtd20_crosswalked`.
  - These files preserve county rows, normalize precinct rows onto the 2020 VTD/precinct geography, and keep vote totals equal to the source contest files.
  - The committed crosswalked contest JSON files are pretty-printed for reviewable diffs.
  - Spartanburg County's former `Fairgrounds` voting precinct is treated as the renamed `Cleveland Elementary` precinct, effective July 1, 2023; the 2024 statewide CSV `division_id` `11588` row is assigned to `Spartanburg - Cleveland Elementary`, while the later York County `Fairgrounds` row remains `York - Fairgrounds`.
  - Additional 2024 Spartanburg precinct corrections split the Converse/Converse Fire Station rows and assign the Converse University-area row, formerly Converse College (`division_id` `11587`), to `Beaumont Methodist`; `Trinity Methodist Church` (`division_id` `11581`) is merged into `Trinity Presbyterian`; `Hearon Circle` (`division_id` `11597`) is treated as the renamed `Bethany Baptist` precinct; `Wade Hampton` (`division_id` `11558`) is treated as the renamed `Cedar Grove Baptist` precinct; `Peach Blossom` (`division_id` `11617`) is treated as the renamed `Chapman Elementary` precinct.

- **Areal and vote-weighted crosswalk workflow:**
  - Added pro-method overlap scripts for legacy VTD/block geography:
    - `scripts/build_legacy_vtd_overlap_pro.py`
    - `scripts/build_weighted_splits_from_areal_crosswalk.py`
    - `scripts/compose_areal_weight_crosswalks.py`
  - Added 2000/2010/2020 bridge support so older precinct results can flow through VTD00 -> VTD10 -> VTD20 when direct VTD20 matching is not enough.
  - Added `scripts/build_legacy_name_weighted_splits.py` to combine legacy name bridge candidates with VTD20 vote-weight splits.
  - Added `scripts/aggregate_contests_to_vtd20_crosswalks.py` to write app-ready contest files without modifying the raw `data/contests/` inputs.

- **SCVotes legacy precinct-name support:**
  - Added `scripts/fetch_scvotes_enr_precinct_names.py` for legacy ENR county/precinct names.
  - Added `scripts/build_vtd00_name_bridge_candidates.py` to help bridge 2006/2008 result names to VTD00 sources and then onward to VTD20.
  - Review cases remain inspectable through generated CSVs in `scripts/out/` during maintenance runs.

- **NC Election Atlas-style friendly VTD20 names:**
  - Added `scripts/build_sc_precinct_friendly_names.js`.
  - Added `data/precinct_friendly_names.json`.
  - `index.html` loads the friendly-name JSON through `CONFIG.paths.precinct_friendly_names`.
  - Precinct polygons and centroids include `precinct_code`, `precinct_full_name`, and `precinct_display_name` fields.
  - Friendly names now prefer source VTD20 fields (`NAME20`, `NAMELSAD20`, `prec_id`, `PREC_ID`) over previously generated display fields, so reruns do not feed on older friendly-name output.
  - Standalone `And` is normalized to lowercase `and` in precinct display labels/tooltips.
  - Source spelling/pluralization is preserved when counties differ; for example Dorchester remains `Four Hole` while Orangeburg remains `Four Holes`.

- **HD-40 / Newberry county district-contest fix:**
  - Updated State House District 40 rows in district contest files so HD-40 matches Newberry County totals where the district covers all of Newberry County.
  - The update covers base State House district contest files, `state_house_2022_lines/`, and the relevant `state_house_2024_lines/` superintendent files.
  - Validation checks confirmed only district `40` changed and every HD-40 row matches the Newberry county row in `data/contests_vtd20_crosswalked`.

- **Overlay opacity and cache busting:**
  - Map Reveal and Balanced opacity presets now have more distinct behavior when precinct overlays are visible.
  - `DATA_CACHE_BUSTER` and `APP_BUILD_ID` are bumped in `index.html` when data/UI changes need a hard refresh on GitHub Pages.

## Recent Updates (May 2026)

- **County/precinct + lines/opacity polish pass (May 2026):**
  - Reverted to the `ae1bfe5` baseline and fixed 2024 county totals to use canonical contest JSON (no 2024 OpenElections county override), restoring expected county margins (for example York 2024 presidential to ~`R+19.09`).
  - Refined precinct alias/display cleanup with typo handling (for example `Licolnville` -> `Lincolnville`) and additional alias mappings in `precinct_aliases.json`.
  - Matched mobile panel aesthetics more closely to desktop floating cards/tooltips while preserving touch-friendly sheet behavior.
  - Tuned overlay opacity behavior for county, district, and precinct browsing. Current Map Reveal/Balanced/Data Focus values live in `getOverlayOpacityPresetConfig()` in `index.html`.
  - Updated State House 2024 lines wiring:
    - `state_house_2024` now points to the dedicated `sc_state_house_2024_lines_tileset.geojson`.
    - State House view now defaults to 2024 lines.
    - First switch to State House forces a geometry refresh so 2024 lines render immediately (no initial 2022 flash).
  - Refined district-line toggle visibility by view:
    - `2024` toggle shown only for State House.
    - `2026` toggle shown only for Congressional.
    - `2022` hidden outside district views.
  - Removed the 2000 anchor line from the Long-Term Trend trajectory block.

- **Precinct matching carryover sync (May 12, 2026):**
  - Ported the precinct key-matching variant logic from `index - copy.html` into the primary `index.html` code path.
  - Updated both `precinctNormVariantsLite(...)` and `precinctNormVariants(...)` to keep county/precinct matching behavior consistent in the live app.

- **NCMap-style mobile sheet simplification + global shift formatting standardization (May 3, 2026):**
  - Unified mobile panel behavior to the sheet system (no legacy dual-mode fallback split):
    - `Layers` (`.main-controls`) uses top-sheet behavior.
    - `Legend` (`.legend`) uses bottom-sheet behavior.
  - Fixed minimized-state rendering so collapsed panels still retain their visible header + action button (no blank/empty minimized shells).
  - Preserved mobile dock behavior (`Search / Layers / Legend`), vote-counter spacing/positioning, and tooltip stacking behavior.
  - Standardized shift text formatting everywhere to concise election-style party deltas:
    - `R+X.XX%` / `D+X.XX%`
    - Example: `Shift: R+6.63% since 2020`
  - Kept underlying shift calculations, margin math, and winner logic unchanged.

## Recent Updates (April 2026)

- **Shift summary wording trim (April 30, 2026):**
  - Updated only the county Census Check summary line format `Since YYYY: Shifted X% toward ...` to use shorter party wording (`GOP` / `Dems`).
  - Left other timeline/legend/momentum party labels unchanged.

- **Basemap town labels above overlays (April 23, 2026):**
  - Ensured Mapbox’s built-in place/town labels stay visible above county/district/precinct overlays by inserting overlay layers below the first basemap symbol layer.
  - Removed the unused `vtds_2000` / “Precincts 2000” placeholder view (old share links fall back to counties).

- **Viewport precinct quick-stats (April 23, 2026):**
  - Added a live **“Viewing N precincts”** line under the fly-to search UI (top bar + desktop controls).
  - Precinct centroid data preloads after first idle (to keep initial paint fast), then the count updates on pan/zoom.

- **Mobile bottom dock + swipeable sheets (April 22, 2026):**
  - Replaced the floating mobile “thumb” buttons with an **NC-style bottom dock**: **Search / Layers / Legend**.
  - Panels open as **bottom sheets** and can be resized:
    - Tap a dock button repeatedly to cycle **half → full → collapsed**.
    - Use the top **grab handle** to swipe/flick up/down between snap states.
    - Tap the scrim (or press **Escape** with a hardware keyboard) to close all sheets.
  - When sheets open, the hover tooltip and vote counter auto-yield space to reduce overlaps.

- **Mobile overlay spacing parity (April 30, 2026):**
  - Mobile `#hover-tooltip` is now a fixed, scrollable card that sits above the bottom dock **and** above the focus briefing panel (`#vote-counter`) using measured `--vote-counter-h` spacing.
  - Android mobile uses the same visualViewport inset offsets while keeping the `+ 24px` dock gap so the tooltip/counter don’t drift under the URL bar.

- **Precinct-mode visibility cleanup (April 21, 2026):**
  - Increased precinct polygon fill opacity so underlying county colors no longer show through faintly during precinct browsing.
  - This is a targeted visual polish for readability only; no contest logic or interaction behavior changed.

- **NC-style hover refinements + flip line + mobile docking (April 10, 2026):**
  - Hover tooltip adds an explicit **Flip line** when the hovered geography’s winner changed since the prior comparable cycle (e.g., `Flip: D→R (2020→2024)`).
  - Vote-delta + population-change insight lines are rendered with tighter **NC desk-hover aesthetics** (aligned, scan-friendly delta rows).
  - Mobile layout: the hover card and selected **focus briefing panel** (`#vote-counter`) avoid the bottom dock so close/details are easier to access on touch.

- **Hover tooltip deltas + NC-style pinning (April 9, 2026):**
  - Hover tooltip now opens with an NC-style **compact “quickline”** (candidate + margin%) plus an **insight** block.
  - Insight block adds raw deltas vs prior cycle (when available): `R Δ`, `D Δ`, and `Margin Δ` in **votes**.
  - Population context is now shown as two Census-estimate deltas: `2020→2024` and `2024→2025`.
  - **Pin** reveals the full “Details” section (chips + full result card + CVAP/VAP as available).

- **Design-only premium UI polish (April 8, 2026):**
  - Refined the flagship **selected focus briefing panel** (`#vote-counter`) to feel more editorial: clearer hierarchy, calmer spacing, and a stronger “main takeaway” line.
  - Reduced the “stacked components” feeling by relying more on typography + whitespace and less on borders/boxed sub-cards (subtle surfaces, quieter dividers).
  - Unified the desktop floating surfaces (controls/legend/modes/topbar/focus) with consistent radii, shadow depth, and opacity for a more premium finish.
  - **CSS-only change**; no data/model/contest logic changes. Sidebar remains disabled.

- **County focus panel teardown + facelift (selected-county experience):**
  - Rebuilt the selected-county hierarchy to read like a premium election desk:
    1) **At a glance** (winner + margin + contest/year)
    2) One dominant summary card with vote-share bar + key context
    3) **Why it votes this way** (short causal explainer)
    4) Confidence + statewide comparison + supporting facts (subordinate)
    5) Deep detail (trajectory/census/trends/buckets) behind a single expandable section
  - **Placement + layout parity with `NCMap.html`:** the county explainer now renders as an NC-style **“At a glance”** + **“Deeper story”** block inside the always-on right-side focus panel (vote counter), within the `Trend` area (not a separate sidebar).
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

## Interaction Model (Desktop + Mobile)

This project intentionally follows the “election desk atlas” interaction pattern: a fast hover/tap read, an optional pin/freeze step, and a separate always-on “focus briefing” panel for selected geography.

### Desktop basics

- **Hover tooltip (fast read):** hover a county/precinct to see the compact quickline + deltas/insight.
- **Pin (freeze):** click **Pin** in the tooltip to freeze the hovered feature so it won’t change as you move the mouse.
- **Details on demand:** pinned tooltips expand to show deeper “Details” (chips + full result card + CVAP/VAP where available).
- **Flips callout:** if a winner changed since the prior comparable cycle, the tooltip includes `Flip: … (year→year)` to make “why this is interesting” legible quickly.
- **Focus briefing panel (`#vote-counter`):** clicking a geography pins it as the selected focus; **Clear** removes the selection.

### Mobile basics

- **Tap instead of hover:** tap a county/precinct to open the hover card (the touch equivalent of the desktop hover tooltip).
- **Thumb dock:** the bottom “thumb-reach” dock exposes quick actions like **Controls** and **Search** without hiding map context.
- **Safe-area + padding sync:** the map and floating panels account for iOS/Android safe areas and the thumb dock height so the hover card and focus panel remain readable.

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
- 2,266 precinct polygons (`data/Voting_Precincts.geojson`)
- 7 congressional districts (`data/tileset/sc_cd118_tileset.geojson`)
- 124 state house districts (`data/tileset/sc_state_house_2022_lines_tileset.geojson`)
- 46 state senate districts (`data/tileset/sc_state_senate_2022_lines_tileset.geojson`)
- 45 raw county/precinct contest slice files (`data/contests/manifest.json`)
- 45 VTD20-crosswalked county/precinct contest slice files (`data/contests_vtd20_crosswalked/manifest.json`)
- 155 district contest manifest entries (`data/district_contests/manifest.json`)
- Friendly VTD20 precinct-name lookup for all 46 counties (`data/precinct_friendly_names.json`)

Coverage varies by office and year. The live app uses `data/contests_vtd20_crosswalked/` for statewide county/precinct contests and `data/district_contests/` for district views.

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

## Running Locally

Because the app fetches local JSON/GeoJSON/CSV assets, running through a local static server is the most reliable way to test:

```bash
python -m http.server 8000
```

Or (Node.js):

```bash
npx http-server . -p 8000
```

Then open:

- http://localhost:8000/

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
|   |-- aggregate_contests_to_vtd20_crosswalks.py
|   |-- build_statewide_contest_mismatch_report.py
|   |-- build_legacy_name_weighted_splits.py
|   |-- build_legacy_vtd_overlap_pro.py
|   |-- build_sc_precinct_friendly_names.js
|   |-- build_vtd00_name_bridge_candidates.py
|   |-- build_weighted_splits_from_areal_crosswalk.py
|   |-- compose_areal_weight_crosswalks.py
|   |-- fetch_scvotes_enr_precinct_names.py
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
    |-- contests_vtd20_crosswalked/
    |-- precinct_friendly_names.json
    `-- district_contests/
```

## Data Pipeline

`build_data.py` is the main offline pipeline. It:

1. Builds county and precinct GeoJSON.
2. Builds congressional/state-house/state-senate district GeoJSON.
3. Aggregates precinct election CSV rows into raw county/precinct contest slices in `data/contests/`.
4. Builds district-level contest slices and manifests.

The VTD20-normalized contest layer is a follow-on pipeline, not a replacement for the raw build:

1. Build or refresh legacy VTD/block overlap CSVs in `scripts/out/`.
2. Build vote-weighted split JSONs from those overlaps.
3. Build legacy name bridge candidates for older result names.
4. Aggregate raw `data/contests/` into `data/contests_vtd20_crosswalked/`.
5. Point the frontend at the crosswalked directory through `CONFIG.paths.contests_dir`.

The app-ready crosswalked contest files are committed. Large intermediate files under `scripts/out/` and source TIGER zips are intentionally treated as scratch/maintenance artifacts.

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

For crosswalked contest slices in `data/contests_vtd20_crosswalked/*.json`:

- County summary rows are preserved from the raw contest slice.
- Precinct rows are normalized to the 2020 VTD/precinct geography.
- Split precincts can emit multiple weighted target rows.
- File-level vote totals should match the corresponding raw contest file exactly.

For friendly precinct display:

- `data/precinct_friendly_names.json` maps county/code/name variants to display names.
- `index.html` loads it before precinct normalization when available.
- `data/Voting_Precincts.geojson` and `data/precinct_centroids.geojson` carry `precinct_code`, `precinct_full_name`, and `precinct_display_name`.
- `scripts/build_sc_precinct_friendly_names.js` treats source geography labels as authoritative for county-specific naming differences. Do not add broad spelling/pluralization overrides when the underlying VTD20 source distinguishes names by county, such as `Dorchester - Four Hole` versus `Orangeburg - Four Holes`.
- The front-end applies the same precinct-name style pass for tooltip fallbacks, including lowercase standalone `and`.

## Common Maintenance Commands

Build all generated outputs:

```bash
python build_data.py
```

Apply precinct aliases/splits across all contest slices:

```powershell
python scripts/apply_precinct_aliases_to_slice.py --all
```

Build friendly VTD20 precinct names:

```powershell
node scripts/build_sc_precinct_friendly_names.js
```

Build pro-method areal overlap crosswalks:

```powershell
python scripts/build_legacy_vtd_overlap_pro.py --source work/crosswalk_inputs/tl_2012_45_vtd10.zip --target work/crosswalk_inputs/tl_2020_45_vtd20.zip --source-kind vtd --target-kind vtd --out scripts/out/vtd10_to_vtd20_areal_top5.csv
```

Build vote-weighted split JSONs from areal crosswalks:

```powershell
python scripts/build_weighted_splits_from_areal_crosswalk.py --areal scripts/out/vtd10_to_vtd20_areal_top5.csv --out scripts/out/vtd10_to_vtd20_vote_weight_splits.json
```

Compose VTD00 -> VTD10 -> VTD20 weights:

```powershell
python scripts/compose_areal_weight_crosswalks.py --first scripts/out/vtd00_to_vtd10_areal_top8.csv --second scripts/out/vtd10_to_vtd20_areal_top5.csv --out scripts/out/vtd00_to_vtd10_to_vtd20_vote_weight_splits.json
```

Fetch official legacy SCVotes ENR precinct names:

```powershell
python scripts/fetch_scvotes_enr_precinct_names.py --year 2008 --state-select-url "https://www.enr-scvotes.org/SC/8562/15723/en/select-county.html?cid=105" --out scripts/out/scvotes_enr_precinct_names_2008.csv
```

Build legacy name bridge candidates:

```powershell
python scripts/build_vtd00_name_bridge_candidates.py
```

Build legacy name weighted splits:

```powershell
python scripts/build_legacy_name_weighted_splits.py
```

Aggregate raw statewide contests to VTD20-normalized app data:

```powershell
python scripts/aggregate_contests_to_vtd20_crosswalks.py
```

Pretty-print crosswalked contest JSON after generation:

```powershell
node -e "const fs=require('fs'),path=require('path');const root='data/contests_vtd20_crosswalked';for(const name of fs.readdirSync(root).filter(n=>n.endsWith('.json'))){const p=path.join(root,name);fs.writeFileSync(p,JSON.stringify(JSON.parse(fs.readFileSync(p,'utf8')),null,2)+'\n');}"
```

Validate crosswalked contest totals against raw contest totals:

```powershell
node -e "const fs=require('fs'),path=require('path');const src='data/contests',out='data/contests_vtd20_crosswalked';const manifest=JSON.parse(fs.readFileSync(path.join(out,'manifest.json'),'utf8')).files;const keys=['dem_votes','rep_votes','other_votes','total_votes'];let bad=0;for(const e of manifest){const s=JSON.parse(fs.readFileSync(path.join(src,e.file),'utf8')).rows||[];const o=JSON.parse(fs.readFileSync(path.join(out,e.file),'utf8')).rows||[];for(const k of keys){const sv=s.reduce((a,r)=>a+Number(r[k]||0),0);const ov=o.reduce((a,r)=>a+Number(r[k]||0),0);if(Math.abs(sv-ov)>0.01)bad++;}}console.log('checked',manifest.length,'bad',bad);"
```

Validate the HD-40/Newberry district-contest contract:

```powershell
node -e "const fs=require('fs'),path=require('path'),cp=require('child_process');const files=cp.execSync('git diff --name-only -- data/district_contests',{encoding:'utf8'}).trim().split(/\r?\n/).filter(Boolean);const bad=[];const changed=new Map();for(const f of files){const old=JSON.parse(cp.execSync('git show HEAD:'+f,{encoding:'utf8',maxBuffer:80*1024*1024}));const cur=JSON.parse(fs.readFileSync(f,'utf8'));const a=old.general.results||{},b=cur.general.results||{};for(const k of new Set([...Object.keys(a),...Object.keys(b)])){if(JSON.stringify(a[k])!==JSON.stringify(b[k]))changed.set(k,(changed.get(k)||0)+1);}const name=path.basename(f,'.json');const m=name.match(/^state_house_(.+)_(\d{4})(?:_(?:2022|2024)_lines)?$/);const contestFile=path.join('data','contests_vtd20_crosswalked',m[1]+'_'+m[2]+'.json');const county=(JSON.parse(fs.readFileSync(contestFile,'utf8')).rows||[]).find(r=>String(r.county||'').toUpperCase()==='NEWBERRY'&&!r.precinct&&!r.precinct_norm);const row=cur.general.results['40'];for(const k of ['dem_votes','rep_votes','other_votes','total_votes'])if(Number(row[k])!==Number(county[k]))bad.push([f,k,row[k],county[k]]);}console.log('files',files.length,'changedDistricts',JSON.stringify([...changed.entries()]),'bad',bad.length);"
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

Rebuild superintendent statewide slices and districtized outputs, optionally using approved crosswalk remaps first:

```powershell
python scripts/rebuild_superintendent_aggregation.py --with-districts --apply-crosswalk --use-runtime-crosswalk --crosswalk-min-confidence medium
```

Convert SC Election Commission export into OpenElections-style format:

```powershell
python scripts/elstats_search_to_openelections.py --input Data/_tmpdata/in.csv --output Data/openelections-data-sc/2024/20241105__sc__general__precinct.csv
```

### Cachebuster

For changes that affect deployed app behavior or served data, bump both constants in `index.html`:

```js
const DATA_CACHE_BUSTER = 'YYYY-MM-DD-N';
const APP_BUILD_ID = 'YYYY-MM-DD-N';
```

The app appends `?v=...` to configured data paths via `withCacheBuster(...)`, and the build ID is shown in the page footer/debug surface.

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
- Hover card / tooltip behavior tuned for touch (tap to open, easy Close access, and “Details” expansion without requiring a separate pin step)
- Selected focus briefing panel placement tuned to sit above the bottom dock + legend on smaller screens

Desktop layout remains available with the full side/control experience.

## Key Data and Config Files

- `index.html`: app UI, rendering logic, and `CONFIG`
- `build_data.py`: primary data build pipeline
- `data/contests/manifest.json`: raw county/precinct contests
- `data/contests_vtd20_crosswalked/manifest.json`: VTD20-normalized county/precinct contests used by the live app
- `data/district_contests/manifest.json`: available district contest slices
- `data/precinct_friendly_names.json`: VTD20 precinct display-name lookup
- `precinct_aliases.json`: manual precinct name normalization overrides
- `scripts/out/`: ignored maintenance outputs such as overlap CSVs, bridge candidates, and weighted split JSONs

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
- Historical precinct names are not always one-to-one across sources. The VTD20 crosswalk workflow uses a mix of direct matches, aliases, areal overlaps, vote-weighted splits, and legacy name bridges.
- Crosswalked precinct splits are estimates. County/file vote totals are preserved, but precinct-level allocation depends on the best available areal/vote-weight bridge.
- Review-held legacy name bridge candidates should not be promoted into weighted splits without manual inspection.
- HD-40 is treated as all-Newberry for the affected State House district-contest files; revalidate this if district geography/source files are regenerated.
- This repository currently has no explicit `LICENSE` file. Add one before broad reuse or redistribution.
