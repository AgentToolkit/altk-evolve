#!/usr/bin/env python3
"""Keep the README "Latest from Evolve" block in sync with the docs snippet.

The single source of truth is ``includes/latest-updates.md`` (a plain-markdown
timeline included by ``docs/index.md`` via a pymdownx snippet). GitHub cannot
include snippets, so this script copies that snippet verbatim into the marked
block in ``README.md``.

Usage:
    python scripts/sync_latest_updates.py          # rewrite README to match the snippet
    python scripts/sync_latest_updates.py check     # exit 1 if README is out of sync

The ``check`` mode mirrors ``plugin-source/build_plugins.py check`` and is the
one wired into pre-commit / CI.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SNIPPET = REPO_ROOT / "includes" / "latest-updates.md"
README = REPO_ROOT / "README.md"

BEGIN = "<!-- BEGIN LATEST-UPDATES"
END = "<!-- END LATEST-UPDATES -->"
# Match the whole block, keeping the exact BEGIN/END marker lines intact.
BLOCK_RE = re.compile(
    r"(?P<begin>^<!-- BEGIN LATEST-UPDATES[^\n]*-->\n)"
    r".*?"
    r"(?P<end>^<!-- END LATEST-UPDATES -->$)",
    re.DOTALL | re.MULTILINE,
)


def render_readme(readme_text: str, snippet_text: str) -> str:
    """Return README text with the marked block replaced by the snippet."""
    snippet = snippet_text.strip("\n")

    def _replace(m: re.Match[str]) -> str:
        return f"{m.group('begin')}{snippet}\n{m.group('end')}"

    new_text, n = BLOCK_RE.subn(_replace, readme_text)
    if n == 0:
        sys.exit(
            f"error: could not find the '{BEGIN} ... {END}' block in {README}.\nAdd the markers around the Latest-from-Evolve list first."
        )
    return new_text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        nargs="?",
        default="write",
        choices=["write", "check"],
        help="write (default) rewrites README; check verifies it is in sync.",
    )
    args = parser.parse_args()

    if not SNIPPET.exists():
        sys.exit(f"error: snippet not found: {SNIPPET}")
    if not README.exists():
        sys.exit(f"error: README not found: {README}")

    snippet_text = SNIPPET.read_text(encoding="utf-8")
    readme_text = README.read_text(encoding="utf-8")
    updated = render_readme(readme_text, snippet_text)

    if args.mode == "check":
        if updated != readme_text:
            print(
                "README 'Latest from Evolve' block is out of sync with "
                "includes/latest-updates.md.\n"
                "Run: python scripts/sync_latest_updates.py",
                file=sys.stderr,
            )
            return 1
        return 0

    if updated != readme_text:
        README.write_text(updated, encoding="utf-8")
        print(f"Updated {README.relative_to(REPO_ROOT)} from {SNIPPET.relative_to(REPO_ROOT)}")
    else:
        print("README already in sync.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
