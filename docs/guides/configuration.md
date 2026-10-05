# Configuration

Evolve uses environment variables for configuration. You can set these in a `.env` file or export them directly.

## LLM Configuration

**Required for OpenAI models:**
```bash
export OPENAI_API_KEY=sk-...
```

### Custom LLM Configuration

Evolve uses [LiteLLM](https://docs.litellm.ai/) and supports OpenAI-compatible proxy endpoints (including LiteLLM) via standard OpenAI environment variables:

```bash
# OpenAI-compatible endpoint configuration (works with LiteLLM)
export OPENAI_API_KEY="your-api-key"
export OPENAI_BASE_URL="https://your-litellm-proxy.com/v1"

# Evolve Model Configuration
export EVOLVE_GUIDELINES_MODEL="openai/gpt-4o-mini"
export EVOLVE_CONFLICT_RESOLUTION_MODEL="openai/gpt-4o-mini"
export EVOLVE_FACT_EXTRACTION_MODEL="openai/gpt-4o-mini"
export EVOLVE_MODEL_NAME="openai/gpt-4o-mini"
export EVOLVE_CUSTOM_LLM_PROVIDER="openai"
```

Model selection precedence:
1. Task-specific models: `EVOLVE_GUIDELINES_MODEL`, `EVOLVE_CONFLICT_RESOLUTION_MODEL`, `EVOLVE_FACT_EXTRACTION_MODEL`
2. Global Evolve fallback: `EVOLVE_MODEL_NAME`
3. Built-in default: `gpt-4o`

`EVOLVE_TIPS_MODEL` is still accepted as a deprecated fallback for one release cycle.

If `EVOLVE_*_MODEL` are unset, set `EVOLVE_MODEL_NAME` to control all Evolve LLM calls.

## Environment Variables

All configuration variables are prefixed with `EVOLVE_`.

### General Settings

| Variable | Description                                                                   | Default                                  |
|----------|-------------------------------------------------------------------------------|------------------------------------------|
| `EVOLVE_BACKEND` | Backend provider (`milvus`, `filesystem`, or `postgres`)                      | `milvus`                                 |
| `EVOLVE_NAMESPACE_ID` | Namespace ID for isolation                                                    | `evolve`                                 |
| `EVOLVE_GUIDELINES_MODE` | Guideline generation pipeline: `standard`, `consistency`, or `all` — see [Enabling Guidelines](guidelines.md) | `standard` |
| `EVOLVE_CONSISTENCY_METHOD` | Consistency mode only: `fast` (LLM self-judged) or `accurate` (resampling based) — see [Enabling Guidelines](guidelines.md#choosing-a-consistency-method) | `fast` |
| `EVOLVE_CONSISTENCY_RESAMPLE_MAX_WORKERS` | `accurate` method only: how many resampling calls run in parallel when a provider won't return several completions in one call — see [Enabling Guidelines](guidelines.md#choosing-a-consistency-method). Raise it to cut resampling wall-clock, lower it to `1` if the provider rate-limits you | `4` |
| `EVOLVE_SEGMENTATION_ENABLED` | Segment trajectories into logical subtasks before generating guidelines. Governs all three generation paths (`standard`, and both `consistency` methods). Disabled by default — see [Trajectory segmentation](#trajectory-segmentation) | `false` |
| `EVOLVE_TRAJECTORY_MAX_STEPS` | Most trajectory steps rendered into a generation prompt — see [Trajectory limits](#trajectory-limits) | `50` |
| `EVOLVE_TRAJECTORY_MAX_STEP_CHARS` | Characters kept per step before truncation | `2000` |
| `EVOLVE_TRAJECTORY_TAIL_STEPS` | How much of the step budget is reserved for the **end** of a long run. `0` truncates head-only, as before — see [Trajectory limits](#trajectory-limits) | `0` |
| `EVOLVE_GUIDELINES_MODEL` | Model for guideline generation only | `EVOLVE_MODEL_NAME` -> `gpt-4o` |
| `EVOLVE_CONFLICT_RESOLUTION_MODEL` | Model for conflict resolution only | `EVOLVE_MODEL_NAME` -> `gpt-4o` |
| `EVOLVE_FACT_EXTRACTION_MODEL` | Model for fact extraction only | `EVOLVE_MODEL_NAME` -> `gpt-4o` |
| `EVOLVE_MODEL_NAME` | Global fallback model for all Evolve LLM calls | `gpt-4o` |
| `EVOLVE_CUSTOM_LLM_PROVIDER` | LiteLLM provider (use `openai` for OpenAI-compatible endpoints). Defaults to `openai` whenever `OPENAI_API_KEY` or `OPENAI_BASE_URL` is set, even if you never set this variable yourself — see the [consistency guide](guidelines.md#choosing-a-consistency-method) for a case where that implicit default causes misrouting | `openai` if `OPENAI_API_KEY`/`OPENAI_BASE_URL` is set, else `None` |
| `EVOLVE_EMBEDDING_MODEL` | Embedding model                                                               | `sentence-transformers/all-MiniLM-L6-v2` |

### Trajectory limits

A trajectory is rendered into the generation prompt as at most `EVOLVE_TRAJECTORY_MAX_STEPS`
steps of at most `EVOLVE_TRAJECTORY_MAX_STEP_CHARS` characters each. Both were fixed at 50
and 2000 before; the defaults are unchanged.

**Keeping the end of a long run.** By default the window is head-only: a 200-step run is
rendered as its first 50 steps, and the 150 that follow — including whatever the run
finished with — are not in the prompt at all. A run's outcome is at its end, so a guideline
mined from the head alone can state an approach that the run went on to abandon.
`EVOLVE_TRAJECTORY_TAIL_STEPS` carves part of the budget out for the end:

```bash
# 40 steps from the start, 10 from the end, the middle replaced by a marker
EVOLVE_TRAJECTORY_MAX_STEPS=50
EVOLVE_TRAJECTORY_TAIL_STEPS=10
```

The tail comes **out of** `EVOLVE_TRAJECTORY_MAX_STEPS`, not on top of it, so the prompt
never grows past the budget; the omitted middle is announced in the prompt as
`[... N intermediate step(s) omitted ...]` rather than silently closed up. It must stay
below `EVOLVE_TRAJECTORY_MAX_STEPS` — a tail that consumed the whole budget would leave no
steps from the start of the run.

Leave it at `0` if you compare results against runs recorded earlier: it changes which steps
reach the model, so guidelines mined before and after the change come from different views
of the same trajectory. One interaction to know about: with elision in effect, the
`accurate` consistency method skips segmentation on any trajectory long enough to be elided,
because its step numbering and the segmenter's stop agreeing across the gap. Trajectories
within the step budget are unaffected, as is the `fast` method.

### Trajectory segmentation

`EVOLVE_SEGMENTATION_ENABLED` is **disabled by default**. With it enabled, a trajectory is
split into subtasks and each subtask gets its own guideline-generation call.

**Why it defaults to off.** A subtask boundary can fall between a failed attempt and the
correction that followed it. The failing segment is then summarised on its own terms — its
description asserts the wrong approach — and the generator writes confident guidelines from
it, with no access to the correction in the neighbouring segment. Both segments' output is
stored at equal `support_count`, so nothing marks one as the anti-lesson, and retrieval can
rank the inverted rule above the correct one. In end-to-end measurement on a
learning-episode trajectory, enabling segmentation produced directly contradictory ordering
rules and cost roughly half of the achievable improvement on the affected cases; disabling
it removed the contradiction at source.

**What you give up by leaving it off.** `task_description` becomes the raw first user
message, verbatim, shared by every guideline from that trajectory. It is the clustering key
and the retrieval ranking key, so within a single trajectory it no longer separates
subtasks, and it carries whatever user-specific values the original request contained into
stored entity metadata. If your trajectories are single-purpose, this costs little; if one
trajectory routinely spans unrelated subtasks, weigh enabling it.

**Turning it on later (mixed corpora).** Entities written while segmentation was off carry
verbatim request text as `task_description`; entities written with it on carry generalized
subtask descriptions. Both are embedded in the same space, so a namespace written across a
change of this setting holds two kinds of key. Nothing breaks and no migration is required
— clustering and retrieval keep working — but similarity between the two kinds is lower
than within either, so recurrence spanning the change may go undetected. To avoid the mix,
use a fresh namespace when you change this setting.

### Milvus Backend Settings

When `EVOLVE_BACKEND=milvus`:

| Variable | Description | Default |
|----------|-------------|---------|
| `EVOLVE_URI` | Milvus URI (file path for Lite) | `entities.milvus.db` |
| `EVOLVE_USER` | Milvus user (optional) | `""` |
| `EVOLVE_PASSWORD` | Milvus password (optional) | `""` |
| `EVOLVE_DB_NAME` | Milvus database name (optional) | `""` |
| `EVOLVE_TOKEN` | Milvus token (optional) | `""` |
| `EVOLVE_TIMEOUT` | Milvus timeout (optional) | `None` |

### Filesystem Backend Settings

When `EVOLVE_BACKEND=filesystem`:

| Variable | Description | Default |
|----------|-------------|---------|
| `EVOLVE_DATA_DIR` | Directory to store JSON data files | `evolve_data` |

### Postgres Backend Settings

When `EVOLVE_BACKEND=postgres`:

| Variable | Description | Default |
|----------|-------------|---------|
| `EVOLVE_PG_HOST` | PostgreSQL host | `localhost` |
| `EVOLVE_PG_PORT` | PostgreSQL port | `5432` |
| `EVOLVE_PG_USER` | PostgreSQL user | `postgres` |
| `EVOLVE_PG_PASSWORD` | PostgreSQL password | `postgres` |
| `EVOLVE_PG_DBNAME` | PostgreSQL database name | `evolve` |
| `EVOLVE_PG_AUTO_CREATE_DB` | Automatically create `EVOLVE_PG_DBNAME` when missing | `false` |
| `EVOLVE_PG_BOOTSTRAP_DB` | Existing database to connect to for `CREATE DATABASE` bootstrap | `postgres` |
| `EVOLVE_PG_EMBEDDING_MODEL` | Embedding model used for pgvector-backed entities | `sentence-transformers/all-MiniLM-L6-v2` |

## Storage Backends

Evolve supports three storage backends:

| Backend | Description | Search | Best For |
|---------|-------------|--------|----------|
| **Milvus** (default) | Vector database with embeddings | Semantic similarity | Production |
| **Filesystem** | JSON files, no embeddings | Text substring match | Development/testing |
| **Postgres** | PostgreSQL with pgvector embeddings | Semantic similarity | Teams already running PostgreSQL |

### Switching Backends

```bash
# Use Milvus backend (default)
export EVOLVE_BACKEND=milvus

# Use Filesystem backend
export EVOLVE_BACKEND=filesystem

# Use Postgres backend
export EVOLVE_BACKEND=postgres
```

### Filesystem Backend Details

The filesystem backend stores all data in JSON files - one file per namespace. This is ideal for:
- Local development and testing
- Debugging (you can inspect/edit the JSON files directly)
- Environments where you don't want to run Milvus
- Quick prototyping without embedding model overhead

**JSON File Structure:**

Each namespace is stored as `<data_dir>/<namespace_id>.json`:

```json
{
  "id": "my_guidelines",
  "created_at": "2026-01-13T21:29:51.986882+00:00",
  "entities": [
    {
      "id": "1",
      "type": "guideline",
      "content": "Always write tests before code",
      "created_at": "2026-01-13T21:30:00.023283+00:00",
      "metadata": null
    }
  ],
  "next_id": 2
}
```

## Low-Code Tracing (Phoenix Integration)

Evolve provides easy integration with Phoenix for tracing LLM calls.

### Installation

```bash
pip install evolve[tracing]
```

### Usage

First, enable auto-mode by setting the environment variable:

```bash
export EVOLVE_AUTO_ENABLED=true
```

Then, add one import at the top of your agent to trigger the patching:

```python
try:
    import altk_evolve.auto # noqa: F401
except ImportError:
    pass

# Your existing code unchanged...
```

### Tracing Environment Variables

| Variable | Description | Default |
| ----- | ----- | ----- |
| `EVOLVE_AUTO_ENABLED` | Enable auto-patching on import | `false` |
| `EVOLVE_TRACING_PROJECT` | Phoenix project name | `evolve-agent` |
| `EVOLVE_TRACING_ENDPOINT` | Phoenix collector endpoint | `http://localhost:6006/v1/traces` |

> **Note**: Auto-patching skips if existing tracing is detected. Use `enable_tracing(force=True)` to override.

## Runtime processing profiles

Processing profiles select built-in or installed trajectory processors with validated,
versioned configuration. Profiles use the existing configured database: PostgreSQL
for the PostgreSQL backend, or SQLite for filesystem and Milvus. Milvus already uses SQLite
for namespace metadata; filesystem namespaces remain in JSON and profile SQLite defaults to
`entities.sqlite.db` inside `EVOLVE_DATA_DIR`. Explicit `EVOLVE_SQLITE_PATH` /
`EVOLVE_SQLITE_URI` overrides are respected. Milvus profiles use its configured
`sqlite_uri` (or `EVOLVE_SQLITE_PATH` override). No separate profile database configuration is required; applications
can still inject a custom repository. See [processing profiles](../design/processing-profiles.md)
for Python, REST, MCP, CLI, and plugin-discovery examples.
