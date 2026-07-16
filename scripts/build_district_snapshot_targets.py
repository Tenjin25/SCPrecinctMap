#!/usr/bin/env python3
"""Extract compact district-share targets from a committed baseline."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FILE_RE = re.compile(r"^(congressional|state_house|state_senate)_(.+)_(\d{4})(?:_(2022|2024)_lines)?\.json$")
FIELDS = ("dem_votes", "rep_votes", "other_votes")


def git_text(commit: str, path: str) -> str:
    return subprocess.check_output(
        ["git", "show", f"{commit}:{path}"], cwd=REPO_ROOT, text=True, encoding="utf-8"
    )


def tree_files(commit: str, directory: str) -> list[str]:
    output = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", commit, directory],
        cwd=REPO_ROOT,
        text=True,
        encoding="utf-8",
    )
    return [line.strip() for line in output.splitlines() if line.strip().endswith(".json")]


def shares_from_payload(payload: dict) -> dict[str, list[float]]:
    shares = {}
    for district, row in ((payload.get("general") or {}).get("results") or {}).items():
        values = [int(row.get(field) or 0) for field in FIELDS]
        total = sum(values)
        if total:
            shares[str(int(district))] = [round(value / total, 10) for value in values]
    return shares


def extract_directory(commit: str, directory: str, expected_scope: str) -> dict[str, dict]:
    contests = {}
    for path in tree_files(commit, directory):
        match = FILE_RE.match(Path(path).name)
        if not match or match.group(1) != expected_scope:
            continue
        contest_key = f"{match.group(2)}_{match.group(3)}"
        payload = json.loads(git_text(commit, path))
        shares = shares_from_payload(payload)
        if shares:
            contests[contest_key] = {"source_file": path, "shares": shares}
    return contests


def extract_local_directory(directory: Path, expected_scope: str) -> dict[str, dict]:
    """Fill gaps from on-disk files (e.g. contests added after the baseline commit)."""
    contests = {}
    if not directory.is_dir():
        return contests
    for path in sorted(directory.glob("*.json")):
        match = FILE_RE.match(path.name)
        if not match or match.group(1) != expected_scope:
            continue
        if match.group(4):
            continue  # skip *_2022_lines / *_2024_lines copies in subdirs if present
        contest_key = f"{match.group(2)}_{match.group(3)}"
        payload = json.loads(path.read_text(encoding="utf-8"))
        shares = shares_from_payload(payload)
        if shares:
            contests[contest_key] = {
                "source_file": path.relative_to(REPO_ROOT).as_posix(),
                "shares": shares,
            }
    return contests


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default="285202983cdee267c6a34cca0ad6dde912938159")
    parser.add_argument("--out", default="data/district_contests/district_snapshot_targets.json")
    args = parser.parse_args()

    congressional = extract_directory(args.commit, "data/district_contests", "congressional")
    state_senate = extract_directory(args.commit, "data/district_contests", "state_senate")
    state_house_root = extract_directory(args.commit, "data/district_contests", "state_house")
    # Contests present on disk as root 2024-lines originals but missing from the baseline commit.
    for key, value in extract_local_directory(REPO_ROOT / "data/district_contests", "state_house").items():
        state_house_root.setdefault(key, value)
    lines_2022 = extract_directory(args.commit, "data/district_contests/state_house_2022_lines", "state_house")
    # Root State House district contests are the 2024-lines originals.
    lines_2024 = dict(state_house_root)
    output = {
        "meta": {
            "baseline_commit": args.commit,
            "method": "party shares extracted from pre-rebuild committed district JSONs",
            "notes": {
                "state_house_2024": "Root data/district_contests/state_house_*.json are the 2024-lines originals.",
            },
            "field_order": list(FIELDS),
        },
        "geometries": {
            "congressional": congressional,
            "state_senate_2022": state_senate,
            "state_house_root": state_house_root,
            "state_house_2022": lines_2022,
            "state_house_2024": lines_2024,
        },
    }
    out_path = REPO_ROOT / args.out
    out_path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print("wrote", out_path.relative_to(REPO_ROOT), {key: len(value) for key, value in output["geometries"].items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
