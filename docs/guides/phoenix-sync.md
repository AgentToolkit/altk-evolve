# Phoenix Sync

Sync agent trajectories from Arize Phoenix to Evolve and automatically generate tips/guidelines.

## Overview

The Phoenix sync module:
1. Fetches agent trajectories from Phoenix's REST API
2. Deduplicates already-processed trajectories (by `span_id`)
3. Converts messages to OpenAI format
4. Generates tips/guidelines using LLM
5. Stores both trajectories and tips in Evolve

## Installation

No additional dependencies required - uses only stdlib for Phoenix API calls.

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PHOENIX_URL` | `http://localhost:6006` | Phoenix server URL |
| `PHOENIX_PROJECT` | `default` | Phoenix project name |
| `EVOLVE_NAMESPACE_ID` | `evolve` | Target namespace for stored entities |
| `EVOLVE_BACKEND` | `milvus` | Backend provider (`milvus` or `filesystem`) |

## Usage

### CLI

```bash
# Basic sync with defaults
uv run evolve sync phoenix

# Custom Phoenix URL and namespace
uv run evolve sync phoenix \
  --url http://phoenix.example.com:6006 \
  --namespace my_namespace

# Fetch more spans and include errors
uv run evolve sync phoenix \
  --limit 500 \
  --include-errors

# Full options
uv run evolve sync phoenix \
  --url http://localhost:6006 \
  --namespace production \
  --project my_project \
  --limit 200 \
  --include-errors
```

### CLI Options

| Option | Short | Description |
|--------|-------|-------------|
| `--url` | `-u` | Phoenix server URL |
| `--namespace` | `-n` | Target Evolve namespace |
| `--project` | `-p` | Phoenix project name |
| `--limit` | | Max spans to fetch (default: 100) |
| `--include-errors` | | Include failed/error spans |

### Python API

```python
from evolve.sync.phoenix_sync import PhoenixSync

# Initialize syncer
syncer = PhoenixSync(
    phoenix_url="http://localhost:6006",
    namespace_id="my_namespace",
    project="default"
)

# Run sync
result = syncer.sync(limit=100, include_errors=False)

print(f"Processed: {result.processed}")
print(f"Skipped: {result.skipped}")
print(f"Tips generated: {result.tips_generated}")
print(f"Errors: {result.errors}")
```

## How It Works

### 1. Fetch Spans

The syncer calls Phoenix's REST API:
```
GET /v1/projects/{project}/spans?limit=N&cursor=CURSOR
```

Only `litellm_request` spans with prompt messages are processed.

### 2. Deduplication

Each processed trajectory stores `span_id` in its metadata. On subsequent syncs, already-processed span IDs are skipped.

### 3. Message Conversion

Anthropic/Claude message format is converted to OpenAI format:
- Tool use blocks → `tool_calls` array
- Tool results → separate `tool` role messages
- Thinking blocks → preserved in `thinking` field

### 4. Tip Generation

The `generate_tips()` function analyzes the trajectory and produces actionable guidelines using an LLM.

### 5. Storage

Two entity types are stored:
- `trajectory` - Individual messages with metadata (trace_id, span_id, model, role)
- `guideline` - Generated tips with conflict resolution enabled

## Data Flow

```
┌─────────────┐      fetch      ┌───────────────────┐
│   Phoenix   │ ───────────────→│   PhoenixSync     │
│   (spans)   │                 │                   │
└─────────────┘                 └─────────┬─────────┘
                                          │
                    ┌─────────────────────┼─────────────────────┐
                    │                     │                     │
                    ▼                     ▼                     ▼
            ┌───────────────┐    ┌───────────────┐    ┌───────────────┐
            │  Deduplicate  │    │   Convert to  │    │   Generate    │
            │  (by span_id) │    │ OpenAI format │    │     Tips      │
            └───────────────┘    └───────────────┘    └───────┬───────┘
                                                              │
                                                              ▼
                                                    ┌───────────────┐
                                                    │    Evolve     │
                                                    │   Backend     │
                                                    └───────────────┘
```

## Running on a Schedule

### Cron

```bash
# Sync every hour
0 * * * * cd /path/to/altk-evolve && uv run evolve sync phoenix --limit 100
```

### Systemd Timer

```ini
# /etc/systemd/system/evolve-sync.service
[Unit]
Description=Evolve Phoenix Sync

[Service]
Type=oneshot
WorkingDirectory=/path/to/altk-evolve
ExecStart=/path/to/uv run evolve sync phoenix
Environment=PHOENIX_URL=http://localhost:6006
Environment=EVOLVE_NAMESPACE_ID=production
```

```ini
# /etc/systemd/system/evolve-sync.timer
[Unit]
Description=Run Evolve Phoenix Sync hourly

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

## Troubleshooting

### Connection refused

Ensure Phoenix is running and accessible at the configured URL:
```bash
curl http://localhost:6006/v1/projects/default/spans?limit=1
```

### No spans processed

- Check that spans have `name="litellm_request"`
- Verify spans contain `gen_ai.prompt.*` attributes
- Use `--include-errors` to include failed spans

### Tips not generating

Ensure LLM API key is configured:
```bash
export OPENAI_API_KEY=sk-...
# or for other providers
export ANTHROPIC_API_KEY=sk-...
```
