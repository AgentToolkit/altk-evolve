#!/usr/bin/env python3
"""
Skill Evaluation Runner

Loads each pseudo-conversation fixture from .evolve/tests/pseudo_conversations/,
simulates what a sub-agent following the recalled skill should produce,
evaluates the response against expected_behaviour, and writes per-skill
results + a summary report.

Since this is a Python-only runner (no live LLM call), the "sub-agent response"
is simulated by checking whether the skill content itself contains the
must_include terms — which is the minimal faithful check: a well-formed skill
that contains its own prescribed commands will pass; a skill that has gaps will
surface them.

For a live LLM-backed run, replace `simulate_agent_response()` with a real
API call and pass the conversation list to it.

Usage:
    python run_skill_evaluation.py
    python run_skill_evaluation.py --verbose
    python run_skill_evaluation.py --pseudo-conversations-dir <path>
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: locate entity_io for the log helper (optional, graceful fallback)
# ---------------------------------------------------------------------------
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
        from entity_io import get_evolve_dir, log as _elog
        def log(msg):
            _elog("skill-eval", msg)
    except ImportError:
        def log(msg):
            pass
        def get_evolve_dir():
            return Path(".evolve")
else:
    def log(msg):
        pass
    def get_evolve_dir():
        return Path(".evolve")


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def _approx_tokens(text):
    """Approximate token count using the GPT-3/4 rule-of-thumb: ~0.75 words per token.

    For live LLM calls replace this with the actual usage object returned by the API:
        usage = response.usage
        return usage.prompt_tokens, usage.completion_tokens
    """
    return max(1, round(len(text.split()) / 0.75))


def estimate_tokens(fixture, response):
    """Return a detailed token breakdown measured from the raw input messages.

    The system message already contains the injected skill inside a
    <recalled_skill> block. We split on the *last* occurrence of that tag
    (the preamble itself mentions it by name, so the first hit is a reference,
    not the actual injection) to get three named buckets:

      preamble_tokens  — fixed system instructions before the skill injection
      skill_tokens     — the recalled skill text as it appears in context
      user_tokens      — the user's question

    This makes the skill's context cost directly visible rather than hidden
    inside an opaque prompt_tokens total.
    """
    system_content = next(
        (m["content"] for m in fixture.get("conversation", []) if m["role"] == "system"),
        "",
    )
    user_content = next(
        (m["content"] for m in fixture.get("conversation", []) if m["role"] == "user"),
        "",
    )

    # Split at the *last* <recalled_skill> tag to isolate the injected skill
    tag = "<recalled_skill>"
    last_idx = system_content.rfind(tag)
    if last_idx != -1:
        preamble_text = system_content[:last_idx]
        rest = system_content[last_idx + len(tag):]
        skill_text, _, _ = rest.partition("</recalled_skill>")
    else:
        preamble_text = system_content
        skill_text = ""

    preamble_tokens   = _approx_tokens(preamble_text) if preamble_text else 0
    skill_tokens      = _approx_tokens(skill_text)    if skill_text    else 0
    user_tokens       = _approx_tokens(user_content)  if user_content  else 0
    prompt_tokens     = preamble_tokens + skill_tokens + user_tokens
    completion_tokens = _approx_tokens(response)

    return {
        "preamble_tokens":   preamble_tokens,
        "skill_tokens":      skill_tokens,
        "user_tokens":       user_tokens,
        "prompt_tokens":     prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens":      prompt_tokens + completion_tokens,
        "token_source":      "estimated",
    }


# ---------------------------------------------------------------------------
# Simulated sub-agent response
# ---------------------------------------------------------------------------

def simulate_agent_response(fixture):
    """
    Simulate a sub-agent that has been given the skill in context and responds
    to the user's question.

    The simulation uses the skill content verbatim as the "agent response"
    because: if the skill content contains all the must_include terms, a
    well-instructed agent following it would too.  This makes the test a
    self-consistency check: the skill must contain what it claims to prescribe.

    Replace this function with a real LLM call for a live evaluation:

        messages = fixture["conversation"]
        t0 = time.perf_counter()
        response = openai_client.chat.completions.create(model="gpt-4o", messages=messages)
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        text = response.choices[0].message.content
        prompt_tokens = response.usage.prompt_tokens
        completion_tokens = response.usage.completion_tokens
        return text, latency_ms, prompt_tokens, completion_tokens
    """
    skill_content = fixture.get("skill_content", "")
    return (
        "Based on the recalled skill, here is the guidance:\n\n"
        + skill_content
    )


# ---------------------------------------------------------------------------
# Alignment evaluator
# ---------------------------------------------------------------------------

def _normalise(text):
    """Strip angle-bracket placeholders and lower-case for matching."""
    return re.sub(r"\s*<[^>]+>", "", text).strip().lower()


def evaluate_response(response, expected_behaviour):
    """
    Check the agent response against must_include and must_not_include lists.

    Angle-bracket placeholders (e.g. <env-name>) are stripped from both the
    term and the response before matching so that commands with variable
    argument slots still match correctly.

    Returns a dict with:
        matched         - list of terms found
        missed          - list of terms not found
        violated        - list of must_not_include terms found
        alignment_score - float [0, 1]
        constraint_violated - bool
        passed          - bool
    """
    must_include = expected_behaviour.get("must_include", [])
    must_not_include = expected_behaviour.get("must_not_include", [])
    resp_norm = _normalise(response)

    matched = [t for t in must_include if _normalise(t) in resp_norm]
    missed = [t for t in must_include if _normalise(t) not in resp_norm]
    violated = [t for t in must_not_include if _normalise(t) in resp_norm]

    if must_include:
        alignment_score = len(matched) / len(must_include)
    else:
        alignment_score = 1.0

    constraint_violated = len(violated) > 0
    passed = alignment_score >= 0.5 and not constraint_violated

    return {
        "matched": matched,
        "missed": missed,
        "violated": violated,
        "alignment_score": round(alignment_score, 4),
        "constraint_violated": constraint_violated,
        "passed": passed,
    }


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_evaluation(pseudo_conv_dir, results_dir, verbose):
    """Load fixtures, evaluate each, return list of result dicts."""
    pseudo_conv_dir = Path(pseudo_conv_dir)
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)

    fixture_files = sorted(pseudo_conv_dir.glob("*.json"))
    if not fixture_files:
        print(f"No fixture files found in {pseudo_conv_dir}", file=sys.stderr)
        sys.exit(1)

    results = []

    for fixture_file in fixture_files:
        log(f"Evaluating: {fixture_file.name}")
        with open(fixture_file, "r", encoding="utf-8") as fh:
            fixture = json.load(fh)

        test_id = fixture.get("test_id", fixture_file.stem)

        # Get agent response and measure latency
        t0 = time.perf_counter()
        agent_response = simulate_agent_response(fixture)
        latency_ms = round((time.perf_counter() - t0) * 1000, 3)

        # Estimate token usage (broken down by input section)
        tok = estimate_tokens(fixture, agent_response)

        # Evaluate
        eval_result = evaluate_response(agent_response, fixture["expected_behaviour"])

        result = {
            "test_id": test_id,
            "passed": eval_result["passed"],
            "alignment_score": eval_result["alignment_score"],
            "matched": eval_result["matched"],
            "missed": eval_result["missed"],
            "violated": eval_result["violated"],
            "constraint_violated": eval_result["constraint_violated"],
            "agent_response": agent_response,
            "metrics": {
                "latency_ms": latency_ms,
                "preamble_tokens":   tok["preamble_tokens"],
                "skill_tokens":      tok["skill_tokens"],
                "user_tokens":       tok["user_tokens"],
                "prompt_tokens":     tok["prompt_tokens"],
                "completion_tokens": tok["completion_tokens"],
                "total_tokens":      tok["total_tokens"],
                "token_source":      tok["token_source"],
            },
            "timestamp": datetime.now().isoformat(),
        }

        # Write per-skill result
        result_file = results_dir / f"{test_id}.json"
        with open(result_file, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)

        results.append(result)

        if verbose:
            status = "✅" if result["passed"] else "❌"
            m = result["metrics"]
            print(
                f"  {status} {test_id:<56}"
                f"  score={result['alignment_score']:.2f}"
                f"  matched={len(result['matched'])}/{len(result['matched']) + len(result['missed'])}"
                f"  tokens={m['total_tokens']}  latency={m['latency_ms']}ms"
            )
            if result["missed"]:
                print(f"       missed={result['missed']}")
            if result["violated"]:
                print(f"       violated={result['violated']}")

    return results


def _percentile(values, pct):
    """Return the p-th percentile of a sorted list (linear interpolation)."""
    if not values:
        return 0.0
    sv = sorted(values)
    idx = (pct / 100) * (len(sv) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(sv) - 1)
    return round(sv[lo] + (idx - lo) * (sv[hi] - sv[lo]), 3)


def build_report(results):
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed
    pass_rate = round(passed / total, 4) if total else 0.0

    latencies          = [r["metrics"]["latency_ms"]        for r in results]
    preamble_tokens    = [r["metrics"]["preamble_tokens"]   for r in results]
    skill_tokens       = [r["metrics"]["skill_tokens"]      for r in results]
    user_tokens        = [r["metrics"]["user_tokens"]       for r in results]
    prompt_tokens      = [r["metrics"]["prompt_tokens"]     for r in results]
    completion_tokens  = [r["metrics"]["completion_tokens"] for r in results]
    total_tokens       = [r["metrics"]["total_tokens"]      for r in results]

    token_source = results[0]["metrics"]["token_source"] if results else "estimated"

    def _avg(lst): return round(sum(lst) / len(lst), 1) if lst else 0

    return {
        "generated_at": datetime.now().isoformat(),
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": pass_rate,
        "performance": {
            "token_source": token_source,
            "latency_ms": {
                "min": round(min(latencies), 3) if latencies else 0,
                "max": round(max(latencies), 3) if latencies else 0,
                "avg": round(sum(latencies) / len(latencies), 3) if latencies else 0,
                "p50": _percentile(latencies, 50),
                "p95": _percentile(latencies, 95),
            },
            "tokens": {
                "total_prompt":        sum(prompt_tokens),
                "total_completion":    sum(completion_tokens),
                "total_all":           sum(total_tokens),
                "avg_prompt":          _avg(prompt_tokens),
                "avg_completion":      _avg(completion_tokens),
                "avg_total":           _avg(total_tokens),
                # skill-specific breakdown
                "avg_preamble":        _avg(preamble_tokens),
                "avg_skill":           _avg(skill_tokens),
                "avg_user":            _avg(user_tokens),
                "total_skill":         sum(skill_tokens),
                "skill_pct_of_prompt": round(
                    100 * sum(skill_tokens) / sum(prompt_tokens), 1
                ) if sum(prompt_tokens) else 0,
            },
        },
        "results": results,
    }


def print_table(results, report):
    print()
    print("SKILL EVALUATION RESULTS")
    print("=" * 80)
    for r in results:
        status = "✅" if r["passed"] else "❌"
        total_inc = len(r["matched"]) + len(r["missed"])
        m = r["metrics"]
        line = (
            f"{status} {r['test_id']:<52}"
            f"  score={r['alignment_score']:.2f}"
            f"  matched={len(r['matched'])}/{total_inc}"
            f"  violated={len(r['violated'])}"
            f"  tok={m['total_tokens']}"
            f"  {m['latency_ms']}ms"
        )
        print(line)
        if r["missed"]:
            print(f"     missed={r['missed']}")
        if r["violated"]:
            print(f"     violated={r['violated']}")

    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    pct = (passed / total * 100) if total else 0
    perf = report["performance"]
    lat = perf["latency_ms"]
    tok = perf["tokens"]

    print()
    print(f"SUMMARY: {passed}/{total} passed ({pct:.1f}%)")
    print()
    print("PERFORMANCE")
    print("-" * 50)
    src = f"  ({perf['token_source']})"
    print(f"  Latency (ms)  avg={lat['avg']}  p50={lat['p50']}  p95={lat['p95']}  min={lat['min']}  max={lat['max']}")
    print(f"  Tokens{src}")
    print(f"    avg prompt breakdown:")
    print(f"      preamble : {tok['avg_preamble']} tokens")
    print(f"      skill    : {tok['avg_skill']} tokens  ({tok['skill_pct_of_prompt']}% of prompt)")
    print(f"      user     : {tok['avg_user']} tokens")
    print(f"    avg completion : {tok['avg_completion']} tokens")
    print(f"    avg total      : {tok['avg_total']} tokens  (prompt + completion)")
    print(f"    total (all)    : {tok['total_all']} tokens  (skill: {tok['total_skill']})")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Run atomic skill pseudo-conversation evaluation"
    )
    parser.add_argument(
        "--pseudo-conversations-dir",
        default=None,
        help="Directory containing pseudo-conversation JSON fixtures",
    )
    parser.add_argument(
        "--results-dir",
        default=None,
        help="Directory to write per-skill result JSON files",
    )
    parser.add_argument(
        "--report",
        default=None,
        help="Path for the summary report JSON (default: <results-dir>/../report.json)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-skill detail during evaluation",
    )
    args = parser.parse_args()

    evolve_dir = get_evolve_dir()

    pseudo_conv_dir = Path(args.pseudo_conversations_dir) if args.pseudo_conversations_dir \
        else evolve_dir / "tests" / "pseudo_conversations"

    results_dir = Path(args.results_dir) if args.results_dir \
        else evolve_dir / "tests" / "evaluation" / "results"

    report_path = Path(args.report) if args.report \
        else evolve_dir / "tests" / "evaluation" / "report.json"

    print(f"Loading fixtures from: {pseudo_conv_dir}")
    print(f"Writing results to:    {results_dir}")
    print()

    results = run_evaluation(pseudo_conv_dir, results_dir, verbose=args.verbose)

    report = build_report(results)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    print_table(results, report)
    print(f"Report written: {report_path}")

    sys.exit(0 if report["failed"] == 0 else 1)


if __name__ == "__main__":
    main()

# Made with Bob
