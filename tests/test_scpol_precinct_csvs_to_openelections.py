import csv
import tempfile
import unittest
from pathlib import Path

from scripts.scpol_precinct_csvs_to_openelections import (
    OUTPUT_FIELDS,
    convert,
    parse_candidate_header,
    parse_contest,
)


class ScpolPrecinctConversionTests(unittest.TestCase):
    def test_candidate_header_parsing(self):
        self.assertEqual(parse_candidate_header("REP Jane Doe (REP)"), ("Jane Doe", "REP"))
        self.assertEqual(parse_candidate_header("Write-In (NON)"), ("Write-In", ""))
        self.assertEqual(
            parse_contest("State House of Representatives, District 007"),
            ("State House", "7"),
        )

    def test_convert_replaces_only_matching_office(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "sources"
            source_dir.mkdir()
            source_path = source_dir / "sc-2026-governor-precincts.csv"
            with source_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, lineterminator="\n")
                writer.writerow(
                    [
                        "County",
                        "Precinct",
                        "Contest ID",
                        "Contest",
                        "Race Type",
                        "REP Jane Doe (REP)",
                        "DEM John Doe (DEM)",
                        "Write-In (NON)",
                        "Contest Total Votes",
                    ]
                )
                writer.writerow(
                    ["Abbeville", "Abbeville No. 1", "1", "Governor", "governor", "6", "3", "1", "10"]
                )

            output_path = root / "results.csv"
            with output_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
                writer.writeheader()
                writer.writerow(
                    {
                        "county": "Abbeville",
                        "precinct": "",
                        "office": "Governor",
                        "district": "",
                        "candidate": "Old Candidate",
                        "party": "REP",
                        "votes": 99,
                    }
                )
                writer.writerow(
                    {
                        "county": "Abbeville",
                        "precinct": "",
                        "office": "Attorney General",
                        "district": "",
                        "candidate": "Preserved Candidate",
                        "party": "REP",
                        "votes": 50,
                    }
                )

            stats = convert(source_dir, output_path)
            self.assertEqual(stats["preserved_rows"], 1)
            self.assertEqual(stats["precinct_reporting_units"], 1)

            with output_path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 4)
            self.assertEqual(rows[0]["office"], "Attorney General")
            self.assertEqual(rows[1]["precinct"], "Abbeville No. 1")
            self.assertEqual(rows[1]["early_voting"], "")
            self.assertEqual(sum(int(row["votes"]) for row in rows[1:]), 10)


if __name__ == "__main__":
    unittest.main()
