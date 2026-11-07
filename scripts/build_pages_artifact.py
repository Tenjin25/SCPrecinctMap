#!/usr/bin/env python3
"""Build the runtime-only static artifact published to GitHub Pages."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

EXCLUDED_DIRECTORIES = {
    ".agents",
    ".codex",
    ".git",
    ".github",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "scripts",
    "tests",
    "tmp_screens",
    "data/2026_precinct_results",
    "data/district_contests_county_constrained_staging",
}

EXCLUDED_FILES = {
    "NCMap.html",
    "__main_inline.js",
    "index - copy.html",
    "index_24_mobile_vh_tooltip_black_fixed.html",
    "index_sc_trajectory_census_edgecases.html",
    "data/20241105__sc__general__precinct.csv",
    "data/20241105__sc__general__precinct_complete.csv",
    "data/20261103__sc__general__precinct.csv",
    "data/7131_list.csv",
}

REQUIRED_RUNTIME_FILES = {
    "index.html",
    "data/Voting_Precincts.geojson",
    "data/precinct_centroids.geojson",
    "data/contests/manifest.json",
    "data/contests/attorney_general_2026.json",
    "data/district_contests/manifest.json",
    "data/district_contests/congressional_us_house_2026.json",
    "data/district_contests/state_house_2022_lines/manifest_2022_lines.json",
    "data/district_contests/state_house_2024_lines/manifest_2024_lines.json",
}


def normalized_relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def should_exclude(path: Path, root: Path, output: Path) -> bool:
    try:
        if path.resolve() == output.resolve() or output.resolve() in path.resolve().parents:
            return True
    except FileNotFoundError:
        pass
    relative = normalized_relative(path, root)
    if any(part.startswith(".") for part in Path(relative).parts):
        return True
    if any(relative == directory or relative.startswith(f"{directory}/") for directory in EXCLUDED_DIRECTORIES):
        return True
    if path.is_dir():
        return path.name in {"__pycache__", ".pytest_cache"}
    if relative in EXCLUDED_FILES:
        return True
    if path.suffix.casefold() == ".xlsx":
        return True
    return False


def build_artifact(root: Path, output: Path) -> dict[str, int]:
    root = root.resolve()
    output = output.resolve()
    if output == root or (root in output.parents and output.name in {"data", "assets"}):
        raise ValueError(f"refusing unsafe Pages output path: {output}")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    copied_files = 0
    copied_bytes = 0
    for source in root.rglob("*"):
        if should_exclude(source, root, output):
            continue
        relative = source.relative_to(root)
        destination = output / relative
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied_files += 1
        copied_bytes += source.stat().st_size

    (output / ".nojekyll").touch()
    missing = sorted(path for path in REQUIRED_RUNTIME_FILES if not (output / path).is_file())
    if missing:
        raise ValueError(f"Pages artifact is missing required runtime files: {', '.join(missing)}")
    return {"files": copied_files + 1, "bytes": copied_bytes}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args()
    stats = build_artifact(args.root, args.output)
    print(
        f"Pages artifact: {stats['files']} files, "
        f"{stats['bytes'] / (1024 * 1024):.2f} MiB at {args.output.resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
