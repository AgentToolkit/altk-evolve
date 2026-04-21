#!/usr/bin/env python3
"""Retrieve and output entities for Claude to filter.

In claw-code this script is invoked by the PreToolUse hook (hooks/pre_tool.sh).
The hook pipes HOOK_TOOL_INPUT (the about-to-run tool's JSON input) via stdin,
and claw-code also exposes the following env vars:

  HOOK_EVENT       - "PreToolUse"
  HOOK_TOOL_NAME   - name of the tool about to execute
  HOOK_TOOL_INPUT  - JSON-encoded tool input (same bytes as stdin)

The script ignores the tool-specific payload beyond logging it; entity loading
is path-based and independent of which tool is running.
"""

import json
import os
import sys
from entity_io import find_entities_dir, load_all_entities, log as _log


def log(message):
    _log("retrieve", message)


log("Script started")

# Log claw-code hook env vars (and any CLAWD_* vars)
log("=== Hook Context ===")
hook_keys = [k for k in os.environ if k.startswith(("HOOK_", "CLAWD_"))]
for key in sorted(hook_keys):
    log(f"  {key}={os.environ[key]}")
if not hook_keys:
    log("  (no HOOK_* or CLAWD_* env vars found — may be running outside a hook)")
log("=== End Hook Context ===")

# Log command-line arguments
log(f"  sys.argv: {sys.argv}")


def format_entities(entities):
    """Format all entities for Claude to review."""
    header = """## Entities for this task

Review these entities and apply any relevant ones:

"""
    items = []
    for e in entities:
        content = e.get("content")
        if not content:
            continue
        item = f"- **[{e.get('type', 'general')}]** {content}"
        if e.get("rationale"):
            item += f"\n  - _Rationale: {e['rationale']}_"
        if e.get("trigger"):
            item += f"\n  - _When: {e['trigger']}_"
        items.append(item)

    return header + "\n".join(items)


def main():
    # Read hook context from stdin (pre_tool.sh pipes HOOK_TOOL_INPUT here).
    # This is best-effort: if stdin is empty or not valid JSON we carry on,
    # because entity loading doesn't depend on it.
    input_data = {}
    try:
        raw = sys.stdin.read()
        if raw.strip():
            input_data = json.loads(raw)
            log(f"Parsed stdin — keys: {list(input_data.keys())}")
        else:
            log("stdin was empty")
    except json.JSONDecodeError as e:
        log(f"stdin was not valid JSON ({e}), continuing without it")

    # Load all entities from directory
    entities_dir = find_entities_dir()
    log(f"Entities dir: {entities_dir}")

    if not entities_dir:
        log("No entities directory found")
        return

    entities = load_all_entities(entities_dir)
    if not entities:
        log("No entities found")
        return

    log(f"Loaded {len(entities)} entities")

    # Output all entities - Claude will filter for relevance
    output = format_entities(entities)
    print(output)
    log(f"Output {len(output)} chars to stdout")


if __name__ == "__main__":
    main()
