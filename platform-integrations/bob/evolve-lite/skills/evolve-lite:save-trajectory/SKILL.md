---
name: save-trajectory
description: Save the current conversation as a trajectory JSON file in OpenAI chat completion format for analysis and fine-tuning
---

# Save Trajectory

## Overview

This skill saves the current session's conversation history as a JSON file in OpenAI chat completion format. The trajectory is saved to `.evolve/trajectories/` in the project root. This enables trajectory analysis, fine-tuning data collection, and session review.

## Workflow

### Step 1: Walk Through Conversation Messages

Review all messages in the current conversation from start to finish. For each message, identify its type:

- **User text messages**
- **Assistant text responses** (may include thinking)
- **Assistant tool calls**
- **Tool results**

### Step 2: Convert to OpenAI Chat Completion Format

Convert each message to the appropriate format:

**User text message:**
```json
{"role": "user", "content": "the user's message text"}
```

**Assistant text response (no thinking):**
```json
{"role": "assistant", "content": "the assistant's response text"}
```

**Assistant text response (with thinking):**
```json
{"role": "assistant", "content": "the assistant's response text", "thinking": "the thinking/reasoning text"}
```

**Assistant tool call (no visible text):**
```json
{
  "role": "assistant",
  "content": null,
  "tool_calls": [
    {
      "id": "tool_call_id_here",
      "type": "function",
      "function": {
        "name": "ToolName",
        "arguments": "{\"param\": \"value\"}"
      }
    }
  ]
}
```

**Assistant tool call with text:**
```json
{
  "role": "assistant",
  "content": "text before/after the tool call",
  "tool_calls": [
    {
      "id": "tool_call_id_here",
      "type": "function",
      "function": {
        "name": "ToolName",
        "arguments": "{\"param\": \"value\"}"
      }
    }
  ]
}
```

**Tool result:**
```json
{"role": "tool", "tool_call_id": "tool_call_id_here", "content": "the tool output text"}
```

#### Important Details

- **Tool call arguments must be a JSON string**, not a nested object. Use `json.dumps()` on the arguments object.
- **Tool call IDs**: Use the actual tool call ID from the conversation. If not available, generate a unique ID like `call_001`, `call_002`, etc.
- **Multiple tool calls**: If the assistant made multiple tool calls in one turn, include all of them in a single assistant message's `tool_calls` array, followed by separate tool result messages for each.
- **Thinking blocks**: If the assistant had both thinking and text in the same turn, combine them into one message with both `content` and `thinking` fields.

### Step 3: Clean Content

Strip `<system-reminder>...</system-reminder>` tags and their contents from all message content. Use a non-greedy multiline match (e.g., `re.sub(r'<system-reminder>[\s\S]*?</system-reminder>', '', text).strip()`). If after stripping, a message has empty content and no tool calls, omit it.

### Step 4: Save via Script

Wrap the messages array in the trajectory envelope and pipe it to the save script using a heredoc. The script handles directory creation, timestamped filename, and file writing.

```bash
python3 .bob/skills/evolve-lite:save-trajectory/scripts/save_trajectory.py << 'TRAJECTORY_END'
{
  "model": "<model-id-from-session>",
  "timestamp": "2025-01-15T10:30:00Z",
  "messages": [...]
}
TRAJECTORY_END
```

- **model**: Use the exact model ID from the current session's environment context (e.g., the value after "You are powered by the model named ..."). Do not hardcode a default — always read it from the session.
- **timestamp**: Current ISO 8601 timestamp
- Use a single-quoted heredoc delimiter (`<< 'TRAJECTORY_END'`) so the content is passed literally with no shell interpolation — safe for any quotes, backslashes, or newlines in conversation content.

The script will print the saved file path — note it for use in the learn skill.

## Example Output

```text
Trajectory saved: .evolve/trajectories/trajectory_2025-01-15T10-30-00.json
Messages: 12
```

## Notes

- This skill captures what's visible in the current conversation context. Very long sessions may have earlier messages compressed or summarized by the system. Include these summarized messages as-is with `role: "user"` or `role: "assistant"` as appropriate — do not skip them, since they preserve the conversation flow.
- The trajectory format is compatible with OpenAI chat completion format for downstream tooling.
- Trajectories are saved per-project in `.evolve/trajectories/` and can be version-controlled or gitignored as preferred.
