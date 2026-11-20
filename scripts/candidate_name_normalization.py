"""Normalize candidate labels in generated election JSON without changing votes."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVED_DIRS = (
    ROOT / "data/contests",
    ROOT / "data/contests_2025_crosswalked",
    ROOT / "data/district_contests",
)
CANDIDATE_FIELD = re.compile(
    r'(?P<before>"(?:dem_candidate|rep_candidate)"\s*:\s*")'
    r'(?P<value>(?:\\.|[^"\\])*)(?P<after>")'
)
INITIAL = re.compile(r"(?<![A-Za-z])([A-Z])(?=\s)")
SUFFIX = re.compile(r"\s*,?\s*(Jr|Sr|III|II|IV)\.?$", re.IGNORECASE)


def normalize_candidate_name(name: str) -> str:
    value = re.sub(r"\s+", " ", name).strip()
    value = re.sub(r"^(?:DEM|REP)\s+", "", value)
    value = INITIAL.sub(r"\1.", value)
    match = SUFFIX.search(value)
    if match:
        suffix = match.group(1).upper()
        suffix = {"JR": "Jr.", "SR": "Sr."}.get(suffix, suffix)
        value = f"{value[:match.start()].rstrip().rstrip(',')}, {suffix}"
    return value


def normalize_candidate_fields(value: object) -> object:
    """Normalize candidate fields in a generated payload before writing it."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"dem_candidate", "rep_candidate"} and isinstance(item, str):
                value[key] = normalize_candidate_name(item)
            else:
                normalize_candidate_fields(item)
    elif isinstance(value, list):
        for item in value:
            normalize_candidate_fields(item)
    return value


def normalize_json_text(content: str) -> tuple[str, int]:
    changes = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal changes
        original = json.loads('"' + match.group("value") + '"')
        normalized = normalize_candidate_name(original)
        if normalized == original:
            return match.group(0)
        changes += 1
        encoded = json.dumps(normalized, ensure_ascii=True)[1:-1]
        return match.group("before") + encoded + match.group("after")

    return CANDIDATE_FIELD.sub(replace, content), changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Update served JSON files in place")
    args = parser.parse_args()
    files = fields = 0
    for root in SERVED_DIRS:
        for path in root.rglob("*.json"):
            content = path.read_text(encoding="utf-8")
            updated, count = normalize_json_text(content)
            if count:
                files += 1
                fields += count
                if args.write:
                    with path.open("w", encoding="utf-8", newline="\n") as handle:
                        handle.write(updated)
    print(f"{'normalized' if args.write else 'would normalize'} {fields} candidate labels in {files} JSON files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
