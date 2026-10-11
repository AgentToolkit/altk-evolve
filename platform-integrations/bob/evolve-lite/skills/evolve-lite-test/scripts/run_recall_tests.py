#!/usr/bin/env python3
"""
Recall Test Runner

Tests whether the recall layer surfaces the right skill for each scenario.

For each pseudo-conversation fixture, this script:
1. Builds the entity manifest (same format as retrieve_entities.py produces)
2. Presents the manifest + the user's question to a simulated recall agent
3. Checks whether the agent identifies the correct skill slug

The "simulated recall agent" uses keyword overlap between the user message and
each trigger — the same heuristic a real agent uses when scanning the manifest.

Reports Recall@K for K = 1, 3, and 5:
  Recall@K = fraction of fixtures where the expected skill is in the top-K results.

The overall pass/fail threshold remains rank <= 3 (Recall@3), consistent with
the existing behaviour, but all three K values are shown in the summary.

Usage:
    python3 run_recall_tests.py
    python3 run_recall_tests.py --verbose
    python3 run_recall_tests.py --pseudo-conversations-dir <path>
    python3 run_recall_tests.py --top-k 1   # pass threshold: rank-1 only
    python3 run_recall_tests.py --top-k 5   # pass threshold: top-5
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
        from entity_io import get_evolve_dir, load_manifest, find_recall_entity_dirs, dedupe_manifest_entries
    except ImportError:
        def get_evolve_dir(): return Path(".evolve")
        def load_manifest(d): return []
        def find_recall_entity_dirs(): return []
        def dedupe_manifest_entries(e): return e
else:
    def get_evolve_dir(): return Path(".evolve")
    def load_manifest(d): return []
    def find_recall_entity_dirs(): return []
    def dedupe_manifest_entries(e): return e


# ---------------------------------------------------------------------------
# Trigger relevance scorer
#
# Simulates how an agent scans the manifest: keyword overlap between the
# user message and the trigger text, weighted by term length (longer terms
# are more specific and score higher).
# ---------------------------------------------------------------------------

_STOP_WORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "should", "could", "may", "might", "must", "can", "this",
    "that", "these", "those", "i", "you", "he", "she", "it", "we", "they",
    "when", "after", "before", "while", "if", "how", "what", "my", "your",
    "just", "need", "want", "make", "sure", "some", "also", "about",
}


def tokenise(text):
    """Lowercase words, drop stop words and very short tokens."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w for w in words if w not in _STOP_WORDS and len(w) > 2]


def score_trigger(trigger, user_message):
    """
    Return a relevance score for (trigger, user_message).

    Score = sum of len(term) for each trigger term found in the user message.
    Longer matching terms (e.g. "orchestrate", "token", "expiration") score
    higher than short ones, which filters out accidental single-word matches.
    """
    trigger_tokens = set(tokenise(trigger))
    user_tokens = set(tokenise(user_message))
    matched = trigger_tokens & user_tokens
    return sum(len(t) for t in matched), matched


def rank_manifest(manifest, user_message):
    """
    Return manifest entries sorted by descending relevance to user_message.
    Each entry gains a 'score' and 'matched_terms' field.
    """
    scored = []
    for entry in manifest:
        score, matched = score_trigger(entry["trigger"], user_message)
        scored.append({**entry, "score": score, "matched_terms": list(matched)})
    return sorted(scored, key=lambda e: e["score"], reverse=True)


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def run_recall_test(fixture, manifest, pass_k=3):
    """
    Run a recall test for one fixture.

    Returns a result dict with:
        passed          - bool: expected skill is in top-{pass_k} results
        rank            - int: 1-based position of expected skill in ranked list
        in_top1         - bool
        in_top3         - bool
        in_top5         - bool
        top_ranked_slug - slug of the #1 ranked skill
        score_expected  - relevance score of the expected skill
        score_top       - relevance score of the #1 ranked skill
        matched_terms   - terms that fired for the expected skill
    """
    skill_slug = fixture["skill_slug"]
    user_msg = next(
        m["content"] for m in fixture["conversation"] if m["role"] == "user"
    )

    ranked = rank_manifest(manifest, user_msg)

    # Find rank of the expected skill
    rank = None
    score_expected = 0
    matched_terms = []
    for i, entry in enumerate(ranked):
        slug = Path(entry["path"]).stem
        if slug == skill_slug:
            rank = i + 1
            score_expected = entry["score"]
            matched_terms = entry["matched_terms"]
            break

    top = ranked[0] if ranked else {}
    top_slug = Path(top.get("path", "")).stem
    score_top = top.get("score", 0)

    in_top1 = rank == 1
    in_top3 = rank is not None and rank <= 3 and score_expected > 0
    in_top5 = rank is not None and rank <= 5 and score_expected > 0

    # Pass threshold is configurable via pass_k
    if pass_k == 1:
        passed = in_top1
    elif pass_k == 5:
        passed = in_top5
    else:  # default: k=3
        passed = in_top3

    return {
        "test_id": f"recall_{skill_slug}",
        "skill_slug": skill_slug,
        "user_message": user_msg,
        "passed": passed,
        "rank": rank,
        "in_top1": in_top1,
        "in_top3": in_top3,
        "in_top5": in_top5,
        "top_ranked_slug": top_slug,
        "score_expected": score_expected,
        "score_top": score_top,
        "matched_terms": matched_terms,
        "top5": [Path(e["path"]).stem for e in ranked[:5]],
        "timestamp": datetime.now().isoformat(),
    }


