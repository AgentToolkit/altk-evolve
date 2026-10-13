---
name: evolve-lite:recall
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

1. If a manifest has already been injected for this turn, use it to pick which entity files to open. Otherwise inspect `${EVOLVE_DIR:-.evolve}/entities/` and `${EVOLVE_DIR:-.evolve}/public/` for guidance relevant to the current task.
2. Read each matching entity file that appears relevant.
3. Summarize the applicable guidance in your own words before proceeding.
4. If no relevant entities exist, state that explicitly before proceeding.

### Required Visible Completion Note

Before moving on, produce an explicit completion note in your reasoning or user update using one of these forms:

- `Recall complete: searched ${EVOLVE_DIR:-.evolve}/entities/, read <files>, applicable guidance: <summary>`
- `Recall complete: searched ${EVOLVE_DIR:-.evolve}/entities/, no relevant entities found`

### Minimum Acceptable Procedure

1. List or search files under `${EVOLVE_DIR:-.evolve}/entities/` and `${EVOLVE_DIR:-.evolve}/public/` (or read the injected manifest if one is present).
2. Identify candidate entities relevant to the task.
3. Open and read those entity files.
4. Summarize what applies, or state that nothing applies.

### Failure Conditions

The skill is not complete if any of the following are true:

- You only read this `SKILL.md`
- You did not inspect `${EVOLVE_DIR:-.evolve}/entities/`
- You did not read the relevant entity files
- You proceeded without stating whether guidance was found

## How It Works

Bob has no auto-injection hook for entity retrieval. Complete the **Required Action** workflow above on every applicable task.

Entities can come from multiple sources:
- **Private entities**: Your own local entities (not shared)
- **Subscribed entities**: Entities cloned from any configured repo —
  read-scope subscriptions and write-scope publish targets both live
  under `${EVOLVE_DIR:-.evolve}/entities/subscribed/{name}/`

## Entities Storage

```text
.evolve/entities/
  guideline/
    use-context-managers-for-file-operations.md   <- private simple preference
  atomic-skill/
    extract-json-fields-in-constrained-shells.md  <- private smallest reusable capability
  skill-flow/
    save-trajectory-then-extract-and-persist.md   <- private reusable multi-step flow
  subscribed/
    memory/                                       <- write-scope clone (publishes land here)
      guideline/
        my-published-guideline.md
      atomic-skill/
        my-published-atomic-skill.md
      skill-flow/
        my-published-skill-flow.md
    alice/                                        <- read-scope clone
      guideline/
        alice-guideline.md                        <- annotated [from: alice]
      atomic-skill/
        alice-atomic-skill.md
      skill-flow/
        alice-skill-flow.md
```

The manifest output is human-readable:

```text
- `.evolve/entities/guideline/use-context-managers-for-file-operations.md` [guideline] — When processing files or managing resources
- `.evolve/entities/atomic-skill/extract-json-fields-in-constrained-shells.md` [atomic-skill] — When extracting fields from JSON in constrained shells
- `.evolve/entities/subscribed/alice/skill-flow/error-triage-and-fix-validation.md` [skill-flow] — When debugging recurring failures with a reusable validation sequence
```

Each file still uses markdown with YAML frontmatter:

```markdown
---
type: guideline
trigger: When processing files or managing resources
---

Use context managers for file operations

## Rationale

Ensures proper resource cleanup
```

For `skill-flow` entities, the frontmatter includes an `atomic_skills` field that references the component atomic skills:

```markdown
---
type: skill-flow
trigger: When creating and uploading Watson Orchestrate agents
atomic_skills: create-yaml-file-with-spec-version, activate-virtual-environment, import-agent-with-orchestrate-cli
---

To create and upload a Watson Orchestrate agent: 1) Create a YAML file... 2) Activate environment... 3) Import agent...

## Rationale

Standard workflow for Watson Orchestrate agent deployment
```

## On-Demand Expansion

When a manifest entry's trigger matches the current task, use `read_file` to load the full entity. The file body contains the entity content and an optional `## Rationale` section. Apply `guideline` entries as simple preferences, `atomic-skill` entries as focused reusable capabilities, and `skill-flow` entries as reusable multi-step compositions.

### Using Skill-Flow References

When you load a `skill-flow` entity:
1. Check the `atomic_skills` field in the frontmatter
2. If present, you can optionally load the referenced atomic skills for more detailed guidance
3. The atomic skill files are located in `.evolve/entities/atomic-skill/` with filenames matching the slugs
4. This allows you to understand both the high-level flow and the detailed implementation of each step

**Example workflow**:
```
1. Load skill-flow: "create-and-upload-watson-orchestrate-agent.md"
2. See atomic_skills: "create-yaml-file-with-spec-version, activate-virtual-environment, import-agent"
3. Optionally read: ".evolve/entities/atomic-skill/create-yaml-file-with-spec-version.md" for details
4. Apply the complete flow with detailed understanding of each step
```
