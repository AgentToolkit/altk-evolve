# Kaizen Low-Code Tracing Guide

> Enable Phoenix tracing for your LLM agents with minimal code changes.

## Architecture Overview

```mermaid
flowchart TB
    subgraph Agent["Your Agent"]
        A1["import kaizen.auto"]
        A2["LLM Calls (OpenAI, LiteLLM, etc.)"]
    end
    
    subgraph Kaizen["kaizen.auto Module"]
        K1{"KAIZEN_AUTO_ENABLED?"}
        K2{"Already instrumented?"}
        K3["phoenix.otel.register()"]
        K4["OpenInference Instrumentors"]
    end
    
    subgraph Phoenix["Phoenix Server"]
        P1["Trace Collector"]
        P2["Trace Storage"]
    end
    
    subgraph Sync["kaizen sync phoenix"]
        S1["Fetch Traces"]
        S2["Generate Tips"]
        S3["Store in DB"]
    end
    
    A1 --> K1
    K1 -->|No| SKIP["No-op"]
    K1 -->|Yes| K2
    K2 -->|Yes| SKIP
    K2 -->|No| K3
    K3 --> K4
    K4 -.->|"Patches"| A2
    A2 -->|"Traces"| P1
    P1 --> P2
    P2 --> S1
    S1 --> S2
    S2 --> S3
```

## Integration

Add one import at the top of your agent:

```python
try:
    import kaizen.auto
except ImportError:
    pass

# Your agent code - all LLM calls are now traced
from openai import OpenAI
client = OpenAI()
response = client.chat.completions.create(...)
```

**Environment variables:**
```bash
export KAIZEN_AUTO_ENABLED=true
export KAIZEN_TRACING_PROJECT=my-agent  # Optional, defaults to "kaizen-agent"
export KAIZEN_TRACING_ENDPOINT=http://localhost:6006/v1/traces  # Optional
```

> **Note**: Auto-patching will skip if existing tracing is detected. Use `enable_tracing(force=True)` to override.

## Example: Simple OpenAI Script

```python
try:
    import kaizen.auto
except ImportError:
    pass

from openai import OpenAI

client = OpenAI()
response = client.chat.completions.create(
    model="gpt-4",
    messages=[{"role": "user", "content": "Hello!"}]
)
print(response.choices[0].message.content)
```

---

## Example: LiteLLM (Multi-Provider)

```python
try:
    import kaizen.auto
except ImportError:
    pass

import litellm

# Call Azure
response = litellm.completion(
    model="Azure/gpt-4",
    messages=[{"role": "user", "content": "What is 2+2?"}]
)

# Call Anthropic
response = litellm.completion(
    model="claude-3-opus-20240229",
    messages=[{"role": "user", "content": "Explain recursion."}]
)

# All calls from any provider are traced!
```

---

## Example: Smolagents (HuggingFace)

```python
try:
    import kaizen.auto
except ImportError:
    pass

from smolagents import CodeAgent, HfApiModel

model = HfApiModel()
agent = CodeAgent(tools=[], model=model, max_steps=5)

# Agent execution is traced
result = agent.run("What is the capital of France?")
print(result)
```

---

## Example: OpenAI Agents SDK

```python
try:
    import kaizen.auto
except ImportError:
    pass

from agents import Agent, Runner

agent = Agent(
    name="helper",
    instructions="You are a helpful assistant."
)

runner = Runner()
result = runner.run(agent, "Write a haiku about coding.")
print(result)
```

---

## Testing & Validation

### 1. Start Phoenix Server

```bash
pip install arize-phoenix
phoenix serve
# Server runs at http://localhost:6006
```

### 2. Run Your Agent

```bash
KAIZEN_AUTO_ENABLED=true KAIZEN_TRACING_PROJECT=test-agent python your_agent.py
```

### 3. Verify Traces in Phoenix

```bash
# Check if project exists
curl http://localhost:6006/v1/projects

# Check spans
curl "http://localhost:6006/v1/projects/test-agent/spans?limit=5"
```

### 4. Sync to Kaizen

```bash
cd kaizen_repo
KAIZEN_BACKEND=filesystem \
KAIZEN_TIPS_MODEL="gpt-4" \
uv run python -m kaizen.frontend.cli.cli sync phoenix \
    --project test-agent \
    --include-errors
```

### 5. Verify Generated Tips

```bash
KAIZEN_BACKEND=filesystem \
uv run python -m kaizen.frontend.cli.cli entities list kaizen --type guideline
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `ModuleNotFoundError: kaizen.auto` | Install: `pip install -e path/to/kaizen_repo` or add to PYTHONPATH |
| No traces appearing | Check `KAIZEN_AUTO_ENABLED=true` is set |
| Wrong project name | Set `KAIZEN_TRACING_PROJECT=your-name` |
| Existing tracer conflict | Use explicit mode with `force=True` |
| Phoenix not running | Start with `phoenix serve` |

---

## Supported Frameworks

Kaizen.auto automatically instruments these frameworks when detected:

- **OpenAI** - ChatCompletion, Completion, Embeddings
- **LiteLLM** - All providers (Azure, Anthropic, etc.)
- **Smolagents** - HuggingFace agents
- **OpenAI Agents SDK** - OpenAI's agent framework