def print_result(result, verbose):
    status = "✅" if result["passed"] else "❌"
    rank_str = f"rank={result['rank']}" if result["rank"] else "rank=not_found"
    k1 = "✓" if result["in_top1"] else "✗"
    k3 = "✓" if result["in_top3"] else "✗"
    k5 = "✓" if result["in_top5"] else "✗"
    line = (
        f"{status} {result['skill_slug']:<56}"
        f"  {rank_str:<12}"
        f"  @1={k1} @3={k3} @5={k5}"
        f"  score={result['score_expected']}"
    )
    print(line)
    if not result["passed"] or verbose:
        print(f"     user_msg     : {result['user_message'][:80]}")
        print(f"     matched_terms: {result['matched_terms']}")
        print(f"     top5         : {result['top5']}")
        if not result["passed"] and result["top_ranked_slug"] != result["skill_slug"]:
            print(f"     ⚠ top-ranked was: {result['top_ranked_slug']}  (score={result['score_top']})")


def main():
    parser = argparse.ArgumentParser(
        description="Test that each skill is correctly recalled for its trigger scenario"
    )
    parser.add_argument("--pseudo-conversations-dir", default=None)
    parser.add_argument("--results-dir", default=None)
    parser.add_argument("--report", default=None)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--top-k", type=int, choices=[1, 3, 5], default=3,
        help="Pass threshold: skill must appear in top-K results (default: 3). "
             "Recall@1, @3, and @5 are always reported regardless of this setting.",
    )
    args = parser.parse_args()

    evolve_dir = get_evolve_dir()

    pseudo_conv_dir = Path(args.pseudo_conversations_dir) if args.pseudo_conversations_dir \
        else evolve_dir / "tests" / "pseudo_conversations"

    results_dir = Path(args.results_dir) if args.results_dir \
        else evolve_dir / "tests" / "evaluation" / "results"

    report_path = Path(args.report) if args.report \
        else evolve_dir / "tests" / "evaluation" / "recall_report.json"

    results_dir.mkdir(parents=True, exist_ok=True)

    # Build manifest from live entities
    raw_entries = []
    for root_dir in find_recall_entity_dirs():
        raw_entries.extend(load_manifest(root_dir))
    manifest = dedupe_manifest_entries(raw_entries)

    if not manifest:
        print("Error: no entities found — recall manifest is empty.", file=sys.stderr)
        sys.exit(1)

    fixture_files = sorted(pseudo_conv_dir.glob("*.json"))
    if not fixture_files:
        print(f"No fixture files found in {pseudo_conv_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Manifest: {len(manifest)} entities")
    print(f"Fixtures: {len(fixture_files)}")
    print()
    print("RECALL TEST RESULTS")
    print("=" * 80)

    results = []
    for fixture_file in fixture_files:
        with open(fixture_file) as fh:
            fixture = json.load(fh)

        result = run_recall_test(fixture, manifest, pass_k=args.top_k)
        print_result(result, args.verbose)
        results.append(result)

        with open(results_dir / f"recall_{fixture['skill_slug']}.json", "w") as fh:
            json.dump(result, fh, indent=2)

    total    = len(results)
    passed   = sum(1 for r in results if r["passed"])
    recall_1 = sum(1 for r in results if r["in_top1"])
    recall_3 = sum(1 for r in results if r["in_top3"])
    recall_5 = sum(1 for r in results if r["in_top5"])

    report = {
        "generated_at": datetime.now().isoformat(),
        "pass_threshold_k": args.top_k,
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "recall_at_1": round(recall_1 / total, 4) if total else 0,
        "recall_at_3": round(recall_3 / total, 4) if total else 0,
        "recall_at_5": round(recall_5 / total, 4) if total else 0,
        "recall_at_1_count": recall_1,
        "recall_at_3_count": recall_3,
        "recall_at_5_count": recall_5,
        "results": results,
    }
    with open(report_path, "w") as fh:
        json.dump(report, fh, indent=2)

    print()
    print("=" * 72)
    print(f"  Recall@1 : {recall_1:>3}/{total}  ({100 * recall_1 / total:.1f}%)" if total else "  Recall@1 : —")
    print(f"  Recall@3 : {recall_3:>3}/{total}  ({100 * recall_3 / total:.1f}%)" if total else "  Recall@3 : —")
    print(f"  Recall@5 : {recall_5:>3}/{total}  ({100 * recall_5 / total:.1f}%)" if total else "  Recall@5 : —")
    print(f"  Pass threshold: top-{args.top_k}  →  {passed}/{total} passed")
    print(f"Report:  {report_path}")

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()

# Made with Bob
