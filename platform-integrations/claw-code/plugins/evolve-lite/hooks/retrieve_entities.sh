#!/usr/bin/env bash
# PreToolUse hook: retrieve relevant entities before each tool execution.
#
# In claw-code, PreToolUse is the closest equivalent to Claude Code's
# UserPromptSubmit hook. It fires before every tool call, injecting stored
# guidelines and preferences from the evolve-lite entity store into context.
#
# Claw-code hook environment variables available:
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
