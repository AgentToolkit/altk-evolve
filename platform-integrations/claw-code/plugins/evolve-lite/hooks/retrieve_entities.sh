#!/usr/bin/env bash
# Optional PreToolUse hook: retrieve relevant entities before tool execution.
#
# If this script is registered by a Claw hook configuration, it can inject
# stored guidelines and preferences from the evolve-lite entity store into
# context before tool calls. The packaged plugin does not currently enable
# this hook automatically — invoke `evolve-lite:recall` manually, or wire the
# hook in your own Claw configuration to opt in.
#
# When invoked as a PreToolUse hook, the following env vars are available:
#   HOOK_EVENT        - "PreToolUse"
#   HOOK_TOOL_NAME    - name of the tool about to run
#   HOOK_TOOL_INPUT   - JSON-encoded input for that tool

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(dirname "$SCRIPT_DIR")"

# Feed the tool context into the entity retrieval script via stdin.
# The script reads it for logging; entity loading is path-based.
printf '%s' "${HOOK_TOOL_INPUT:-{}}" \
  | python3 "$PLUGIN_ROOT/skills/recall/scripts/retrieve_entities.py"
