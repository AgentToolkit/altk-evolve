# Kaizen Demo Scripts

This directory contains demo scripts that showcase Kaizen's automatic trajectory extraction and guideline generation capabilities.

## Overview

The demo scripts demonstrate the complete Kaizen workflow:
1. Run an agent with automatic tracing via Phoenix
2. Extract the agent trajectory from Phoenix traces
3. Generate actionable guidelines/tips from the trajectory using LLM analysis
4. Export guidelines to JSON for review

## Prerequisites

### 1. Environment Setup

Ensure you have the following installed:
- Python 3.12+
- `uv` package manager
- Kaizen virtual environment activated

### 2. Phoenix Server

Phoenix must be running for trace collection:

```bash
# Start Phoenix server (if not already running)
cd /Users/duester/Work/kaizen
source .venv/bin/activate
phoenix serve > phoenix.log 2>&1 &
```

Phoenix UI will be available at: http://localhost:6006

### 3. Environment Variables

Add the following to your `.env` file:

```bash
# OpenAI API Configuration (for LiteLLM proxy)
OPENAI_API_KEY=your-api-key-here
OPENAI_BASE_URL="https://ete-litellm.bx.cloud9.ibm.com"

# Kaizen Model Configuration (REQUIRED for tip generation)
KAIZEN_MODEL_NAME="Azure/gpt-4o"
KAIZEN_CUSTOM_LLM_PROVIDER="openai"

# Optional: Kaizen Configuration
KAIZEN_NAMESPACE_ID="kaizen"  # Default namespace for storing guidelines
KAIZEN_BACKEND="milvus"       # Backend storage (milvus or filesystem)
```

**Important**: The `KAIZEN_MODEL_NAME` and `KAIZEN_CUSTOM_LLM_PROVIDER` variables are **required** for the script to work with your LiteLLM proxy. Without these, Kaizen will try to use the default `gpt-4o` model which won't work with your proxy.

## Available Scripts

### `run_openai_agents_demo_with_tips.sh`

Complete end-to-end demo that:
- Runs an OpenAI Agents SDK demo (multi-step math problem)
- Extracts the trajectory from Phoenix
- Generates guidelines using Kaizen's LLM analysis
- Exports guidelines to JSON

**Usage:**

```bash
cd /Users/duester/Work/kaizen
./run_openai_agents_demo_with_tips.sh
```

**Output Files:**

Each run creates timestamped files (format: `HHMMSS`):
- `trajectory_openai_agents_HHMMSS.json` - Complete agent trajectory with messages, tool calls, and responses
- `generated_tips_openai_agents_HHMMSS.json` - Generated guidelines with metadata

**Example:**
```
trajectory_openai_agents_105121.json
generated_tips_openai_agents_105121.json
```

### `extract_trajectories.py`

Standalone script to extract trajectories from Phoenix traces.

**Usage:**

```bash
# Extract latest trajectory
python3 extract_trajectories.py --limit 1 -o output.json --pretty --project kaizen-agent

# Extract multiple trajectories
python3 extract_trajectories.py --limit 10 -o trajectories.json --project kaizen-agent

# Include error spans
python3 extract_trajectories.py --limit 5 --include-errors -o all_traces.json
```

**Options:**
- `--limit N` - Maximum number of trajectories to extract
- `-o FILE` - Output file path
- `--pretty` - Pretty-print JSON output
- `--project NAME` - Phoenix project name (default: `kaizen-agent`)
- `--include-errors` - Include failed/error spans

## Output Format

### Trajectory JSON Structure

```json
[
  {
    "trace_id": "e9322857544764d14df95373f62b763f",
    "span_id": "823f8dbaaf6fed8e",
    "model": "Azure/gpt-4o",
    "timestamp": "2026-03-24T14:34:29.327085+00:00",
    "messages": [
      {
        "role": "system",
        "content": "You are a helpful assistant that does math."
      },
      {
        "role": "user",
        "content": "What is (10 * 2) + 5?"
      },
      {
        "role": "assistant",
        "tool_calls": [
          {
            "id": "call_MGqYcylFgpwr0TxsA4Ca0POW",
            "type": "function",
            "function": {
              "name": "multiply",
              "arguments": "{\"a\":10,\"b\":2}"
            }
          }
        ]
      },
      {
        "role": "tool",
        "tool_call_id": "call_MGqYcylFgpwr0TxsA4Ca0POW",
        "content": 20
      }
    ],
    "usage": {
      "prompt_tokens": 150,
      "completion_tokens": 50,
      "total_tokens": 200
    }
  }
]
```

