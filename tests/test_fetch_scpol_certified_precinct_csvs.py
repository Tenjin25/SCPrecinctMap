import csv
import tempfile
import unittest
from pathlib import Path

from scripts.fetch_scpol_certified_precinct_csvs import (
    STATE_CERTIFICATION,
    candidate_label,
    publish,
    write_wide_csv,
)


class ScpolCertifiedFeedTests(unittest.TestCase):
    def test_candidate_label(self):
        self.assertEqual(candidate_label({"name": "REP Jane Doe", "party": "REP"}), "REP Jane Doe (REP)")
        self.assertEqual(candidate_label({"name": "Write-In", "party": "NON"}), "Write-In (NON)")

    def test_state_certification_keeps_ordered_recounts_explicit(self):
        self.assertEqual(STATE_CERTIFICATION["status"], "official_except_ordered_recounts")
        self.assertEqual(
            STATE_CERTIFICATION["ordered_recounts"],
            [
                {"office": "U.S. House", "district": "2"},
                {"office": "State House", "district": "27"},
                {"office": "State House", "district": "89"},
            ],
        )

    def test_uncertified_county_is_rejected(self):
        counties = {f"County {index}": {"certified": True} for index in range(46)}
        counties["County 45"]["certified"] = False
        feed = {"sourceMetadata": {"counties": counties}}
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "uncertified county feeds: County 45"):
                publish(feed, Path(temp_dir))

    def test_wide_csv_reconciles_votes(self):
        race = {
            "id": "1",
            "title": "Attorney General",
            "type": "statewide",
            "candidates": [
                {"name": "REP Jane Doe", "party": "REP"},
                {"name": "Write-In", "party": "NON"},
            ],
        }
        precinct = {"county": "Abbeville", "name": "Abbeville No. 1", "votes": {"1": [10, 1]}}
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "results.csv"
            stats = write_wide_csv(path, [race], [precinct])
            self.assertEqual(stats, {"rows": 1, "votes": 11, "contests": 1})
            with path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["REP Jane Doe (REP)"], "10")
            self.assertEqual(rows[0]["Contest Total Votes"], "11")


if __name__ == "__main__":
    unittest.main()
