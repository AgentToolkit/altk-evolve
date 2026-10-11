#!/usr/bin/env python3
"""
Pseudo-Conversation Generator for Atomic Skill Evaluation

Reads each atomic skill in .evolve/entities/atomic-skill/watson-orchestrate/,
builds a pseudo-conversation (system + user message with the skill injected),
derives expected_behaviour (must_include / must_not_include) from the skill
content, and writes one JSON fixture per skill to
.evolve/tests/pseudo_conversations/.

Usage:
    python generate_pseudo_conversations.py
    python generate_pseudo_conversations.py --export-csv
"""

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: locate entity_io.py by walking up from this script's location
# ---------------------------------------------------------------------------
_script = Path(__file__).resolve()
_lib = None
for _ancestor in _script.parents:
    _candidate = _ancestor / "lib" / "evolve-lite"
    if (_candidate / "entity_io.py").is_file():
        _lib = _candidate
        break
if _lib is None:
    raise ImportError(f"Cannot find lib/evolve-lite/entity_io.py above {_script}")
sys.path.insert(0, str(_lib))
sys.path.insert(0, str(_script.parent))

from entity_io import get_evolve_dir, markdown_to_entity  # noqa: E402
from trigger_parser import trigger_to_user_question  # noqa: E402

# ---------------------------------------------------------------------------
# Subagent prompt template (mirrors .evolve/tests/evaluation/subagent_prompt_template.md)
# ---------------------------------------------------------------------------
_SYSTEM_PREAMBLE = """\
You are a technical assistant evaluating whether you can correctly apply a recalled skill.

A skill has been recalled for this conversation. It is enclosed in <recalled_skill> tags in the
system context. Your job is to respond to the user's question by FOLLOWING the skill exactly.

Rules:
- Use the specific commands, steps, or guidance from the recalled skill
- Do not invent alternative approaches not mentioned in the skill
- If the skill prescribes a specific command, include that exact command in your response
- If the skill says NOT to use something, do not suggest it

Respond concisely and directly. Your response will be evaluated for skill adherence.\
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_backtick_terms(content):
    """Return all strings wrapped in single backticks (inline code spans).

    Fenced code blocks (``` ... ```) are excluded before scanning so that
    multi-line code examples don't end up in must_include.
    """
    # Remove fenced code blocks first (``` ... ```)
    stripped = re.sub(r"```[\s\S]*?```", "", content)
    return re.findall(r"`([^`]+)`", stripped)


def extract_must_not_include(content):
    """
    Scan content for negation patterns and return the negated term.
    Patterns: 'does not accept X', 'not as a X', 'instead of X', 'avoid X',
              'do not use X', 'not use X'.
    """
    patterns = [
        r"does not accept\s+(`[^`]+`|\S+)",
        r"not as a\s+(`[^`]+`|\S+)",
        r"instead of\s+(`[^`]+`|\S+)",
        r"avoid\s+(`[^`]+`|\S+)",
        r"do not (?:use|suggest)\s+(`[^`]+`|\S+)",
        r"not use\s+(`[^`]+`|\S+)",
    ]
    terms = []
    for pat in patterns:
        for match in re.finditer(pat, content, re.IGNORECASE):
            term = match.group(1).strip("`").strip()
            if term:
                terms.append(term)
    return terms


def strip_placeholders(term):
    """Remove angle-bracket placeholder segments from a string.

    e.g. 'orchestrate env add --name <env-name> --url <url>' -> 'orchestrate env add --name --url'
    Used at evaluation time (not generation time) so must_include retains the
    original command structure with placeholders intact in the fixture.
    """
    return re.sub(r"\s*<[^>]+>", "", term).strip()


def _rubric_to_must_include(rubric_text):
    """Parse a ## Success Rubric bullet list into must_include terms.

    Each bullet line may contain backtick-wrapped terms and/or plain criteria.
    Backtick terms are extracted verbatim (they represent observable commands or
    outputs). Plain bullet text (after stripping the leading ``-``) is included
    as-is so the evaluator can match it as a substring in the agent response.
    """
    seen: set = set()
    terms = []
    for line in rubric_text.splitlines():
        line = line.strip().lstrip("-").strip()
        if not line:
            continue
        # Prefer backtick-wrapped terms within the bullet if present
        backtick_hits = re.findall(r"`([^`]+)`", line)
        candidates = [t.strip() for t in backtick_hits if t.strip()] if backtick_hits else [line]
        for t in candidates:
            if t not in seen:
                seen.add(t)
                terms.append(t)
    return terms


def build_expected_behaviour(skill_path, entity):
    """Derive must_include, must_not_include, and action_type from skill content.

    Priority order for must_include:
      1. ## Success Rubric bullets — explicit, author-stated pass criteria
      2. Backtick-wrapped inline code from the skill body
      3. Underscore-identifier / long-word fallback
    """
    content = entity.get("content", "")
    rubric  = entity.get("success_rubric", "")

    rubric_terms = _rubric_to_must_include(rubric) if rubric else []

    if rubric_terms:
        # Priority 1: author-stated success criteria take precedence over heuristics
        must_include = rubric_terms
        action_type = "rubric_criteria"
    else:
        # rubric was absent OR present but contained no extractable terms —
        # fall through to content-based heuristics in both cases
        backtick_terms = extract_backtick_terms(content)
        if backtick_terms:
            # Priority 2: backtick-wrapped inline code from the skill body
            must_include = [t.strip() for t in backtick_terms if t.strip()]
            action_type = "command_recommendation"
        else:
            # Priority 3: underscore-identifier / long-word fallback
            underscore_terms = re.findall(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b", content)
            if underscore_terms:
                seen: set = set()
                must_include = []
                for t in underscore_terms:
                    if t not in seen:
                        seen.add(t)
                        must_include.append(t)
            else:
                _stop = {
                    "with", "from", "this", "that", "file", "have", "into",
                    "when", "also", "some", "here", "there", "then", "than",
                    "each", "will", "your", "been", "able", "make", "only",
                    "such", "used", "both", "they", "them", "what", "more",
                }
                candidates = re.findall(r"\b([a-z]{5,})\b", content.lower())
                seen = set()
                must_include = []
                for t in candidates:
                    if t not in _stop and t not in seen:
                        seen.add(t)
                        must_include.append(t)
                must_include = must_include[:6]
            action_type = "procedural_guidance"

    # must_not_include: scan both skill body and rubric (if present) for negation patterns
    must_not_include = extract_must_not_include(content)
    if rubric:
        must_not_include += extract_must_not_include(rubric)
        must_not_include = list(dict.fromkeys(must_not_include))  # dedup, preserve order

    return {
        "description": f"Agent should follow the guidance in {Path(skill_path).stem}",
        "must_include": must_include,
        "must_not_include": must_not_include,
        "action_type": action_type,
    }


def build_pseudo_conversation(skill_slug, skill_path, entity, evolve_dir):
    """Build the complete pseudo-conversation fixture dict for one skill."""
    content = entity.get("content", "")
    trigger = entity.get("trigger", "")
    traj_ref = entity.get("trajectory")  # may be absent

    # System message: preamble + recalled skill
    system_content = (
        f"{_SYSTEM_PREAMBLE}\n\n"
        f"The following skill has been recalled and MUST guide your response:\n"
        f"<recalled_skill>\n{content}\n</recalled_skill>"
    )

    # User message: prefer trajectory-derived, fall back to trigger-derived
    generation_method = "trigger_derived"
    user_content = None

    if traj_ref:
        # Trajectory path is kept in sources for traceability but is no longer
        # used to derive the user message — trigger-derived questions are more
        # skill-specific and avoid the "same opening message for all skills" problem.
        pass

    user_content = trigger_to_user_question(trigger, llm_fn=None)
    generation_method = "trigger_derived"

    conversation = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]

    expected_behaviour = build_expected_behaviour(skill_path, entity)

    sources = {
        "trajectory": traj_ref or None,
        "generation_method": generation_method,
        "trigger": trigger,  # stored for CSV export and traceability
    }

    return {
        "test_id": skill_slug,
        "skill_slug": skill_slug,
        "skill_path": str(Path(skill_path).relative_to(evolve_dir.parent)),
        "skill_content": content,
        "conversation": conversation,
        "expected_behaviour": expected_behaviour,
        "sources": sources,
    }


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

def export_csv(fixtures, output_path):
    """Write a human-readable CSV of all pseudo-conversations."""
    fieldnames = [
        "skill_slug", "trigger", "user_message",
        "must_include", "must_not_include", "action_type", "generation_method",
    ]
    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for fx in fixtures:
            user_msg = next(
                (m["content"] for m in fx["conversation"] if m["role"] == "user"), ""
            )
            writer.writerow({
                "skill_slug": fx["skill_slug"],
                "trigger": fx["sources"].get("trigger", ""),
                "user_message": user_msg[:300],
                "must_include": "|".join(fx["expected_behaviour"]["must_include"]),
                "must_not_include": "|".join(fx["expected_behaviour"]["must_not_include"]),
                "action_type": fx["expected_behaviour"]["action_type"],
                "generation_method": fx["sources"]["generation_method"],
            })
    print(f"  CSV written: {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate pseudo-conversation test fixtures for atomic skill evaluation"
    )
    parser.add_argument(
        "--export-csv",
        action="store_true",
        help="Also write .evolve/tests/pseudo_conversations/test_suite.csv",
    )
    parser.add_argument(
        "--entities-dir",
        default=None,
        help=(
            "Root directory to scan for entity .md files (recursively). "
            "Default: .evolve/entities/atomic-skill/watson-orchestrate/"
        ),
    )
    parser.add_argument(
        "--filter-slugs",
        default=None,
        help=(
            "Path to a JSON file whose top-level keys are the only entity slugs "
            "to generate fixtures for. Slugs not in the file are skipped. "
            "Supports the main_entity_slugs.json manifest format."
        ),
    )
    parser.add_argument(
        "--pinned-rubrics",
        default=None,
        help=(
            "Path to a main_entity_slugs.json manifest. For any slug whose entry "
            "has a non-empty 'rubric_terms' list, use those terms as must_include "
            "instead of re-deriving them from the merged entity. "
            "Use this during regression gating so forks cannot weaken rubrics and "
            "silently pass their own tests."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory to write fixture JSON files. Default: .evolve/tests/pseudo_conversations/",
    )
    args = parser.parse_args()

    evolve_dir = get_evolve_dir()

    if args.entities_dir:
        skills_dir = Path(args.entities_dir)
    else:
        skills_dir = evolve_dir / "entities" / "atomic-skill" / "watson-orchestrate"

    output_dir = Path(args.output_dir) if args.output_dir \
        else evolve_dir / "tests" / "pseudo_conversations"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load slug filter if provided
    allowed_slugs = None
    if args.filter_slugs:
        filter_path = Path(args.filter_slugs)
        if not filter_path.exists():
            print(f"Error: --filter-slugs file not found: {filter_path}", file=sys.stderr)
            sys.exit(1)
        with open(filter_path, "r", encoding="utf-8") as fh:
            allowed_slugs = set(json.load(fh).keys())
        print(f"Filtering to {len(allowed_slugs)} slug(s) from {filter_path}")

    # Load pinned rubrics if provided
    pinned_rubrics = {}  # slug -> [must_include terms]
    if args.pinned_rubrics:
        pinned_path = Path(args.pinned_rubrics)
        if not pinned_path.exists():
            print(f"Error: --pinned-rubrics file not found: {pinned_path}", file=sys.stderr)
            sys.exit(1)
        with open(pinned_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        for slug, entry in manifest.items():
            terms = entry.get("rubric_terms", [])
            if terms:
                pinned_rubrics[slug] = terms
        print(f"Pinned rubrics loaded for {len(pinned_rubrics)} slug(s) from {pinned_path}")

    if not skills_dir.exists():
        print(f"Error: skills directory not found: {skills_dir}", file=sys.stderr)
        sys.exit(1)

    md_files = sorted(
        p for p in skills_dir.rglob("*.md")
        if not p.is_symlink()
    )

    # Apply slug filter
    if allowed_slugs is not None:
        md_files = [p for p in md_files if p.stem in allowed_slugs]

    if not md_files:
        print("No skill files found.", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(md_files)} skill file(s) in {skills_dir}")
    print()

    fixtures = []
    traj_derived = 0
    trigger_derived = 0

    for md_file in md_files:
        skill_slug = md_file.stem
        print(f"  Processing: {skill_slug}")

        entity = markdown_to_entity(md_file)
        fixture = build_pseudo_conversation(skill_slug, md_file, entity, evolve_dir)

        # Override must_include with pinned rubric terms if available for this slug.
        # This ensures the regression gate always tests the original main-repo rubric
        # even if a fork changed the entity's rubric section.
        if skill_slug in pinned_rubrics:
            fixture["expected_behaviour"]["must_include"] = pinned_rubrics[skill_slug]
            fixture["expected_behaviour"]["action_type"] = "rubric_criteria_pinned"
            print(f"    [pinned rubric] must_include overridden from manifest")

        out_path = output_dir / f"{skill_slug}.json"
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(fixture, fh, indent=2)
        print(f"    Written: {out_path}")
        print(f"    method={fixture['sources']['generation_method']}  "
              f"must_include={fixture['expected_behaviour']['must_include']}")
        if fixture["expected_behaviour"]["must_not_include"]:
            print(f"    must_not_include={fixture['expected_behaviour']['must_not_include']}")

        fixtures.append(fixture)
        if fixture["sources"]["generation_method"] == "trajectory_derived":
            traj_derived += 1
        else:
            trigger_derived += 1

    print()
    print("=" * 60)
    print(f"Generated {len(fixtures)} fixture(s)  (expect 6)")
    print(f"  trajectory_derived : {traj_derived}")
    print(f"  trigger_derived    : {trigger_derived}")
    print(f"Output dir: {output_dir}")

    if args.export_csv:
        csv_path = output_dir / "test_suite.csv"
        export_csv(fixtures, csv_path)

    print()
    print("Done.")


if __name__ == "__main__":
    main()

# Made with Bob