### Guidelines JSON Structure

```json
[
  {
    "id": "465138652015230980",
    "content": "Always articulate reasoning from the very first step to ensure clarity and traceability of thought.",
    "type": "guideline",
    "metadata": {
      "category": "strategy",
      "rationale": "Providing reasoning from the beginning helps identify any logical errors early and makes the problem-solving process more transparent.",
      "trigger": "When starting a new task or solving a problem.",
      "source_task_id": "05453df7ed7e67840cade0e15e13fad3",
      "source_span_id": "f6b2490c40196985",
      "task_description": "What is (10 * 2) + 5?",
      "creation_mode": "auto-phoenix"
    },
    "created_at": "2026-03-24T14:38:58+00:00"
  }
]
```

## Guideline Metadata Fields

Each generated guideline includes rich metadata:

- **`category`**: Type of guideline (e.g., "strategy", "optimization", "error-handling")
- **`rationale`**: Explanation of why this guideline is important
- **`trigger`**: When to apply this guideline
- **`source_task_id`**: Original Phoenix trace ID
- **`source_span_id`**: Original Phoenix span ID
- **`task_description`**: Description of the original task
- **`creation_mode`**: How the guideline was created:
  - `auto-phoenix`: Automatically generated from Phoenix traces
  - `auto-mcp`: Generated via MCP tool calls
  - `manual`: Manually created

## Troubleshooting

### Issue: "Model not allowed" error

**Error:**
```
AuthenticationError: team not allowed to access model. This team can only access models=['Azure/gpt-4o', ...]
```

**Solution:** Ensure your `.env` file has:
```bash
KAIZEN_MODEL_NAME="Azure/gpt-4o"
KAIZEN_CUSTOM_LLM_PROVIDER="openai"
```

### Issue: No trajectories extracted

**Possible causes:**
1. Phoenix server not running
2. Wrong project name (check with `--project kaizen-agent`)
3. No recent agent runs

**Solution:**
```bash
# Check Phoenix is running
curl http://localhost:6006/health

# Verify traces exist in Phoenix UI
open http://localhost:6006
```

### Issue: Empty guidelines generated

**Possible causes:**
1. Trajectory too simple for guideline extraction
2. LLM model configuration issue

**Solution:**
- Run a more complex agent task
- Check `KAIZEN_MODEL_NAME` is set correctly
- Review Phoenix sync logs for errors

## Managing Guidelines

### List all guidelines

```bash
uv run kaizen entities list kaizen --type guideline
```

### Search for specific guidelines

```bash
uv run kaizen entities search kaizen "error handling"
```

### View guideline details

```bash
uv run kaizen entities show kaizen <entity_id>
```

### Delete a guideline

```bash
uv run kaizen entities delete kaizen <entity_id>
```

## Advanced Usage

### Manual Phoenix Sync

To manually sync trajectories from Phoenix and generate guidelines:

```bash
uv run python -m kaizen.cli.cli sync phoenix \
  --project kaizen-agent \
  --limit 10 \
  --namespace kaizen
```

### Custom Namespace

To use a different namespace for guidelines:

```bash
export KAIZEN_NAMESPACE_ID="my-custom-namespace"
./run_openai_agents_demo_with_tips.sh
```

### Filesystem Backend

To use filesystem storage instead of Milvus:

```bash
export KAIZEN_BACKEND="filesystem"
export KAIZEN_DATA_DIR="./kaizen_data"
./run_openai_agents_demo_with_tips.sh
```

## File Naming Convention

All output files use timestamped filenames to prevent overwrites:

- **Format**: `filename_HHMMSS.json`
- **Example**: `trajectory_openai_agents_143052.json` (created at 14:30:52)
- **Uniqueness**: One-second granularity (sufficient for manual runs)

## Next Steps

1. **View traces**: Open http://localhost:6006 to see Phoenix traces
2. **Review trajectories**: Examine the extracted trajectory JSON files
3. **Analyze guidelines**: Review the generated guidelines JSON files
4. **Integrate guidelines**: Use the guidelines to improve your agents
5. **Iterate**: Run multiple experiments and compare results

## Related Documentation

- [Kaizen Main README](README.md)
- [Configuration Guide](CONFIGURATION.md)
- [Phoenix Sync Documentation](README_phoenix_sync.md)
- [CLI Documentation](CLI.md)

---

**Created with Bob** 🤖