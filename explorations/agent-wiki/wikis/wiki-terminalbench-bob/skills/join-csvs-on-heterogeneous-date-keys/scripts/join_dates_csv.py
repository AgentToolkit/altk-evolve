#!/usr/bin/env python3
"""Join two CSVs on a date column with heterogeneous date formats.

Usage: join_dates_csv.py <file_a.csv> <file_b.csv> <out.txt>

file_a uses a single ISO date format; file_b's date column may mix
formats and row order. Both have columns: date, value. Edit DATE_FMT_A,
FMT_FALLBACKS, and the aggregate to fit your data.
"""
import csv
import sys
from datetime import datetime

DATE_FMT_A = "%Y-%m-%d"
FMT_FALLBACKS = ["%m/%d/%Y %H:%M:%S", "%m-%d-%Y %H:%M:%S", "%Y-%m-%d"]
VALUE_COL = "temperature"
DATE_COL = "date"


def parse_with_fallback(s, fmts):
    for fmt in fmts:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"no format matched: {s!r}")


def load(path, fmts):
    out = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            key = parse_with_fallback(row[DATE_COL].strip(), fmts)
            out[key] = float(row[VALUE_COL].strip())
    return out


def main(a_path, b_path, out_path):
    a = load(a_path, [DATE_FMT_A])
    b = load(b_path, FMT_FALLBACKS)
    diffs = [a[d] - b[d] for d in a if d in b]
    avg = sum(diffs) / len(diffs)
    with open(out_path, "w") as f:
        f.write(str(avg))
    return avg


if __name__ == "__main__":
    print(main(sys.argv[1], sys.argv[2], sys.argv[3]))
