---
name: gist
description: Generate a purpose-directed gist of the current conversation optimized for remembering user preferences and attributes across sessions.
context: fork
---

# Gist Memory

## Overview

This skill generates a **purpose-directed gist** of the current conversation — a compressed representation optimized for answering questions about the user (preferences, behaviors, habits, attributes). Unlike generic summarization, purpose-directed gisting foregrounds user-relevant signal and discards topical noise, making it dramatically more effective for personalization in future sessions.

The gist will be stored as an entity and automatically injected into future sessions via the recall hook.

## Workflow

### Step 1: Walk Through Conversation Messages

Review all messages in the current conversation from start to finish. Collect all user and assistant messages as a list.

### Step 2: Generate the Gist

Create a gist of the conversation following these specific instructions:

**You are creating a gist that will be stored in a vector database and used to answer questions about the user.**

Therefore:
- **Focus on what the conversation reveals about the user** — their preferences, behaviors, habits, expertise, opinions, constraints, and attributes
- **It can contain phrases and keywords** — does not need complete sentences
- **It is not intended to be read by humans** — optimize for machine retrieval
- **Discard topical noise** — if the user discussed Kubernetes for 20 messages but mentioned preferring Python in one sentence, the Python preference is higher signal for the gist than the Kubernetes discussion
- If there is nothing notable about the user in the conversation, output "no user signal" and stop

**Example:** A conversation about network routing where the user mentions "By the way, I strongly prefer Python over R for data analysis" should produce a gist like:
```
user prefers Python over R for data analysis; mentioned during networking discussion
```
NOT a summary of the networking discussion.

### Step 3: Save the Gist

Output the gist as a JSON entity and save it using the save_entities.py script:

```bash
echo '<your-json>' | python3 ${CLAUDE_PLUGIN_ROOT}/skills/learn/scripts/save_entities.py
```

The JSON format:
```json
{
  "entities": [
    {
      "content": "<the gist text>",
      "type": "gist",
      "rationale": "Purpose-directed gist for personalization",
      "trigger": "When answering questions about the user's preferences or attributes"
    }
  ]
}
```

### Step 4: Confirm

Tell the user what was captured in the gist. Be brief — just list the user-relevant signals that were preserved.

## Examples

### Good Gist (purpose-directed)
Conversation: 20 messages about Kubernetes pod networking, one mention of preferring dark mode in IDEs
```
user prefers dark mode in IDEs; works with Kubernetes networking; container orchestration context
```

### Bad Gist (topic-preserving summary)
```
Discussion covered Kubernetes pod networking including CNI plugins, service mesh patterns, and ingress configuration. The user asked about Calico vs Cilium performance benchmarks.
```
This is a topic summary, not a user-attribute gist. It would fail to surface the dark mode preference in future sessions.

### Good Gist (multiple signals)
```
user: senior backend engineer; prefers Go over Rust for systems work; uses Neovim; dislikes ORMs; team of 5; shipping deadline March 30
```

### No-Signal Case
Conversation: User asks "What time is it?" and gets an answer.
```
no user signal
```
