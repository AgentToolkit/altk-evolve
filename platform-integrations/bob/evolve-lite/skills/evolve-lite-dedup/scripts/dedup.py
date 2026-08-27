#!/usr/bin/env python3
"""
evolve-lite dedup — two-phase skill library deduplication

Phase 1 (quality_gate.py):
  Runs ALL quality checks on every entity:
    • Format check   – required frontmatter, valid type, non-empty content
    • Recall test    – skill ranks in top-3 for its own trigger scenario
    • Skill eval     – content is self-consistent (must_include terms present);
                       skill-flow atomic_skills references resolve
  Blocks Phase 2 if any skill fails.

Phase 2 (refine.py):
  Groups entities by token-set Jaccard similarity. For each cluster:
    • keep-all  – skills are distinct enough
    • merge     – combine similar skills into one enriched entity
    • discard   – remove near-identical duplicates, keep the richest

Usage:
    python3 dedup.py                   # full pipeline, auto decisions
    python3 dedup.py --interactive     # Phase 2 prompts for each cluster
    python3 dedup.py --dry-run         # show all decisions, write nothing
    python3 dedup.py --phase1-only     # quality gate only, no refinement
    python3 dedup.py --phase2-only     # skip quality gate (use with care)
    python3 dedup.py --threshold 0.5   # override similarity threshold
    python3 dedup.py --report-dir <d>  # write both reports to this directory
    python3 dedup.py --verbose         # print every skill in Phase 1
"""

import argparse
import subprocess
import sys
from pathlib import Path

# Locate sibling scripts
_script = Path(__file__).resolve()
_scripts_dir = _script.parent
_quality_gate = _scripts_dir / "quality_gate.py"
_refine = _scripts_dir / "refine.py"


def run_script(script, extra_args, label):
    """Run a python3 script as a subprocess. Returns its exit code."""
    cmd = [sys.executable, str(script)] + extra_args
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}")
    result = subprocess.run(cmd)
    return result.returncode


def main():
    parser = argparse.ArgumentParser(
        description="Two-phase skill library deduplication"
    )
    parser.add_argument("--manifest-dir", default=None,
                        help="Build recall manifest from this directory instead of the live .evolve/entities/. "
                             "Forwarded to quality_gate.py.")
    parser.add_argument("--phase1-only", action="store_true",
                        help="Run quality gate only, skip refinement")
    parser.add_argument("--phase2-only", action="store_true",
                        help="Skip quality gate, run refinement only")
    parser.add_argument("--interactive", action="store_true",
                        help="Prompt for decisions in Phase 2")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show all decisions without writing changes")
    parser.add_argument("--threshold", type=float, default=None,
                        help="Jaccard similarity threshold for Phase 2 (default: 0.45)")
    parser.add_argument("--entities-dir", default=None,
                        help="Override entities directory")
    parser.add_argument("--report-dir", default=None,
                        help="Directory for JSON reports (default: .evolve/tests/dedup/)")
    parser.add_argument("--verbose", action="store_true",
                        help="Print every skill result in Phase 1")
    parser.add_argument("--local-only", action="store_true",
                        help="Score private entities only (forwards to quality_gate.py)")
    args = parser.parse_args()

    # Resolve report dir
    if args.report_dir:
        report_dir = Path(args.report_dir)
    else:
        # Walk up to find .evolve
        from pathlib import Path as _P
        evolve = _P(".evolve")
        report_dir = evolve / "tests" / "dedup"
    report_dir.mkdir(parents=True, exist_ok=True)

    p1_report = report_dir / "quality_gate_report.json"
    p2_report = report_dir / "refine_report.json"

    # -----------------------------------------------------------------------
    # Phase 1 – Quality Gate
    # -----------------------------------------------------------------------
    phase1_passed = True
    if not args.phase2_only:
        p1_args = ["--report", str(p1_report)]
        if args.entities_dir:
            p1_args += ["--entities-dir", args.entities_dir]
        if args.manifest_dir:
            p1_args += ["--manifest-dir", args.manifest_dir]
        if args.verbose:
            p1_args.append("--verbose")
        if args.local_only:
            p1_args.append("--local-only")

        rc = run_script(_quality_gate, p1_args, "PHASE 1 — Quality Gate")
        if rc != 0:
            phase1_passed = False
            print()
            print("❌  Phase 1 failed. Resolve quality issues before running Phase 2.")
            print(f"    Report: {p1_report}")
            if not args.phase1_only:
                print("    Phase 2 (refine) was NOT run.")
            sys.exit(1)
        print()
        print("✅  Phase 1 passed.")

    if args.phase1_only:
        print(f"\nReport: {p1_report}")
        sys.exit(0)

    # -----------------------------------------------------------------------
    # Phase 2 – Refine
    # -----------------------------------------------------------------------
    p2_args = ["--report", str(p2_report)]
    if args.entities_dir:
        p2_args += ["--entities-dir", args.entities_dir]
    if args.interactive:
        p2_args.append("--interactive")
    if args.dry_run:
        p2_args.append("--dry-run")
    if args.threshold is not None:
        p2_args += ["--threshold", str(args.threshold)]

    rc = run_script(_refine, p2_args, "PHASE 2 — Refine (Deduplication)")
    if rc != 0:
        print("\n❌  Phase 2 encountered an error.")
        sys.exit(rc)

    print()
    print("✅  Dedup complete.")
    print(f"    Phase 1 report : {p1_report}")
    print(f"    Phase 2 report : {p2_report}")
    sys.exit(0)


if __name__ == "__main__":
    main()

# Made with Bob
