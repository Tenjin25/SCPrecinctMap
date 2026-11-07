import tempfile
import unittest
from pathlib import Path

from scripts.build_pages_artifact import REQUIRED_RUNTIME_FILES, build_artifact


class PagesArtifactTests(unittest.TestCase):
    def test_build_keeps_runtime_and_excludes_source_material(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "source"
            output = Path(temp_dir) / "site"
            for relative in REQUIRED_RUNTIME_FILES:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}", encoding="utf-8")
            raw_csv = root / "data/20261103__sc__general__precinct.csv"
            raw_csv.write_text("source-only", encoding="utf-8")
            raw_dir = root / "data/2026_precinct_results"
            raw_dir.mkdir(parents=True)
            (raw_dir / "source.csv").write_text("source-only", encoding="utf-8")
            script_dir = root / "scripts"
            script_dir.mkdir()
            (script_dir / "build.py").write_text("source-only", encoding="utf-8")
            test_dir = root / "tests"
            test_dir.mkdir()
            (test_dir / "test_build.py").write_text("source-only", encoding="utf-8")

            stats = build_artifact(root, output)
            self.assertGreater(stats["files"], 0)
            self.assertTrue((output / "index.html").is_file())
            self.assertTrue((output / "data/contests/attorney_general_2026.json").is_file())
            self.assertFalse((output / "data/20261103__sc__general__precinct.csv").exists())
            self.assertFalse((output / "data/2026_precinct_results").exists())
            self.assertFalse((output / "scripts").exists())
            self.assertFalse((output / "tests").exists())
            self.assertTrue((output / ".nojekyll").is_file())


if __name__ == "__main__":
    unittest.main()
