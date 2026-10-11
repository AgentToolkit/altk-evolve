#!/usr/bin/env python3
"""
Trigger Test Runner

Tests whether a skill's trigger correctly identifies scenarios where the skill
applies. For each fixture, the agent receives ONLY the user message (no skill
injected) and must independently produce a response. The response is then
checked against the same must_include terms as the content tests.

If the agent independently arrives at the right answer without being told the
skill, the trigger describes a scenario specific enough that any competent
agent would naturally do the right thing. A failure means the situation is
non-obvious and the skill is genuinely necessary.

Unlike the content tests (which verify skill self-consistency), trigger tests
validate the trigger's DISCRIMINATING POWER — does this trigger identify a
scenario that a naive agent would handle incorrectly without the skill?

Two outcomes are both informative:
  PASS  — agent gets it right without the skill → trigger is well-scoped,
          but consider whether the skill adds value at all
  FAIL  — agent misses something without the skill → the skill IS necessary,
          and the trigger correctly identifies a non-obvious situation

Only skill fixtures (no edge_ / guideline_ prefix) are evaluated — edge cases
and guidelines are not atomic skills with standalone triggers.

Usage:
    python3 run_baseline_tests.py --responses-file <path>
    python3 run_baseline_tests.py --simulate   (uses naive baseline responses)
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap
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
        from entity_io import get_evolve_dir
    except ImportError:
        def get_evolve_dir(): return Path(".evolve")
else:
    def get_evolve_dir(): return Path(".evolve")


# ---------------------------------------------------------------------------
# Naive baseline responses
#
# These simulate what a generic agent would say WITHOUT the skill injected.
# Each response is intentionally written as a reasonable-but-incomplete answer
# — the kind a competent agent gives when it doesn't have the specific skill.
# ---------------------------------------------------------------------------

NAIVE_RESPONSES = {
    # ---- Watson Orchestrate skills ----
    "watson-orchestrate-activate-venv":
        "Make sure your Python environment is set up. You may need to install the "
        "orchestrate package. Try running `pip install ibm-watsonx-orchestrate` and "
        "then retry your command.",

    "watson-orchestrate-authenticate-orchestrate-env":
        "Before running Watson Orchestrate commands, make sure you're logged in. "
        "You may need to configure your credentials or run a login command. "
        "Check the orchestrate CLI help with `orchestrate --help` to see available "
        "authentication options.",

    "watson-orchestrate-create-agent-yaml":
        "To create a Watson Orchestrate agent, you'll need a YAML configuration file. "
        "The file should define your agent's properties. Common fields include name, "
        "description, and the model to use. Check the Watson Orchestrate documentation "
        "for the full schema.",

    "watson-orchestrate-decorate-tool-function":
        "To register a Python function as a tool, you need to add a decorator to it. "
        "Check the Watson Orchestrate SDK documentation for the correct decorator "
        "syntax and which package to import it from.",

    "watson-orchestrate-deploy-agent":
        "After importing an agent, you should be able to see it in the Watson Orchestrate "
        "interface. Check the agents list to confirm the import was successful. "
        "If the agent isn't appearing, try refreshing or checking the import logs.",

    "watson-orchestrate-import-agent-yaml":
        "To import an agent into Watson Orchestrate, use the orchestrate CLI. "
        "Run `orchestrate agents import` with your agent file. Check `orchestrate agents --help` "
        "for the exact syntax and required flags.",

    "watson-orchestrate-import-multi-tool-python-file":
        "To expose multiple Python functions as tools, you can import the Python file "
        "using the orchestrate tools import command. Check the CLI help for how to "
        "specify the file and tool names.",

    "watson-orchestrate-pipe-api-key-stdin":
        "If a CLI command keeps prompting for a password, you could try setting "
        "environment variables beforehand, or look for a `--no-interactive` flag. "
        "Some tools also support reading credentials from a config file.",

    "watson-orchestrate-reauth-expired-token":
        "A token expiration error means your session has timed out. You'll need to "
        "log in again. Try running the authentication command again and re-enter your "
        "credentials when prompted.",

    "watson-orchestrate-register-and-auth-orchestrate-env":
        "To set up the Watson Orchestrate CLI for the first time, you need to configure "
        "your environment. Check the CLI documentation for the setup commands and "
        "provide your credentials when prompted.",
}


# ---------------------------------------------------------------------------
# Evaluator (same logic as run_skill_evaluation.py)
# ---------------------------------------------------------------------------

def _normalise(text):
    """Strip angle-bracket placeholders and lower-case for matching."""
    return re.sub(r"\s*<[^>]+>", "", text).strip().lower()


def evaluate_response(response, expected_behaviour):
    must_include     = expected_behaviour.get("must_include", [])
    must_not_include = expected_behaviour.get("must_not_include", [])
    resp_norm = _normalise(response)
    matched  = [t for t in must_include     if _normalise(t) in resp_norm]
    missed   = [t for t in must_include     if _normalise(t) not in resp_norm]
    violated = [t for t in must_not_include if _normalise(t) in resp_norm]
    score    = round(len(matched) / len(must_include), 4) if must_include else 1.0
    passed   = score >= 0.5 and not violated
    return {
        "matched": matched,
        "missed": missed,
        "violated": violated,
        "alignment_score": score,
        "passed": passed,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Test skill trigger discrimination — does an agent without the skill arrive at the right answer?"
    )
    parser.add_argument(
        "--pseudo-conversations-dir", default=None,
        help="Directory containing pseudo-conversation fixtures",
    )
    parser.add_argument(
        "--responses-file", default=None,
        help="JSON file mapping skill_slug → agent_response (for live responses)",
    )
    parser.add_argument(
        "--simulate", action="store_true",
        help="Use built-in naive baseline responses instead of live agent responses",
    )
    parser.add_argument(
        "--results-dir", default=None,
    )
    parser.add_argument(
        "--report", default=None,
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    evolve_dir = get_evolve_dir()

    pseudo_conv_dir = Path(args.pseudo_conversations_dir) if args.pseudo_conversations_dir \
        else evolve_dir / "tests" / "pseudo_conversations"
    results_dir = Path(args.results_dir) if args.results_dir \
        else evolve_dir / "tests" / "evaluation" / "results"
    report_path = Path(args.report) if args.report \
        else evolve_dir / "tests" / "evaluation" / "baseline_report.json"

    results_dir.mkdir(parents=True, exist_ok=True)

    # Load responses
    if args.responses_file:
        with open(args.responses_file) as fh:
            responses = json.load(fh)
        mode = "live"
    elif args.simulate:
        responses = NAIVE_RESPONSES
        mode = "simulated_naive"
    else:
        # Print instructions for spawning agents and exit
        fixture_files = sorted(pseudo_conv_dir.glob("*.json"))
        print("To run trigger tests with live agent responses, spawn one sub-agent")
        print("per skill using ONLY the user message (no skill injected), then pass")
        print("the responses via --responses-file.\n")
        print("User messages to send (no system prompt, no skill context):\n")
        for fpath in fixture_files:
            with open(fpath) as fh:
                fx = json.load(fh)
            user_msg = next(m["content"] for m in fx["conversation"] if m["role"] == "user")
            print(f"  [{fx['skill_slug']}]")
            print(f"  {user_msg}\n")
        print("Collect responses into a JSON file:")
        print('  { "skill-slug": "agent response text", ... }')
        print("\nThen run:")
        print("  python3 run_baseline_tests.py --responses-file responses.json")
        sys.exit(0)

    # Only skill fixtures — skip edge_ and guideline_ prefixed files since
    # those are edge-case tests, not standalone skill trigger tests.
    fixture_files = sorted(
        f for f in pseudo_conv_dir.glob("*.json")
        if not f.name.startswith(("edge_", "guideline_"))
    )
    if not fixture_files:
        print(f"No fixtures found in {pseudo_conv_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Mode: {mode}")
    print(f"Testing {len(fixture_files)} skills\n")
    print("TRIGGER TEST RESULTS")
    print("=" * 80)
    print("(Agent responds WITHOUT skill injected — tests trigger discriminating power)\n")

    results = []
    for fixture_file in fixture_files:
        with open(fixture_file) as fh:
            fixture = json.load(fh)

        slug = fixture["skill_slug"]
        user_msg = next(m["content"] for m in fixture["conversation"] if m["role"] == "user")
        response = responses.get(slug, "")

        if not response:
            print(f"  ⚠  {slug} — no response provided, skipping")
            continue

        eval_result = evaluate_response(response, fixture["expected_behaviour"])

        result = {
            "test_id": f"trigger_{slug}",
            "skill_slug": slug,
            "mode": mode,
            "user_message": user_msg,
            "passed": eval_result["passed"],
            "alignment_score": eval_result["alignment_score"],
            "matched": eval_result["matched"],
            "missed": eval_result["missed"],
            "violated": eval_result["violated"],
            "agent_response": response,
            "interpretation": (
                "Skill may not be necessary — agent gets it right without the skill"
                if eval_result["passed"] else
                "Skill IS necessary — agent misses key guidance without it"
            ),
            "timestamp": datetime.now().isoformat(),
        }
        results.append(result)

        with open(results_dir / f"trigger_{slug}.json", "w") as fh:
            json.dump(result, fh, indent=2)

        status = "✅" if result["passed"] else "❌"
        total_inc = len(result["matched"]) + len(result["missed"])
        print(f"{status} {slug:<60}  score={result['alignment_score']:.2f}  matched={len(result['matched'])}/{total_inc}")
        if args.verbose or not result["passed"]:
            print(f"   → {result['interpretation']}")
            if result["missed"]:
                print(f"     missed={result['missed']}")

    if not results:
        print("No results — check that responses are provided for all skills.")
        sys.exit(1)

    total   = len(results)
    n_pass  = sum(1 for r in results if r["passed"])
    n_fail  = total - n_pass

    report = {
        "generated_at": datetime.now().isoformat(),
        "mode": mode,
        "total": total,
        "naive_agent_passes": n_pass,
        "skill_necessary_count": n_fail,
        "results": results,
    }
    with open(report_path, "w") as fh:
        json.dump(report, fh, indent=2)

    print()
    print(f"SUMMARY: {n_pass}/{total} answered correctly without the skill")
    print(f"         {n_fail}/{total} require the skill (agent missed key guidance)")
    print(f"Report:  {report_path}")
    # Trigger tests do NOT exit 1 on "failure" — a skill being necessary is a PASS
    # for the skill library. Exit 1 only if nothing could be evaluated.
    sys.exit(0)


if __name__ == "__main__":
    main()

# Made with Bob
