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
export EVOLVE_TIPS_MODEL="openai/gpt-4o-mini"
export EVOLVE_CONFLICT_RESOLUTION_MODEL="openai/gpt-4o-mini"
export EVOLVE_FACT_EXTRACTION_MODEL="openai/gpt-4o-mini"
export EVOLVE_MODEL_NAME="openai/gpt-4o-mini"
export EVOLVE_CUSTOM_LLM_PROVIDER="openai"
```

Model selection precedence:
1. Task-specific models: `EVOLVE_TIPS_MODEL`, `EVOLVE_CONFLICT_RESOLUTION_MODEL`, `EVOLVE_FACT_EXTRACTION_MODEL`
2. Global Evolve fallback: `EVOLVE_MODEL_NAME`
3. Built-in default: `gpt-4o`

If `EVOLVE_*_MODEL` are unset, set `EVOLVE_MODEL_NAME` to control all Evolve LLM calls.

## Environment Variables

All configuration variables are prefixed with `EVOLVE_`.

### General Settings

| Variable | Description                                                                   | Default                                  |
|----------|-------------------------------------------------------------------------------|------------------------------------------|
| `EVOLVE_BACKEND` | Backend provider (`milvus` or `filesystem`)                                   | `milvus`                                 |
| `EVOLVE_NAMESPACE_ID` | Namespace ID for isolation                                                    | `evolve`                                 |
| `EVOLVE_TIPS_MODEL` | Model for tip generation only | `EVOLVE_MODEL_NAME` -> `gpt-4o` |
| `EVOLVE_CONFLICT_RESOLUTION_MODEL` | Model for conflict resolution only | `EVOLVE_MODEL_NAME` -> `gpt-4o` |
| `EVOLVE_FACT_EXTRACTION_MODEL` | Model for fact extraction only | `EVOLVE_MODEL_NAME` -> `gpt-4o` |
| `EVOLVE_MODEL_NAME` | Global fallback model for all Evolve LLM calls | `gpt-4o` |
| `EVOLVE_CUSTOM_LLM_PROVIDER` | LiteLLM provider (use `openai` for OpenAI-compatible endpoints) | `None`                                   |
| `EVOLVE_EMBEDDING_MODEL` | Embedding model                                                               | `sentence-transformers/all-MiniLM-L6-v2` |

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

## Storage Backends

Evolve supports two storage backends:

| Backend | Description | Search | Best For |
|---------|-------------|--------|----------|
| **Milvus** (default) | Vector database with embeddings | Semantic similarity | Production |
| **Filesystem** | JSON files, no embeddings | Text substring match | Development/testing |

### Switching Backends

```bash
# Use Milvus backend (default)
export EVOLVE_BACKEND=milvus

# Use Filesystem backend
export EVOLVE_BACKEND=filesystem
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
    import evolve.auto # noqa: F401
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
