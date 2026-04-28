---
name: recall
description: Must be used at the start of any non-trivial task involving code changes, debugging, repo exploration, file inspection, or environment/tooling investigation to surface stored guidance before analysis or tool use.
---

# Entity Retrieval

## Overview

This skill loads relevant stored Evolve entities into the current turn before substantive work begins.

Use this skill first whenever the task involves:
- code changes
- debugging
- code review
- repo exploration
- file inspection
- environment/tooling investigation

Skip only for trivial conversational requests with no local context.

## Required Action

Before any non-trivial local work, you must complete the recall workflow below. Reading this `SKILL.md` alone does not satisfy the skill.

### Completion Rule

Do not proceed to other analysis or tool use until all steps below are complete.

1. Inspect `.evolve/entities/` for guidance relevant to the current task.
2. Read each matching entity file that appears relevant.
3. Summarize the applicable guidance in your own words before proceeding.
4. If no relevant entities exist, state that explicitly before proceeding.

### Required Visible Completion Note

Before moving on, produce an explicit completion note in your reasoning or user update using one of these forms:

- `Recall complete: searched .evolve/entities/, read <files>, applicable guidance: <summary>`
- `Recall complete: searched .evolve/entities/, no relevant entities found`

### Minimum Acceptable Procedure

1. List or search files under `.evolve/entities/`.
2. Identify candidate entities relevant to the task.
3. Open and read those entity files.
4. Summarize what applies, or state that nothing applies.

### Failure Conditions

The skill is not complete if any of the following are true:

- You only read this `SKILL.md`
- You did not inspect `.evolve/entities/`
- You did not read the relevant entity files
- You proceeded without stating whether guidance was found

## How It Works

1. If Codex hooks are enabled in `~/.codex/config.toml` with `[features] codex_hooks = true`, the Codex `UserPromptSubmit` hook runs before the prompt is sent.
2. The helper script reads the prompt JSON from stdin.
3. It loads stored entities from `.evolve/entities/` (covers private,
   read-scope subscriptions, and write-scope publish targets which all
   live under `entities/subscribed/{repo}/`).
4. It prints formatted guidance to stdout.
5. Codex adds that text as extra developer context for the turn.

## Entities Storage

```text
.evolve/entities/
  guideline/
    use-context-managers-for-file-operations.md   <- private
  subscribed/
    memory/                                       <- write-scope clone (publishes land here)
      guideline/
        my-published-guideline.md
    alice/                                        <- read-scope clone
      guideline/
        alice-guideline.md                        <- annotated [from: alice]
```

Each file uses markdown with YAML frontmatter:

```markdown
---
type: guideline
trigger: When processing files or managing resources
---

Use context managers for file operations

## Rationale

Ensures proper resource cleanup
```
