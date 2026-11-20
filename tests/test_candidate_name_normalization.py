import json
import unittest

from scripts.candidate_name_normalization import (
    normalize_candidate_fields,
    normalize_candidate_name,
    normalize_json_text,
    should_normalize_candidate_json,
)


class CandidateNameNormalizationTests(unittest.TestCase):
    def test_initials_suffixes_and_party_artifacts(self):
        cases = {
            "Grady L Patterson Jr": "Grady L. Patterson, Jr.",
            "Grady Patterson Jr": "Grady Patterson, Jr.",
            "David Mack III": "David Mack, III",
            "DEM Annie E McDaniel": "Annie E. McDaniel",
            "REP Kirkman Finlay III": "Kirkman Finlay, III",
            "Bruce W. Bannister": "Bruce W. Bannister",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(normalize_candidate_name(source), expected)
                self.assertEqual(normalize_candidate_name(expected), expected)

    def test_json_normalization_preserves_votes_and_quoted_names(self):
        source = '{"dem_candidate":"Grady L Patterson Jr","rep_candidate":"James \\"Jim\\" E Clyburn","total_votes":42}'
        updated, changes = normalize_json_text(source)
        self.assertEqual(changes, 2)
        payload = json.loads(updated)
        self.assertEqual(payload["dem_candidate"], "Grady L. Patterson, Jr.")
        self.assertEqual(payload["rep_candidate"], 'James "Jim" E. Clyburn')
        self.assertEqual(payload["total_votes"], 42)
        self.assertEqual(normalize_json_text(updated), (updated, 0))
        self.assertEqual(normalize_candidate_fields(payload), payload)

    def test_direct_races_before_2022_are_excluded(self):
        for family in ("state_house_state_house", "state_senate_state_senate", "congressional_us_house"):
            self.assertFalse(should_normalize_candidate_json(f"{family}_2020.json"))
            self.assertTrue(should_normalize_candidate_json(f"{family}_2022.json"))
        self.assertFalse(should_normalize_candidate_json("state_house_state_house_2018_2022_lines.json"))
        self.assertTrue(should_normalize_candidate_json("state_house_state_treasurer_2006.json"))
