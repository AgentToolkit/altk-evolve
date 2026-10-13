#!/usr/bin/env python3
"""
Generate pseudo-conversation test fixtures for newly saved skill files.

Called automatically after evolve-lite-learn saves new entities. Accepts one
or more skill file paths as arguments, generates a fixture for each, and
prints a summary. Skips non-atomic-skill files silently.

Usage:
    python3 generate_skill_tests.py <skill.md> [<skill2.md> ...]
    python3 generate_skill_tests.py --all          # regenerate all skills

The generated fixtures land in .evolve/tests/pseudo_conversations/ following
the same format as generate_pseudo_conversations.py. The two scripts share
the same build_pseudo_conversation() logic — this one just accepts arbitrary
paths rather than scanning a fixed directory.
"""

import argparse
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: locate entity_io.py and the shared generator logic
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

from entity_io import get_evolve_dir, markdown_to_entity  # noqa: E402

# Import shared builder from the sibling generator script
_scripts_dir = _script.parent
sys.path.insert(0, str(_scripts_dir))
from generate_pseudo_conversations import (  # noqa: E402
    build_pseudo_conversation,
)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def generate_for_paths(skill_paths, evolve_dir, output_dir):
    """Generate fixtures for a list of skill file paths.

    Returns (generated, skipped) counts.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    generated = 0
    skipped = 0

    for path in skill_paths:
        path = Path(path)
        if not path.exists():
            print(f"  Warning: file not found, skipping: {path}", file=sys.stderr)
            skipped += 1
            continue

        entity = markdown_to_entity(path)

        # Only generate tests for atomic-skills
        if entity.get("type") != "atomic-skill":
            skipped += 1
            continue

        skill_slug = path.stem
        fixture = build_pseudo_conversation(skill_slug, path, entity, evolve_dir)

        out_path = output_dir / f"{skill_slug}.json"
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(fixture, fh, indent=2)

        eb = fixture["expected_behaviour"]
        print(f"  ✓ {skill_slug}")
        print(f"      user_msg     : {next(m['content'] for m in fixture['conversation'] if m['role']=='user')[:90]}")
        print(f"      must_include : {eb['must_include']}")
        if eb["must_not_include"]:
            print(f"      must_not     : {eb['must_not_include']}")
        generated += 1

    return generated, skipped


def main():
    parser = argparse.ArgumentParser(
        description="Generate pseudo-conversation test fixtures for new atomic skills"
    )
    parser.add_argument(
        "skill_files",
        nargs="*",
        help="Paths to newly saved atomic skill .md files",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Regenerate fixtures for all atomic skills in .evolve/entities/",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Override output directory (default: .evolve/tests/pseudo_conversations/)",
    )
    args = parser.parse_args()

    evolve_dir = get_evolve_dir()
    output_dir = Path(args.output_dir) if args.output_dir \
        else evolve_dir / "tests" / "pseudo_conversations"

    if args.all:
        skill_paths = sorted(
            p for p in (evolve_dir / "entities").glob("**/*.md")
            if not p.is_symlink()
        )
    elif args.skill_files:
        skill_paths = [Path(p) for p in args.skill_files]
    else:
        parser.print_help()
        sys.exit(0)

    if not skill_paths:
        print("No skill files to process.")
        sys.exit(0)

    print(f"Generating test fixtures for {len(skill_paths)} file(s)...")
    print()
    generated, skipped = generate_for_paths(skill_paths, evolve_dir, output_dir)
    print()
    print(f"Done. Generated: {generated}  Skipped (non-atomic-skill): {skipped}")
    print(f"Output: {output_dir}")


if __name__ == "__main__":
    main()

# Made with Bob
