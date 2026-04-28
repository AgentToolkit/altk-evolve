# E2E Testing: Manifest-First Recall in Claude Container

## Goal

Verify the full recall loop end-to-end inside the Claude sandbox container:
prompt submission → `UserPromptSubmit` hook fires → manifest emitted →
Claude reads relevant entity files on demand → response reflects entity guidance.

---

## Current test coverage

| Layer | What's tested | Where |
|-------|--------------|-------|
| Shared library | `load_manifest`, `dedupe_manifest_entries`, frontmatter parsing | `tests/platform_integrations/test_entity_io_core.py` |
| Claude script | Manifest output shape, no bodies, symlink skip, deterministic order | `tests/platform_integrations/test_claude_retrieve_manifest.py` |
| Codex script | Same as above for Codex variant | `tests/platform_integrations/test_codex_retrieve_manifest.py` |
| Cross-platform | Parametrized tests covering both scripts | `tests/platform_integrations/test_retrieve.py` |
| Integration | Sync + retrieve interaction (symlinks, subscribed entities) | `tests/platform_integrations/test_sync.py` |

**Gap:** No test exercises the hook → agent → file read loop. The unit tests
prove the script emits the right manifest, but nothing confirms that Claude
actually reads entity files in response to manifest triggers.

---

## Approach 1: Container smoke test (recommended first step)

Use the existing `just claude-prompt` harness to run a single-shot prompt
against a seeded workspace.

### Prerequisites

- Docker installed
- `sandbox/myenv` with a valid `ANTHROPIC_API_KEY`
- Images built: `just sandbox-build claude`

### Test script

```bash
#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="$(mktemp -d)"
trap 'rm -rf "$WORKSPACE"' EXIT

# 1. Seed entities with distinctive, grep-able content
mkdir -p "$WORKSPACE/.evolve/entities/guideline"
cat > "$WORKSPACE/.evolve/entities/guideline/docker-exif.md" << 'EOF'
---
type: guideline
trigger: When extracting image metadata in containerized environments
---

Always use the --no-cache flag with exiftool inside Docker containers
to avoid stale layer artifacts in EXIF output.

## Rationale

Docker layer caching can cause exiftool to return metadata from a
previous build layer rather than the current file.
EOF

# 2. Run Claude with a prompt that should match the trigger
RESPONSE=$(docker run --rm \
  --env-file sandbox/myenv \
  -v "$WORKSPACE":/workspace \
  -v "$(pwd)/platform-integrations/claude/plugins":/plugins \
  -w /workspace \
  kaizen-claude \
  claude -p "I need to extract EXIF metadata from images inside a Docker container. What should I watch out for?" \
    --plugin-dir /plugins/evolve-lite \
    --dangerously-skip-permissions \
    --output-format text \
  2>/dev/null)

# 3. Assert the response references entity content
if echo "$RESPONSE" | grep -qi "no-cache\|exiftool\|stale.*layer"; then
  echo "PASS: Claude referenced the seeded entity guidance"
  exit 0
else
  echo "FAIL: Response did not reference entity content"
  echo "---"
  echo "$RESPONSE"
  exit 1
fi
```

### What this proves

- The `UserPromptSubmit` hook fires inside the container
- The manifest is emitted and visible to Claude
- Claude matches the trigger to the prompt and reads the full entity file
- The entity content influences the response

### Limitations

- **Requires a real API key** — cannot run in CI without secrets
- **Non-deterministic** — Claude may paraphrase or omit keywords; fuzzy
  assertions (multiple grep alternatives) reduce flakiness
- **Cost** — each run is one API call (~$0.02–0.10 depending on response length)
- **No structured assertion** — relies on keyword grep, not JSON contract

---

## Approach 2: Hook output capture (no API key needed)

Test only the hook layer by intercepting what the `UserPromptSubmit` hook
emits, without sending the prompt to the Claude API.

### Mechanism

Run the retrieve script directly inside the container, piping synthetic
hook input on stdin, and assert the manifest output:

