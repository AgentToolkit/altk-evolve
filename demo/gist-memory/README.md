# Gist Memory Demo — Buried Preference Recall

This demo shows how purpose-directed gisting enables an AI agent to recall user preferences that were briefly mentioned during an unrelated conversation — a task that generic summarization and standard RAG both fail at.

## The Problem

When a user buries a preference in a long, topically unrelated conversation, generic approaches fail:
- **Topic-preserving summarization** discards the preference as low-salience noise
- **Standard RAG** dilutes the preference signal in full-passage embeddings dominated by the conversation's main topic

Purpose-directed gisting solves this by compressing conversations specifically to foreground user attributes.

## Setup

### Option A: Kaizen Lite (Claude Code Plugin)

```bash
# Install the plugin
claude --plugin-dir /path/to/kaizen/platform-integrations/claude/plugins/kaizen-lite
```

### Option B: Full Kaizen (MCP Server)

```bash
# Start the MCP server
uv run fastmcp run kaizen/frontend/mcp/mcp_server.py --transport sse --port 8201
```

## Demo Script

### Session 1: Preference Embedding

Have a multi-turn conversation about an unrelated technical topic. Bury a preference in one of the messages.

See [session1_script.md](session1_script.md) for the full conversation script.

**Key message (message 5 of 12):**
> "That makes sense about the CNI plugin architecture. By the way, I strongly prefer Python over R for all my data analysis work — I find pandas much more intuitive than tidyverse. Anyway, back to the networking question — how does Cilium handle network policy enforcement?"

The preference ("Python over R", "pandas over tidyverse") is <5% of the total conversation content.

**At end of session:**
- **Lite path:** Run `/kaizen:gist`
- **MCP path:** Call `store_gist` with the conversation JSON

**Expected gist output:**
```
user prefers Python over R for data analysis; finds pandas more intuitive than tidyverse; works with Kubernetes networking (Cilium, CNI plugins)
```

Note how the gist foregrounds the Python/pandas preference despite it being a tiny fraction of the conversation.

### Session 2: Preference Recall

Start a new session and ask:

> "I need to start a new data analysis project working with network telemetry data. What language and tools would you recommend I use?"

**With gist memory:** Claude recommends Python and pandas, citing your stated preference.

**Without gist memory:** Claude gives a generic recommendation (likely mentioning both Python and R, or asking about your preference).

See [session2_script.md](session2_script.md) for the verification prompts.

## What to Look For

1. **Gist content:** Does the gist capture the Python/pandas preference despite it being buried?
2. **Recall accuracy:** In Session 2, does the agent correctly apply the preference?
3. **A/B contrast:** Run Session 2 without gist memory to see the failure mode.
