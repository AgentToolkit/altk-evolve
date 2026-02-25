# Configuration

Kaizen uses environment variables for configuration. You can set these in a `.env` file or export them directly.

## LLM Configuration

**Required for OpenAI models:**
```bash
export OPENAI_API_KEY=sk-...
```

### Custom LLM Configuration

Kaizen uses [LiteLLM](https://docs.litellm.ai/) and supports using a LiteLLM proxy server for centralized LLM access:

```bash
# LiteLLM Proxy Configuration
export LITELLM_PROXY_API_KEY="your-proxy-token"
export LITELLM_PROXY_API_BASE="https://your-litellm-proxy.com/v1"

# Kaizen Model Configuration
export KAIZEN_TIPS_MODEL="openai/gpt-4o-mini"
export KAIZEN_CONFLICT_RESOLUTION_MODEL="openai/gpt-4o-mini"
export KAIZEN_FACT_EXTRACTION_MODEL="openai/gpt-4o-mini"
export KAIZEN_CUSTOM_LLM_PROVIDER="openai"
```

Model selection precedence:
1. Task-specific models: `KAIZEN_TIPS_MODEL`, `KAIZEN_CONFLICT_RESOLUTION_MODEL`, `KAIZEN_FACT_EXTRACTION_MODEL`
2. Global Kaizen fallback: `KAIZEN_MODEL_NAME`
3. Shared fallback (commonly used in tests): `MODEL_NAME`
4. Built-in default: `gpt-4o`

For tests, if `KAIZEN_*_MODEL` and `KAIZEN_MODEL_NAME` are unset, set `MODEL_NAME` to control all Kaizen LLM calls.

## Environment Variables

All configuration variables are prefixed with `KAIZEN_`.

### General Settings

| Variable | Description                                                                   | Default                                  |
|----------|-------------------------------------------------------------------------------|------------------------------------------|
| `KAIZEN_BACKEND` | Backend provider (`milvus` or `filesystem`)                                   | `milvus`                                 |
| `KAIZEN_NAMESPACE_ID` | Namespace ID for isolation                                                    | `kaizen`                                 |
| `KAIZEN_TIPS_MODEL` | Model for tip generation only | `KAIZEN_MODEL_NAME` -> `MODEL_NAME` -> `gpt-4o` |
| `KAIZEN_CONFLICT_RESOLUTION_MODEL` | Model for conflict resolution only | `KAIZEN_MODEL_NAME` -> `MODEL_NAME` -> `gpt-4o` |
| `KAIZEN_FACT_EXTRACTION_MODEL` | Model for fact extraction only | `KAIZEN_MODEL_NAME` -> `MODEL_NAME` -> `gpt-4o` |
| `KAIZEN_MODEL_NAME` | Global fallback model for all Kaizen LLM calls | `MODEL_NAME` -> `gpt-4o` |
| `MODEL_NAME` | Shared cross-project fallback (used by tests if Kaizen-specific vars are unset) | `gpt-4o` |
| `KAIZEN_CUSTOM_LLM_PROVIDER` | LiteLLM provider (use `openai` for proxy with custom models) | `None`                                   |
| `KAIZEN_EMBEDDING_MODEL` | Embedding model                                                               | `sentence-transformers/all-MiniLM-L6-v2` |

### Milvus Backend Settings

When `KAIZEN_BACKEND=milvus`:

| Variable | Description | Default |
|----------|-------------|---------|
| `KAIZEN_URI` | Milvus URI (file path for Lite) | `entities.milvus.db` |
| `KAIZEN_USER` | Milvus user (optional) | `""` |
| `KAIZEN_PASSWORD` | Milvus password (optional) | `""` |
| `KAIZEN_DB_NAME` | Milvus database name (optional) | `""` |
| `KAIZEN_TOKEN` | Milvus token (optional) | `""` |
| `KAIZEN_TIMEOUT` | Milvus timeout (optional) | `None` |

### Filesystem Backend Settings

When `KAIZEN_BACKEND=filesystem`:

| Variable | Description | Default |
|----------|-------------|---------|
| `KAIZEN_DATA_DIR` | Directory to store JSON data files | `kaizen_data` |

## Storage Backends

Kaizen supports two storage backends:

| Backend | Description | Search | Best For |
|---------|-------------|--------|----------|
| **Milvus** (default) | Vector database with embeddings | Semantic similarity | Production |
| **Filesystem** | JSON files, no embeddings | Text substring match | Development/testing |

### Switching Backends

```bash
# Use Milvus backend (default)
export KAIZEN_BACKEND=milvus

# Use Filesystem backend
export KAIZEN_BACKEND=filesystem
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

Kaizen provides easy integration with Phoenix for tracing LLM calls.

### Installation

```bash
pip install kaizen[tracing]
```

### Usage

First, enable auto-mode by setting the environment variable:

```bash
export KAIZEN_AUTO_ENABLED=true
```

Then, add one import at the top of your agent to trigger the patching:

```python
try:
    import kaizen.auto # noqa: F401
except ImportError:
    pass

# Your existing code unchanged...
```

### Tracing Environment Variables

| Variable | Description | Default |
| ----- | ----- | ----- |
| `KAIZEN_AUTO_ENABLED` | Enable auto-patching on import | `false` |
| `KAIZEN_TRACING_PROJECT` | Phoenix project name | `kaizen-agent` |
| `KAIZEN_TRACING_ENDPOINT` | Phoenix collector endpoint | `http://localhost:6006/v1/traces` |

> **Note**: Auto-patching skips if existing tracing is detected. Use `enable_tracing(force=True)` to override.
