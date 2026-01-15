# Extract Trajectories from Arize Phoenix

A Python tool to extract agent trajectories from Arize Phoenix traces and convert them to OpenAI chat completion message format.

## Features

- Fetches spans from Phoenix's REST API with pagination support
- Converts Anthropic/Claude message format to OpenAI-compatible format
- Preserves agent reasoning/thinking in a dedicated field
- Handles tool calls and tool responses
- Cleans system reminders and noise from messages
- Supports both JSON and human-readable text output

## Requirements

- Python 3.10+
- Arize Phoenix running locally (default: `http://localhost:6006`)
- No external dependencies (uses only stdlib)

## Usage

### Command Line

```bash
# Get recent trajectories as JSON (pretty-printed)
python3 extract_trajectories.py --limit 100 --pretty

# Get a specific trace by ID
python3 extract_trajectories.py --trace-id a67264f4375a60a1ac26ef3061c7352d --pretty

# Human-readable text format
python3 extract_trajectories.py --text --trace-id a67264f4375a60a1ac26ef3061c7352d

# Include failed/error spans
python3 extract_trajectories.py --include-errors --pretty

# Keep system reminders (don't clean)
python3 extract_trajectories.py --no-clean --pretty

# Save to file
python3 extract_trajectories.py --limit 100 -o trajectories.json --pretty

# Custom Phoenix URL
python3 extract_trajectories.py --url http://localhost:8080 --pretty
```

### CLI Options

| Option | Description |
|--------|-------------|
| `--url` | Phoenix server URL (default: `http://localhost:6006`) |
| `--limit` | Maximum number of spans to fetch (default: 100) |
| `--include-errors` | Include failed/error spans |
| `--no-clean` | Don't remove system reminders from messages |
| `--output, -o` | Output file path (default: stdout) |
| `--pretty` | Pretty-print JSON output |
| `--trace-id` | Filter to a specific trace ID |
| `--text` | Output as human-readable text instead of JSON |

### As a Library

```python
from extract_trajectories import (
    get_trajectories,
    get_trajectory_by_trace_id,
    format_trajectory_as_text
)

# Get all recent trajectories
trajectories = get_trajectories(
    base_url="http://localhost:6006",
    limit=100,
    include_errors=False,
    clean=True
)

# Get a specific trajectory by trace ID
trajectory = get_trajectory_by_trace_id("a67264f4375a60a1ac26ef3061c7352d")

# Format for human-readable display
if trajectory:
    print(format_trajectory_as_text(trajectory, include_thinking=True))
```

## Output Format

### JSON Output

Each trajectory is converted to an OpenAI-compatible format:

```json
{
  "trace_id": "a67264f4375a60a1ac26ef3061c7352d",
  "span_id": "c2b544103b5db193",
  "model": "aws/claude-sonnet-4-5",
  "timestamp": "2026-01-13T19:47:05.287716+00:00",
  "messages": [
    {
      "role": "user",
      "content": "What states do I have teammates in?"
    },
    {
      "role": "assistant",
      "thinking": "The user is asking me to read a file...",
      "content": "I'll help you find out what states your teammates are in.",
      "tool_calls": [
        {
          "id": "tooluse_nIHmKHC8rZ3UetpCPKjbbH",
          "type": "function",
          "function": {
            "name": "Read",
            "arguments": "{\"file_path\": \"/path/to/states.txt\"}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "tooluse_nIHmKHC8rZ3UetpCPKjbbH",
      "content": "texas\nnew york\nmassachusetts"
    },
    {
      "role": "assistant",
      "content": "Based on the file, you have teammates in Texas, New York, and Massachusetts."
    }
  ],
  "usage": {
    "prompt_tokens": 18282,
    "completion_tokens": 42,
    "total_tokens": 18324
  }
}
```

### Message Types

| Role | Description |
|------|-------------|
| `user` | User input messages |
| `assistant` | Agent responses (may include `thinking` and `tool_calls`) |
| `tool` | Tool execution results (includes `tool_call_id`) |

### Non-Standard Fields

The `thinking` field on assistant messages preserves Claude's chain-of-thought reasoning. This is not part of the OpenAI spec but is useful for:
- Debugging agent behavior
- Understanding decision-making
- Training/fine-tuning datasets

### Text Output

The `--text` flag produces human-readable output:

```
=== Trajectory: a67264f4375a... ===
Model: aws/claude-sonnet-4-5
Timestamp: 2026-01-13T19:47:05.287716+00:00

[USER]
What states do I have teammates in?

[ASSISTANT]
<thinking>
The user is asking me to read a file...
</thinking>

I'll help you find out what states your teammates are in.
  -> Tool call: Read
     Args: {"file_path": "/path/to/states.txt"}

[TOOL RESULT] (id: tooluse_nIHmKHC8rZ3U...)
texas
new york
massachusetts

[ASSISTANT]
Based on the file, you have teammates in Texas, New York, and Massachusetts.
```

## API Reference

### `get_trajectories(base_url, limit, include_errors, clean) -> list[dict]`

Fetch and extract agent trajectories from Phoenix.

**Parameters:**
- `base_url` (str): Phoenix server URL. Default: `"http://localhost:6006"`
- `limit` (int): Maximum spans to fetch. Default: `100`
- `include_errors` (bool): Include failed spans. Default: `False`
- `clean` (bool): Remove system reminders. Default: `True`

**Returns:** List of trajectory dictionaries.

### `get_trajectory_by_trace_id(trace_id, base_url) -> dict | None`

Get a single trajectory by its trace ID.

**Parameters:**
- `trace_id` (str): The trace ID to look up
- `base_url` (str): Phoenix server URL. Default: `"http://localhost:6006"`

**Returns:** Trajectory dict or `None` if not found.

### `format_trajectory_as_text(trajectory, include_thinking) -> str`

Format a trajectory as human-readable text.

**Parameters:**
- `trajectory` (dict): The trajectory dictionary
- `include_thinking` (bool): Include agent reasoning. Default: `True`

**Returns:** Formatted string.

## Phoenix API

This tool uses the Phoenix REST API endpoint:

```
GET /v1/projects/default/spans?limit=N&cursor=CURSOR
```

The spans contain attributes like:
- `gen_ai.prompt.N.role` / `gen_ai.prompt.N.content` - Input messages
- `gen_ai.completion.N.role` / `gen_ai.completion.N.content` - Output messages
- `gen_ai.request.model` - Model used
- `gen_ai.usage.*` - Token usage statistics
