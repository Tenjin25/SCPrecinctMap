# 2026 general-election precinct exports

These wide CSV files were downloaded from the independent
[South Carolina Political Data election map](https://elections.scpol.info/2026/).
The map states that its vote totals come from the South Carolina Election
Commission's county Election Night Reporting feeds.

The files were refreshed on November 10, 2026, after state certification. The
results are official except for the ordered recounts in U.S. House District 2
and State House Districts 27 and 89. The eight statewide files contain one row
per precinct/reporting unit. The all-U.S.-House and all-State-House files
contain one row per precinct and district, so a split precinct can appear more
than once. All files use one column per candidate.

Run `python scripts/fetch_scpol_certified_precinct_csvs.py` to refresh the wide
CSVs and `certified_feed_metadata.json`, then run
`python scripts/scpol_precinct_csvs_to_openelections.py` to replace the matching
contests in `data/20261103__sc__general__precinct.csv` while preserving contests
that are still available only from the SC Votes county-summary workbook. The
2026 State Senate contest is intentionally excluded from this refresh.

Non-geographic rows such as failsafe and provisional reporting units remain in
county totals but are not joined to precinct polygons.
