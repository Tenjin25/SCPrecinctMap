# 2026 statewide general-election precinct exports

These wide CSV files were downloaded from the independent
[South Carolina Political Data election map](https://elections.scpol.info/2026/).
The map states that its unofficial vote totals come from the South Carolina
Election Commission's county Election Night Reporting feeds.

The files were retrieved on November 5, 2026. The seven statewide files contain
one row per precinct/reporting unit. The all-State-House file contains one row
per precinct and House district, so a split precinct can appear more than once.
All files use one column per candidate. Run
`python scripts/scpol_precinct_csvs_to_openelections.py` to replace the matching
statewide contests in `data/20261103__sc__general__precinct.csv` while preserving
the other contests converted from the SC Votes county-summary workbook.

Non-geographic rows such as failsafe and provisional reporting units remain in
county totals but are not joined to precinct polygons.
