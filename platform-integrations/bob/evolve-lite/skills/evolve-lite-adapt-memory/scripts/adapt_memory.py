#!/usr/bin/env python3
"""
Adapt Memory Script (Claude-only)

Mirrors a single native Claude memory file into the in-repo evolve store at
``${EVOLVE_DIR:-.evolve}/entities/<type>/<slug>.md`` so the memory becomes
shareable and auditable like every other evolve entity.

Native memory files live under ``~/.claude/projects/<hash>/memory/`` and carry
frontmatter of the form::

    ---
    name: <slug>
    description: <one-line summary>
    metadata:
      type: user | feedback | project | reference
    ---

    <body>

The agent supplies a synthesized ``--trigger`` (the single most important field
for future retrieval). The body of the native file becomes the entity content;
the native ``description`` is carried into the body as a lead line when present.

By default the script auto-locates the just-saved memory: it derives the
project's native memory dir ``~/.claude/projects/<slug>/memory/`` (slug =
:func:`entity_io.claude_project_slug` of the resolved cwd — the same slug
doctor.py uses) and mirrors the most-recently-modified ``*.md`` there other than
``MEMORY.md``. The entity ``--type`` defaults to the native ``metadata.type``
from that file's frontmatter (``project`` if absent). Both can still be
overridden: pass an explicit memory path (e.g. when several memories were saved
this turn) and/or ``--type``.

Usage:
    python3 adapt_memory.py --trigger "<trigger>"
    python3 adapt_memory.py <native_memory_path> --type <type> --trigger <trigger>
"""

import argparse
import sys
from pathlib import Path

# Walk up from the script location to find the installed plugin lib directory.
# Every host installs the shared lib under lib/evolve-lite/ so multiple
# plugins can coexist side by side (e.g. .bob/lib/evolve-lite/).
_script = Path(__file__).resolve()
_lib = None
for _ancestor in _script.parents:
    _candidate = _ancestor / "lib" / "evolve-lite"
    if (_candidate / "entity_io.py").is_file():
        _lib = _candidate
        break
if _lib is None:
    raise ImportError(f"Cannot find plugin lib directory above {_script}")
sys.path.insert(0, str(_lib))
from entity_io import (  # noqa: E402
    claude_memory_dir,
    find_entities_dir,
    get_default_entities_dir,
    slugify,
    write_entity_file,
    log as _log,
)


def log(message):
    _log("adapt-memory", message)


def parse_native_memory(text):
    """Split a native memory file into (name, description, mem_type, body).

    Native frontmatter is simple ``key: value`` lines plus a nested
    ``metadata:`` block; we parse the top-level ``name`` and ``description``
    lines, the nested ``metadata.type`` value, and treat everything after the
    closing ``---`` as the body. The ``name`` is the native slug we reuse as the
    stable entity id; ``mem_type`` is used as the entity type when the caller
    doesn't pass ``--type``. Missing frontmatter is tolerated — the whole text
    is then the body.
    """
    name = None
    description = None
    mem_type = None
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            frontmatter, body = parts[1], parts[2]
            in_metadata = False
            for line in frontmatter.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                if line[:1].isspace():
                    # Nested keys (under metadata:); we only care about type.
                    if in_metadata:
                        key, _, value = stripped.partition(":")
                        if key.strip() == "type" and value.strip():
                            mem_type = value.strip()
                    continue
                # Top-level key — keeps the nested metadata.* keys out of the
                # top-level matches.
                in_metadata = False
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip()
                if key == "metadata":
                    in_metadata = True
                elif key == "name" and value:
                    name = value
                elif key == "description" and value:
                    description = value
    return name, description, mem_type, body.strip()


def locate_latest_memory(memory_dir):
    """Return the most-recently-modified ``*.md`` under *memory_dir* other than
    ``MEMORY.md`` (that's the memory just saved), or ``None`` if there is none.
    """
    if not memory_dir.is_dir():
        return None
    candidates = [p for p in memory_dir.glob("*.md") if p.is_file() and p.name != "MEMORY.md"]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def main():
    parser = argparse.ArgumentParser(description="Mirror a native memory into the evolve store.")
    parser.add_argument(
        "memory_path",
        nargs="?",
        help="Path to the just-saved native memory file. Omit to auto-locate the newest memory under ~/.claude/projects/<slug>/memory/.",
    )
    parser.add_argument(
        "--type",
        default=None,
        help="Entity type override (e.g. user, feedback, project, reference). "
        "Defaults to the native frontmatter metadata.type, else 'project'.",
    )
    parser.add_argument(
        "--trigger",
        required=True,
        help="Synthesized one-sentence 'when to recall this' description.",
    )
    args = parser.parse_args()

    if args.memory_path:
        memory_path = Path(args.memory_path).expanduser()
        if not memory_path.is_file():
            print(f"Error: native memory file not found: {memory_path}", file=sys.stderr)
            sys.exit(1)
    else:
        # Auto-locate the just-saved native memory for this project.
        memory_dir = claude_memory_dir(Path.cwd())
        located = locate_latest_memory(memory_dir)
        if located is None:
            print(
                f"No native memory found under {memory_dir}; pass the path explicitly.",
                file=sys.stderr,
            )
            sys.exit(1)
        memory_path = located

    memory_path = memory_path.resolve()

    try:
        text = memory_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"Error: cannot read {memory_path} - {exc}", file=sys.stderr)
        sys.exit(1)

    name, description, mem_type, body = parse_native_memory(text)
    if not body:
        print(f"Error: native memory {memory_path} has no body to mirror.", file=sys.stderr)
        sys.exit(1)

    # Carry the native description into the body as a lead line when it isn't
    # already echoed there, so the mirrored entity is self-describing.
    content = body
    if description and description not in body:
        content = f"{description}\n\n{body}"

    # The native ``name`` becomes the stable, derivable entity slug so the
    # entity id is ``<type>/<name>`` on both sides — provenance can map an
    # audited native memory straight onto its mirror. Fall back to a
    # content-derived slug only when the native frontmatter has no name.
    slug = slugify(name) if name else slugify(content)

    # Explicit --type wins (back-compat); otherwise infer from the native
    # frontmatter metadata.type, defaulting to "project" when neither is set.
    entity_type = args.type or mem_type or "project"

    entity = {
        "type": entity_type,
        "trigger": args.trigger,
        "content": content,
        "source": "native-memory",
        # Record the resolved path actually used (auto-located or explicit).
        "native_path": str(memory_path),
    }

    entities_dir = find_entities_dir()
    if entities_dir:
        entities_dir = entities_dir.resolve()
        log(f"Using existing entities dir: {entities_dir}")
    else:
        entities_dir = get_default_entities_dir()
        log(f"Created entities dir: {entities_dir}")

    # Deterministic, idempotent write: re-mirroring the same native memory
    # (same name + type) overwrites <type>/<name>.md in place rather than
    # creating <name>-2.md, keeping the entity id stable.
    path = write_entity_file(entities_dir, entity, filename=slug, overwrite=True)
    entity_id = f"{entity['type']}/{slug}"
    log(f"Mirrored {memory_path} -> {path} (id: {entity_id})")
    print(f"Mirrored native memory into evolve store: {path}")
    print(f"Entity id: {entity_id}")


if __name__ == "__main__":
    main()
