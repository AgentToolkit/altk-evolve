#!/usr/bin/env python3
"""
check_tests.py — quality gate for the evolve-lite skill test suite.

Runs the content-evaluation test and the recall test against existing fixtures,
then fails with exit code 1 if either suite falls below the required pass-rate
threshold.

Usage:
    python3 check_tests.py                    # default threshold: 0.8
    python3 check_tests.py --threshold 0.9    # stricter gate
    python3 check_tests.py --threshold 1.0    # all must pass
    python3 check_tests.py --verbose          # per-skill detail
    python3 check_tests.py --report <path>    # write gate report JSON here
    python3 check_tests.py --pseudo-conversations-dir <path>

Exit codes:
    0  both suites meet or exceed the threshold
    1  one or both suites fell below the threshold (or no fixtures found)
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_script = Path(__file__).resolve()
_scripts_dir = _script.parent
_eval_script      = _scripts_dir / "run_skill_evaluation.py"
_recall_script    = _scripts_dir / "run_recall_tests.py"
_baseline_script  = _scripts_dir / "run_baseline_tests.py"

# Walk up to find .evolve dir helper (graceful fallback)
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


def _run_suite(script, extra_args, label):
    """Run a test-suite script and return its exit code."""
    cmd = [sys.executable, str(script)] + extra_args
    print(f"\n{'─' * 60}")
    print(f"  {label}")
    print(f"{'─' * 60}")
    result = subprocess.run(cmd)
    return result.returncode


def _read_pass_rate(report_path, rate_key="pass_rate"):
    """Read a pass_rate from a JSON report. Returns None if missing."""
    p = Path(report_path)
    if not p.exists():
        return None
    try:
        with open(p) as fh:
            data = json.load(fh)
        return data.get(rate_key)
    except Exception:
        return None


def _read_pass_counts(report_path):
    """Return (passed, total) from a JSON report."""
    p = Path(report_path)
    if not p.exists():
        return None, None
    try:
        with open(p) as fh:
            data = json.load(fh)
        return data.get("passed"), data.get("total")
    except Exception:
        return None, None


def main():
    parser = argparse.ArgumentParser(
        description="Quality gate: run content + recall tests and enforce a minimum pass-rate"
    )
    parser.add_argument(
        "--threshold", type=float, default=0.8,
        help="Minimum required pass rate for both suites (0.0–1.0, default: 0.8)",
    )
    parser.add_argument(
        "--pseudo-conversations-dir", default=None,
        help="Path to pseudo-conversation fixtures directory",
    )
    parser.add_argument(
        "--report", default=None,
        help="Path to write the gate summary JSON report",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Pass --verbose to both sub-runners",
    )
    args = parser.parse_args()

    evolve_dir = get_evolve_dir()

    eval_report     = evolve_dir / "tests" / "evaluation" / "report.json"
    recall_report   = evolve_dir / "tests" / "evaluation" / "recall_report.json"
    baseline_report = evolve_dir / "tests" / "evaluation" / "baseline_report.json"
    gate_report     = Path(args.report) if args.report \
        else evolve_dir / "tests" / "evaluation" / "gate_report.json"

    # ── build shared sub-runner args ─────────────────────────────────────────
    common = []
    if args.pseudo_conversations_dir:
        common += ["--pseudo-conversations-dir", args.pseudo_conversations_dir]
    if args.verbose:
        common.append("--verbose")

    # ── run content-evaluation ───────────────────────────────────────────────
    _run_suite(_eval_script, common + ["--report", str(eval_report)],
               "Content Evaluation (skill self-consistency)")

    # ── run recall test ──────────────────────────────────────────────────────
    _run_suite(_recall_script, common + ["--report", str(recall_report)],
               "Recall Test (trigger surfacing)")

    # ── run baseline test ────────────────────────────────────────────────────
    _run_suite(_baseline_script, common + ["--simulate", "--report", str(baseline_report)],
               "Baseline Test (skill necessity)")

    # ── read results ─────────────────────────────────────────────────────────
    eval_rate   = _read_pass_rate(eval_report,   rate_key="pass_rate")
    recall_rate = _read_pass_rate(recall_report, rate_key="recall_at_3")

    eval_passed,   eval_total    = _read_pass_counts(eval_report)
    recall_passed, recall_total  = _read_pass_counts(recall_report)

    # Baseline gate: necessity_rate = skill_necessary_count / total
    # Passes when >= 80% of skills are necessary (agent fails without the skill)
    baseline_data = {}
    _bp = Path(baseline_report)
    if _bp.exists():
        try:
            import json as _json
            with open(_bp) as _fh:
                baseline_data = _json.load(_fh)
        except Exception:
            pass
    _b_total = baseline_data.get("total") or 0
    _b_necessary = baseline_data.get("skill_necessary_count") or 0
    necessity_rate = round(_b_necessary / _b_total, 4) if _b_total else None
    baseline_passed_count  = _b_necessary
    baseline_total_count   = _b_total

    threshold = args.threshold
    eval_ok   = eval_rate   is not None and eval_rate   >= threshold
    recall_ok = recall_rate is not None and recall_rate >= threshold
    baseline_ok = necessity_rate is not None and necessity_rate >= 0.8

    overall_passed = eval_ok and recall_ok and baseline_ok

    # ── print gate summary ───────────────────────────────────────────────────
    print(f"\n{'═' * 60}")
    print("  TEST GATE SUMMARY")
    print(f"{'═' * 60}")
    print(f"  Threshold : {threshold:.0%}")
    print()

    def _fmt(label, rate, passed, total, ok, threshold_pct=None):
        mark = "✅" if ok else "❌"
        pct  = f"{rate:.1%}" if rate is not None else "n/a"
        cnt  = f"{passed}/{total}" if passed is not None else "n/a"
        need = threshold_pct if threshold_pct is not None else threshold
        req  = "pass" if ok else f"FAIL (need ≥{need:.0%})"
        print(f"  {mark} {label:<28}  {pct}  ({cnt})  {req}")

    _fmt("Content evaluation", eval_rate,    eval_passed,           eval_total,         eval_ok)
    _fmt("Recall test",        recall_rate,  recall_passed,         recall_total,       recall_ok)
    _fmt("Baseline (necessity)", necessity_rate, baseline_passed_count, baseline_total_count, baseline_ok, threshold_pct=0.8)

    print()
    if overall_passed:
        print("  ✅  Gate PASSED — all suites meet the threshold.")
    else:
        print("  ❌  Gate FAILED — fix failing skills before continuing.")
    print(f"{'═' * 60}")

    # ── write gate report ────────────────────────────────────────────────────
    gate_report.parent.mkdir(parents=True, exist_ok=True)
    report_data = {
        "generated_at":   datetime.now().isoformat(),
        "threshold":       threshold,
        "overall_passed":  overall_passed,
        "suites": {
            "content_evaluation": {
                "report":      str(eval_report),
                "pass_rate":   eval_rate,
                "passed":      eval_passed,
                "total":       eval_total,
                "gate_passed": eval_ok,
            },
            "recall_test": {
                "report":      str(recall_report),
                "pass_rate":   recall_rate,
                "passed":      recall_passed,
                "total":       recall_total,
                "gate_passed": recall_ok,
            },
            "baseline_test": {
                "report":          str(baseline_report),
                "necessity_rate":  necessity_rate,
                "necessary_count": baseline_passed_count,
                "total":           baseline_total_count,
                "gate_passed":     baseline_ok,
            },
        },
    }
    with open(gate_report, "w") as fh:
        json.dump(report_data, fh, indent=2)
    print(f"  Gate report: {gate_report}")

    sys.exit(0 if overall_passed else 1)


if __name__ == "__main__":
    main()

# Made with Bob
