#!/usr/bin/env python3
"""Count CSV rows that contain a literal comma in at least one field.
Uses stdlib `csv.reader` with the required `newline=''` open argument so
embedded newlines inside quoted fields don't break row boundaries.

Usage: python3 count.py <csv-path>
"""

from __future__ import annotations
import csv, sys


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: count.py <csv-path>", file=sys.stderr)
        return 2
    n = 0
    with open(sys.argv[1], newline="") as f:
        next(csv.reader(f), None)  # skip header
        for row in csv.reader(f):
            if any("," in field for field in row):
                n += 1
    print(n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
