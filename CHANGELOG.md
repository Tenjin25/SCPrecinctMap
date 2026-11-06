# Change history

## 2026-11-05

- Added unofficial 2026 precinct results for seven statewide contests and all 124 State House contests, credited to [SCPOL Elections](https://elections.scpol.info/2026/).
- Converted the wide source exports to the repository's OpenElections-compatible format and rebuilt the county, precinct, congressional, State House, and State Senate result layers.

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
