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

The agent passes the native ``--type`` through verbatim (native types map
straight onto the entity type — no remapping) and supplies a synthesized
``--trigger`` (the single most important field for future retrieval). The body
of the native file becomes the entity content; the native ``description`` is
carried into the body as a lead line when present.

Usage:
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
    find_entities_dir,
    get_default_entities_dir,
    slugify,
    write_entity_file,
    log as _log,
)


def log(message):
    _log("adapt-memory", message)


def parse_native_memory(text):
    """Split a native memory file into (name, description, body).

    Native frontmatter is simple ``key: value`` lines plus a nested
    ``metadata:`` block; we parse the top-level ``name`` and ``description``
    lines and treat everything after the closing ``---`` as the body. The
    ``name`` is the native slug we reuse as the stable entity id. Missing
    frontmatter is tolerated — the whole text is then the body.
    """
    name = None
    description = None
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            frontmatter, body = parts[1], parts[2]
            for line in frontmatter.splitlines():
                # Only top-level keys (no leading indentation) — keeps the
                # nested metadata.* keys out of the top-level matches.
                if line[:1].isspace():
                    continue
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip()
                if key == "name" and value:
                    name = value
                elif key == "description" and value:
                    description = value
    return name, description, body.strip()


def main():
    parser = argparse.ArgumentParser(description="Mirror a native memory into the evolve store.")
    parser.add_argument("memory_path", help="Path to the just-saved native memory file.")
    parser.add_argument(
        "--type",
        required=True,
        help="Native memory type, passed through as the entity type (e.g. user, feedback, project, reference).",
    )
    parser.add_argument(
        "--trigger",
        required=True,
        help="Synthesized one-sentence 'when to recall this' description.",
    )
    args = parser.parse_args()

    memory_path = Path(args.memory_path).expanduser()
    if not memory_path.is_file():
        print(f"Error: native memory file not found: {memory_path}", file=sys.stderr)
        sys.exit(1)

    try:
        text = memory_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"Error: cannot read {memory_path} - {exc}", file=sys.stderr)
        sys.exit(1)

    name, description, body = parse_native_memory(text)
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

    entity = {
        "type": args.type,
        "trigger": args.trigger,
        "content": content,
        "source": "native-memory",
        "native_path": args.memory_path,
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
