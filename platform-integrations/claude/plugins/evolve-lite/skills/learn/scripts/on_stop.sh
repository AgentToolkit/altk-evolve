#!/bin/bash
INPUT=$(cat)
STOP_HOOK_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active')

# Guard against infinite loop — don't inject if already continuing from a stop hook
if [ "$STOP_HOOK_ACTIVE" = "true" ]; then
  exit 0
fi

echo '{
  "decision": "block",
  "reason": "Run the /evolve-lite:learn skill to extract guidelines from this conversation.",
  "suppressOutput": true,
  "statusMessage": "Running the evolve-lite learn skill..."
}'
