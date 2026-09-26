#!/usr/bin/env python3
"""Audit congressional placement using district labels on 2022 U.S. House ballots."""

from __future__ import annotations

import sys

from audit_2022_house_ballot_geography import main


if __name__ == "__main__":
    raise SystemExit(main(["--office", "U.S. House", *sys.argv[1:]]))