```bash
WORKSPACE="$(mktemp -d)"
# ... seed entities as above ...

OUTPUT=$(docker run --rm \
  -v "$WORKSPACE":/workspace \
  -v "$(pwd)/platform-integrations/claude/plugins":/plugins \
  -w /workspace \
  -e EVOLVE_DIR=/workspace/.evolve \
  kaizen-claude \
  python3 /plugins/evolve-lite/skills/recall/scripts/retrieve_entities.py \
  <<< '{"prompt": "extracting EXIF metadata in Docker"}')

# Assert manifest shape
echo "$OUTPUT" | grep -q '"trigger": "When extracting image metadata in containerized environments"'
```

### What this proves

- The script runs correctly inside the container image (correct Python
  version, dependencies, path resolution)
- Manifest output is correct for seeded entities
- No API key required — safe for CI

### Limitations

- Does **not** prove Claude reads the entity file after seeing the manifest
- Essentially a containerized version of the existing unit tests

---

## Approach 3: Pytest e2e with subprocess (future)

Add a proper pytest e2e test under `tests/e2e/` that:

1. Builds (or reuses) the Claude container image
2. Bind-mounts a temp directory with seeded entities
3. Runs `claude -p` via `docker run` as a subprocess
4. Parses the response for entity-derived content

```python
# tests/e2e/test_manifest_recall_e2e.py

import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.container]

REQUIRES_API_KEY = not os.environ.get("ANTHROPIC_API_KEY")


@pytest.fixture
def seeded_workspace(tmp_path):
    entities = tmp_path / ".evolve" / "entities" / "guideline"
    entities.mkdir(parents=True)
    (entities / "test-entity.md").write_text(
        "---\n"
        "type: guideline\n"
        "trigger: When writing retry logic for HTTP clients\n"
        "---\n\n"
        "Use exponential backoff with jitter. Never use fixed-interval retries.\n"
    )
    return tmp_path


@pytest.mark.skipif(REQUIRES_API_KEY, reason="ANTHROPIC_API_KEY not set")
def test_claude_reads_entity_from_manifest(seeded_workspace):
    repo_root = Path(__file__).parent.parent.parent
    result = subprocess.run(
        [
            "docker", "run", "--rm",
            "--env-file", str(repo_root / "sandbox" / "myenv"),
            "-v", f"{seeded_workspace}:/workspace",
            "-v", f"{repo_root}/platform-integrations/claude/plugins:/plugins",
            "-w", "/workspace",
            "kaizen-claude",
            "claude", "-p",
            "I'm implementing retry logic for an HTTP client. Any guidelines?",
            "--plugin-dir", "/plugins/evolve-lite",
            "--dangerously-skip-permissions",
            "--output-format", "text",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0
    response = result.stdout.lower()
    assert any(
        keyword in response
        for keyword in ["exponential backoff", "jitter", "fixed-interval", "fixed interval"]
    ), f"Response did not reference entity content:\n{result.stdout[:500]}"
```

### Marker strategy

- `@pytest.mark.e2e` + `@pytest.mark.container` — excluded from default
  `pytest` runs
- Run explicitly: `pytest -m "e2e and container"` or via `just e2e-test`
- CI runs only if `ANTHROPIC_API_KEY` is available as a secret

---

## Approach 4: Structured output assertion (future, most reliable)

Use Claude's `--output-format json` or a system prompt that asks Claude to
list which entity files it read, producing machine-parseable output:

```
System: After answering, append a JSON block listing every .evolve entity
file you read during this turn: {"entities_read": ["path1", "path2"]}
```

Then assert:

```python
import re

match = re.search(r'\{"entities_read":\s*\[.*?\]\}', result.stdout)
assert match
read_list = json.loads(match.group())
assert ".evolve/entities/guideline/test-entity.md" in read_list["entities_read"]
```

This eliminates keyword-matching flakiness entirely but requires a custom
system prompt or wrapper, which adds complexity.

---

## Recommendation

1. **Now:** Ship Approach 2 (hook output capture) as a CI-safe container
   test. Proves the script works inside the image without API cost.
2. **Next:** Add Approach 1 as a manual smoke test script in `sandbox/`
   for local validation before releases.
3. **Later:** If the team wants automated proof of the full loop, implement
   Approach 3 gated behind `ANTHROPIC_API_KEY` in CI.
4. **Eventually:** Approach 4 if flakiness becomes a problem.
