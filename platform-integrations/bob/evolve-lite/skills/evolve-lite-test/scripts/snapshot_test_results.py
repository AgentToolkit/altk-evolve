#!/usr/bin/env python3
"""
snapshot_test_results.py — capture current test pass counts for regression comparison.

Reads the existing content-evaluation and recall-test reports and writes a
compact snapshot JSON that can be compared against a later run to detect
regressions (e.g. after a dedup operation).

Usage:
    # Save a snapshot before dedup:
    python3 snapshot_test_results.py --out .evolve/tests/evaluation/pre_dedup_snapshot.json

    # After dedup, run tests and then compare:
    python3 check_tests.py                     # regenerates report.json + recall_report.json
    python3 snapshot_test_results.py \\
        --compare .evolve/tests/evaluation/pre_dedup_snapshot.json

    # Or compare inline:
    python3 snapshot_test_results.py \\
        --compare .evolve/tests/evaluation/pre_dedup_snapshot.json \\
        --out     .evolve/tests/evaluation/post_dedup_snapshot.json

Exit codes:
    0   no regression (pass counts are equal or better than the snapshot)
    1   regression detected (fewer tests pass after the operation)
    2   snapshot or current reports missing
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Bootstrap to find .evolve dir
_script = Path(__file__).resolve()
_lib = None
for _ancestor in _script.parents:
    _candidate = _ancestor / "lib" / "evolve-lite"
    if (_candidate / "entity_io.py").is_file():
        _lib = _candidate
        break
if _lib:
    sys.path.insert(0, str(_lib))
    try:
        from entity_io import get_evolve_dir
    except ImportError:
        def get_evolve_dir(): return Path(".evolve")
else:
    def get_evolve_dir(): return Path(".evolve")


def _read_counts(report_path):
    """Return dict with passed/total/pass_rate from a report, or None."""
    p = Path(report_path)
    if not p.exists():
        return None
    try:
        with open(p) as fh:
            data = json.load(fh)
        return {
            "passed":    data.get("passed"),
            "total":     data.get("total"),
            "pass_rate": data.get("pass_rate"),
            "report":    str(p),
        }
    except Exception:
        return None


def build_snapshot(eval_report, recall_report):
    """Read both reports and build a snapshot dict."""
    eval_data   = _read_counts(eval_report)
    recall_data = _read_counts(recall_report)
    return {
        "captured_at":   datetime.now().isoformat(),
        "content_eval":  eval_data,
        "recall_test":   recall_data,
    }


def compare_snapshots(before, after):
    """
    Compare two snapshots.  Returns (ok, lines) where ok is True when no
    regression was found and lines is a list of human-readable result strings.
    """
    ok = True
    lines = []

    for key, label in [("content_eval", "Content evaluation"),
                        ("recall_test",  "Recall test")]:
        b = before.get(key) or {}
        a = after.get(key)  or {}

        b_passed = b.get("passed")
        a_passed = a.get("passed")
        b_total  = b.get("total")
        a_total  = a.get("total")

        if b_passed is None or a_passed is None:
            lines.append(f"  ⚠  {label}: missing data — cannot compare")
            continue

        delta = a_passed - b_passed
        if delta < 0:
            ok = False
            mark = "❌"
            status = f"REGRESSION  {b_passed}→{a_passed} passed  (Δ{delta})"
        elif delta == 0:
            mark = "✅"
            status = f"no regression  {a_passed}/{a_total} passed"
        else:
            mark = "✅"
            status = f"improved  {b_passed}→{a_passed} passed  (+{delta})"

        lines.append(f"  {mark} {label:<28}  {status}")

    return ok, lines


def main():
    parser = argparse.ArgumentParser(
        description="Snapshot current test results and optionally compare against a prior snapshot"
    )
    parser.add_argument(
        "--out", default=None,
        help="Path to write the new snapshot JSON (default: .evolve/tests/evaluation/snapshot.json)",
    )
    parser.add_argument(
        "--compare", default=None,
        metavar="SNAPSHOT_PATH",
        help="Path to a prior snapshot JSON.  When provided, compares current results against it "
             "and exits 1 if a regression is found.",
    )
    parser.add_argument(
        "--eval-report", default=None,
        help="Override path to content-evaluation report.json",
    )
    parser.add_argument(
        "--recall-report", default=None,
        help="Override path to recall_report.json",
    )
    args = parser.parse_args()

    evolve_dir = get_evolve_dir()

    eval_report   = Path(args.eval_report)   if args.eval_report   \
        else evolve_dir / "tests" / "evaluation" / "report.json"
    recall_report = Path(args.recall_report) if args.recall_report \
        else evolve_dir / "tests" / "evaluation" / "recall_report.json"

    out_path = Path(args.out) if args.out \
        else evolve_dir / "tests" / "evaluation" / "snapshot.json"

    # ── build current snapshot ───────────────────────────────────────────────
    snapshot = build_snapshot(eval_report, recall_report)

    missing = []
    if snapshot["content_eval"] is None:
        missing.append(str(eval_report))
    if snapshot["recall_test"] is None:
        missing.append(str(recall_report))

    if missing:
        print("Error: the following reports are missing — run check_tests.py first:", file=sys.stderr)
        for m in missing:
            print(f"  {m}", file=sys.stderr)
        sys.exit(2)

    # ── write snapshot ───────────────────────────────────────────────────────
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as fh:
        json.dump(snapshot, fh, indent=2)
    print(f"Snapshot written: {out_path}")

    # ── compare (optional) ───────────────────────────────────────────────────
    if args.compare:
        compare_path = Path(args.compare)
        if not compare_path.exists():
            print(f"Error: comparison snapshot not found: {compare_path}", file=sys.stderr)
            sys.exit(2)

        with open(compare_path) as fh:
            before = json.load(fh)

        ok, lines = compare_snapshots(before, snapshot)

        print()
        print("═" * 60)
        print("  REGRESSION CHECK")
        print(f"  Before : {before.get('captured_at', '?')}")
        print(f"  After  : {snapshot['captured_at']}")
        print("═" * 60)
        for line in lines:
            print(line)
        print()
        if ok:
            print("  ✅  No regressions detected.")
        else:
            print("  ❌  Regressions detected — review dedup changes.")
        print("═" * 60)

        sys.exit(0 if ok else 1)

    sys.exit(0)


if __name__ == "__main__":
    main()

# Made with Bob
