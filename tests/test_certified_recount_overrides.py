import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_data
from scripts.certified_recount_overrides import apply_certified_recounts, load_certification


class CertifiedRecountOverrideTests(unittest.TestCase):
    def test_direct_district_recounts_are_reapplied_without_changing_candidates(self):
        expected = {
            "congressional_us_house_2026.json": {
                "2": (144945, 147785, 4007, 296737, 2840),
            },
            "state_house_state_house_2026.json": {
                "27": (9111, 9143, 8, 18262, 32),
                "89": (7263, 7157, 11, 14431, -106),
            },
            "state_house_state_house_2026_2024_lines.json": {
                "27": (9111, 9143, 8, 18262, 32),
                "89": (7263, 7157, 11, 14431, -106),
            },
        }
        self.assertEqual(len(load_certification()["certified_recounts"]), 3)
        for filename, districts in expected.items():
            with self.subTest(filename=filename):
                payload = {"general": {"results": {
                    key: {"dem_votes": 1, "rep_votes": 1, "other_votes": 0,
                          "dem_candidate": "Dem", "rep_candidate": "Rep"}
                    for key in districts
                }}}
                self.assertEqual(
                    apply_certified_recounts(filename, payload, build_data.margin_color),
                    list(districts),
                )
                for key, (dem, rep, other, total, margin) in districts.items():
                    row = payload["general"]["results"][key]
                    self.assertEqual(
                        (row["dem_votes"], row["rep_votes"], row["other_votes"],
                         row["total_votes"], row["margin"]),
                        (dem, rep, other, total, margin),
                    )
                    self.assertEqual(row["margin_pct"], round(margin / total * 100, 4))
                    self.assertEqual(row["winner"], "R" if margin > 0 else "D")
                    self.assertEqual(row["color"], build_data.margin_color(row["margin_pct"]))
                    self.assertEqual((row["dem_candidate"], row["rep_candidate"]), ("Dem", "Rep"))

    def test_missing_direct_district_fails_loudly(self):
        with self.assertRaisesRegex(ValueError, "missing certified recount district 2"):
            apply_certified_recounts(
                "congressional_us_house_2026.json",
                {"general": {"results": {}}},
                build_data.margin_color,
            )

    def test_other_files_are_untouched(self):
        payload = {"general": {"results": {"2": {"dem_votes": 1}}}}
        self.assertEqual(apply_certified_recounts(
            "congressional_us_house_2024.json", payload, build_data.margin_color
        ), [])
        self.assertEqual(payload["general"]["results"]["2"]["dem_votes"], 1)

    def test_legacy_district_build_reapplies_recounts_after_aggregation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "data"
            output_dir.mkdir()
            csv_path = Path(temp_dir) / "election.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=("office", "district", "party", "candidate", "votes", "precinct")
                )
                writer.writeheader()
                for office, districts in (("U.S. House", ("2",)), ("State House", ("27", "89"))):
                    for district in districts:
                        writer.writerow({"office": office, "district": district, "party": "DEM",
                                         "candidate": "Dem", "votes": 1, "precinct": "One"})
                        writer.writerow({"office": office, "district": district, "party": "REP",
                                         "candidate": "Rep", "votes": 1, "precinct": "One"})
            with patch.object(build_data, "DATA_OUT", str(output_dir)), patch.object(
                build_data, "ELECTION_FILES", {2026: str(csv_path)}
            ):
                build_data.build_district_contests({2026})
            district_dir = output_dir / "district_contests"
            for filename, district, expected in (
                ("congressional_us_house_2026.json", "2", 296737),
                ("state_house_state_house_2026.json", "27", 18262),
                ("state_house_state_house_2026.json", "89", 14431),
            ):
                payload = json.loads((district_dir / filename).read_text(encoding="utf-8"))
                self.assertEqual(payload["general"]["results"][district]["total_votes"], expected)
