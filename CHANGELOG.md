# Change history

## 2026-11-06

- Refreshed the 2026 results from the SCPOL/SC Votes ENR feed after all 46 county feeds were marked certified; final statewide certification is still pending.
- Added precinct-level Attorney General and all seven U.S. House contests, and rebuilt the county, precinct, congressional, State House, and State Senate statewide-overlay layers. The 2026 State Senate direct contest remains excluded.
- Added a reproducible importer and feed metadata snapshot so future certification updates can be checked and regenerated from the normalized source.
- Added a runtime-only GitHub Pages workflow that validates the static artifact on pull requests and excludes raw election inputs, development scripts, tests, screenshots, and QA staging files from production deployments.

## 2026-11-05

- Added unofficial 2026 precinct results for seven statewide contests and all 124 State House contests, credited to [SCPOL Elections](https://elections.scpol.info/2026/).
- Converted the wide source exports to the repository's OpenElections-compatible format and rebuilt the county, precinct, congressional, State House, and State Senate result layers. Statewide overlays now cover both the 2022-line and 2024-line State House views; direct 2026 House races use the current 2024 lines.
- Restored the stronger click-to-zoom framing for State House and State Senate districts by fitting the rendered district under the pointer instead of a potentially disconnected full-geometry extent.

## 2026-10-06

- Unlock the contest selector as soon as its manifest is ready instead of waiting for optional district geometry and demographic files.
- Begin fetching the contest manifest immediately instead of waiting for map-idle and browser-idle delays.
- Automatically collapse the mobile legend after selected election results finish loading.
- Start atlas hydration as soon as the Mapbox map is ready, avoiding mobile delays caused by waiting for every window asset.
- Preserve two-decimal margin precision for close results and competitiveness thresholds (for example, 0.45%).
- Refresh application and data cache tokens, plus shared-module asset tokens where present; repair shared module loading where needed.

## March–October 2026

**Last updated:** October 4, 2026

### Consistent District Demographic Pill Sizing (October 4, 2026)

- Applied the populated Demographics-mode compact pill styling to district demographic cards before election results load.
- Preserved the original natural popup dimensions and complete highest-to-lowest demographic layout; only the pill scale and spacing are unified.

### Descending Demographic Percentages (October 4, 2026)

- Sorted demographic race/ethnicity percentages from highest to lowest across the atlas's demographic cards.
- Kept unavailable Hispanic data at the end of the list so known percentages remain easy to scan.
