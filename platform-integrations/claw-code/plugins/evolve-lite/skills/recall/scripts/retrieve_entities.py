#!/usr/bin/env python3
"""Retrieve and output entities for the agent to filter.

In claw-code this script is invoked by the PreToolUse hook
(hooks/retrieve_entities.sh). The hook pipes HOOK_TOOL_INPUT (the about-to-run
tool's JSON input) via stdin, and claw-code also exposes the following env
vars:

  HOOK_EVENT       - "PreToolUse"
  HOOK_TOOL_NAME   - name of the tool about to execute
  HOOK_TOOL_INPUT  - JSON-encoded tool input (same bytes as stdin)

The script ignores the tool-specific payload beyond logging it; entity loading
is path-based and independent of which tool is running.
"""

import json
import os
import sys
from pathlib import Path

# Add lib to path so we can import entity_io
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "lib"))
from entity_io import find_recall_entity_dirs, markdown_to_entity, log as _log


def log(message):
    _log("retrieve", message)


log("Script started")

# Log claw-code hook env vars (and any CLAWD_* vars).
# HOOK_TOOL_INPUT and similar values can contain prompts, tool args, paths,
# code, or secrets, so log only the names of those keys and a redacted
# placeholder. HOOK_EVENT and HOOK_TOOL_NAME are safe to log verbatim.
_LOGGABLE_HOOK_KEYS = {"HOOK_EVENT", "HOOK_TOOL_NAME"}
log("=== Hook Context ===")
hook_keys = [k for k in os.environ if k.startswith(("HOOK_", "CLAWD_"))]
for key in sorted(hook_keys):
    if key in _LOGGABLE_HOOK_KEYS:
        log(f"  {key}={os.environ[key]}")
    else:
        log(f"  {key}=<redacted>")
if not hook_keys:
    log("  (no HOOK_* or CLAWD_* env vars found — may be running outside a hook)")
log("=== End Hook Context ===")

# Log command-line arguments
log(f"  sys.argv: {sys.argv}")


def format_entities(entities):
    """Format all entities for the agent to review.

    Entities that came from a subscribed source have their path recorded in
    the private ``_source`` key (set by load_entities_with_source). These are
    annotated with ``[from: {name}]`` so the agent knows their provenance.
    """
    header = """## Entities for this task

Review these entities and apply any relevant ones:

"""
    items = []
    for e in entities:
        content = e.get("content")
        if not content:
            continue
        source = e.get("_source")
        if source:
            content = f"[from: {source}] {content}"
        item = f"- **[{e.get('type', 'general')}]** {content}"
        if e.get("rationale"):
            item += f"\n  - _Rationale: {e['rationale']}_"
        if e.get("trigger"):
            item += f"\n  - _When: {e['trigger']}_"
        items.append(item)

    return header + "\n".join(items)


def load_entities_with_source(entities_dir):
    """Glob all .md files under entities_dir and parse each.

    Entities stored under entities/subscribed/{name}/ have ``_source`` set to
    the subscription name so format_entities can annotate them. The owner field
    written by publish.py is preserved; _source is just a routing key used
    internally and is never written to disk.
    """
    entities_dir = Path(entities_dir)
    entities = []
    for md in sorted(p for p in entities_dir.glob("**/*.md") if ".git" not in p.parts):
        if md.is_symlink():
            continue
        try:
            entity = markdown_to_entity(md)
            if not entity.get("content"):
                continue
            # Detect subscribed entities by path: .../entities/subscribed/{name}/...
            parts = md.parts
            try:
                entities_index = parts.index("entities")
                # Verify the structure is .../entities/subscribed/{name}/...
                if entities_index + 2 < len(parts) and parts[entities_index + 1] == "subscribed":
                    entity["_source"] = parts[entities_index + 2]
            except (ValueError, IndexError):
                # "entities" not found or invalid structure - not a subscribed entity
                pass
            entities.append(entity)
        except (OSError, UnicodeError):
            pass
    return entities


def main():
    # Read hook context from stdin (retrieve_entities.sh pipes HOOK_TOOL_INPUT
    # here). This is best-effort: if stdin is empty or not valid JSON we carry
    # on, because entity loading doesn't depend on it.
    input_data = {}
    try:
        raw = sys.stdin.read()
        if raw.strip():
            input_data = json.loads(raw)
            if isinstance(input_data, dict):
                log(f"Parsed stdin — keys: {list(input_data.keys())}")
            else:
                log(f"Parsed stdin — type: {type(input_data).__name__}")
        else:
            log("stdin was empty")
    except json.JSONDecodeError as e:
        log(f"stdin was not valid JSON ({e}), continuing without it")

    recall_dirs = find_recall_entity_dirs()
    log(f"Recall dirs: {recall_dirs}")
    if not recall_dirs:
        log("No entities directory found")
        return

    entities = []
    for entities_dir in recall_dirs:
        entities.extend(load_entities_with_source(entities_dir))

    if not entities:
        log("No entities found")
        return

    log(f"Loaded {len(entities)} entities")
    output = format_entities(entities)
    print(output)
    log(f"Output {len(output)} chars to stdout")


if __name__ == "__main__":
    main()
