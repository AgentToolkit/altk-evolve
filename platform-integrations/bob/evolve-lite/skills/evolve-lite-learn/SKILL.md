---
name: evolve-lite:learn
description: Extracts reusable entities (guidelines, atomic skills, skill-flows) from the current conversation and saves them to the local entity library. Run explicitly with /evolve-lite:learn when you want to persist lessons from a session.
---

# Entity Generator

## Entity Types

`evolve-lite:learn` should classify reusable knowledge into exactly one of these entity types:

- `guideline`: a **declarative preference** that shapes how an agent chooses between approaches — no executable steps. Covers tool/approach choices only (e.g. "prefer CLI over MCP server"). Not for naming conventions, dependencies, or how-to instructions.
- `atomic-skill`: the smallest self-contained executable procedure that solves **one focused sub-problem**. Must include everything needed to execute it successfully: steps, naming conventions, required dependencies, concrete examples, any other context that reduces ambiguity, and a **success rubric** — explicit criteria that confirm the skill completed correctly.
- `skill-flow`: a named, recurring **ordered sequence of steps** where each step maps to an existing atomic skill, another skill-flow, or an inlined well-known operation. The order matters — skipping or reordering steps would break the outcome. Must include necessary dependencies, concrete examples, and a **success rubric** — explicit criteria that confirm the full flow completed correctly. Every step does not need its own dedicated atomic skill; trivial operations (e.g. activating a venv) can be described inline.

**Type selection decision table:**

| Question | Type |
|---|---|
| Is it a tool/approach preference with no executable steps? | `guideline` |
| Does it solve exactly one sub-problem through executable steps? | `atomic-skill` |
| Is it an ordered sequence of 2+ steps that recurs as a named unit, where order matters? | `skill-flow` |
| Is it a naming convention, dependency list, or "how to run X"? | `atomic-skill` |
| Is it a failure-derived lesson with a specific fix or command? | `atomic-skill` |
| Is it a failure-derived preference with no steps? | `guideline` |

> **Tiebreaker**: if a procedure can be described as a single coherent action ("validate this config", "import this agent"), it is an `atomic-skill`. If it only makes sense as an ordered sequence — "do A, then B, then C" — where steps are distinct named operations and order matters, it is a `skill-flow`.

## Product Organization

Entities are automatically organized by product/domain for better retrieval:

- **Product Detection**: Config-driven and automatic. The system reads `.bob/lib/evolve-lite/products.yaml` for the declared product registry (slug + match patterns), then merges in any extra product folders already present in `.evolve/entities/`. No manual directory setup is needed.
- **Folder creation**: Product folders are created automatically the first time an entity is saved to that product. You do not need to pre-create them.
- **Matching**: Config products are matched via their explicit regex patterns. Folder-only products (not in the config) fall back to whole-word slug-token matching. Highest score wins.
- **General fallback**: `general` is only assigned when nothing scores above zero. It is never preferred over a specific product.
- **Directory Structure**: Entities are stored in `{type}/{product}/{slug}.md` format.
- **Slugs**: Include the product prefix automatically (e.g., `watson-orchestrate-handle-token-expiration.md`).

**To register a new product** (before any entities exist for it): add an entry to `.bob/lib/evolve-lite/products.yaml`. Its folder will be created on first save. No code changes needed.

Currently registered products (from `.bob/lib/evolve-lite/products.yaml`):
- `watson-orchestrate`: Watson Orchestrate CLI, agents, environments
- `concert`: IBM Concert lab and audit workflows
- `langgraph`: LangGraph, LangChain, LangSmith
- `vault`: HashiCorp Vault secrets management
- `github`: GitHub operations, pull requests, workflows
- `docker`: Docker containers, images, Dockerfiles
- `kubernetes`: Kubernetes, kubectl, Helm
- `general`: Cross-product or non-specific skills (final fallback only, not in config)

## Common-Sense Filtering

The system automatically filters out skills that are too basic or well-known:

**Filtered patterns include:**
- Basic file operations (create file, read file, delete file)
- Standard Python operations (activate virtual environment, install dependencies)
- Basic git commands (git add, git commit, git push)
- Very short content (< 20 characters)
- Overly vague triggers

**What gets saved:**
- Product-specific workflows and workarounds
- Error resolutions and failure-derived solutions
- Non-obvious command sequences
- Environment-specific configurations
- Concrete solutions with code/commands

## Failure-Derived Skill Marking

The system automatically marks skills that were learned from failures:

**Automatic Detection:**
- Skills containing error indicators (error, failed, exception, retry, workaround, fix, token expired, permission denied, not found, missing) are automatically flagged
- The `derived_from_failure` field is set to "true" in the entity frontmatter
- This helps track which skills came from solving actual problems vs general best practices

**Why This Matters:**
- Failure-derived skills represent real problems that were encountered and solved
- They should be prioritized during extraction (see Step 5 and Best Practices)
- They provide concrete solutions to specific error scenarios

### Automatic Skill-Flow Decomposition

When you create a `skill-flow` entity, the system automatically:
1. **Extracts individual steps** from the flow content
2. **Creates atomic skills** for each step if they don't already exist
3. **References the atomic skills** in the skill-flow's frontmatter via `atomic_skills` field

This ensures that:
- Each step is reusable independently
- Skill-flows explicitly declare their dependencies
- The skill library remains well-structured and composable

**Example**: A skill-flow "Create and upload Watson agent" with steps like "Create YAML file", "Activate environment", "Import agent" will automatically generate 3-4 atomic skills and reference them.

## Overview

This skill analyzes the current conversation to extract actionable instructions that would help on similar tasks in the future. It **identifies errors encountered during the conversation** - tool failures, exceptions, wrong approaches, retry loops - and provides recommendations to prevent those errors from recurring. This skill should take note of the concrete solution which solved a concrete problem, not an abstract idea. When the successful resolution involves a non-trivial workaround, parser, command sequence, or fallback pipeline that could be used to avoid wasted effort, capture that solution as a reusable artifact first, then save entities that point future agents to use it.

## When To Use

Run this skill explicitly (via `/evolve-lite:learn`) when you want to persist knowledge from a session to the entity library. Good candidates include sessions where you encountered:
- tool failures
- permission issues
- missing dependencies
- retries or abandoned approaches
- reusable command sequences or scripts

Examples of artifacts that must be immediately created once proven as the successful solution include:
- an inline Python, shell, or other heredoc script
- a command assembled interactively over multiple retries
- a parser or extractor implemented ad hoc during the turn
- a fallback path triggered by missing dependencies or restricted tooling

Unless that artifact happens to be:
- code which is a trivial one-liner that future agents would not benefit from reusing
- code which embeds secrets, tokens, or user-specific sensitive data
- a guideline that would instruct the agent to invoke a skill, tool, or external command by name (e.g. "run evolve-lite:learn", "call save_trajectory") - such guidelines trigger prompt-injection detection when retrieved by the recall skill in a future session
- the user explicitly asked for a one-off result and not to persist helper code
- redundant because an equivalent local artifact on disk would be just as effective

## Workflow

### Step 0: Extract and Load the Trajectory from Bob's Logs

Instead of manually copying the conversation, automatically extract it from Bob's task logs:

1. Run the trajectory extraction script:
```bash
python3 -c "
import sys
sys.path.insert(0, '.bob/lib/evolve-lite')
from trajectory_extractor import save_trajectory_from_bob
path = save_trajectory_from_bob()
print(f'Trajectory saved: {path}')
"
```

2. Capture the exact path from the output as `saved_trajectory_path`. You will attach this exact path to each entity's `trajectory` field in Step 6.

3. Read `saved_trajectory_path` with the Read tool and analyze that saved trajectory rather than relying only on live context.

If the trajectory cannot be extracted or read, output zero entities and exit. Do not invent a trajectory path.

**Note**: This automatically reads from Bob's task logs at `~/Library/Application Support/IBM Bob/user_global_storage/id_bob_code/tasks/`, extracting the most recent task file. No manual conversation copying required!

### Step 1: Analyze the Conversation

Identify from the saved trajectory loaded in Step 0:

- **Task/Request**: What was the user asking for?
- **Steps Taken**: What reasoning, actions, and observations occurred?
- **What Worked**: Which approaches succeeded?
- **What Failed**: Which approaches did not work and why?
- **Errors Encountered**: Tool failures, exceptions, permission errors, retry loops, dead ends, and wrong initial approaches
- **Reusable Outcome**: Did the final working solution produce a reusable script, parser, command template, or workflow that would save time on a similar task?

### Step 2: Identify Errors and Root Causes

Scan the conversation for these error signals:

1. **Tool or command failures**: Non-zero exit codes, error messages, exceptions, stack traces
2. **Permission or access errors**: "Permission denied", "not found", sandbox restrictions
3. **Wrong initial approach**: First attempt abandoned in favor of a different strategy
4. **Retry loops**: Same action attempted multiple times with variations before succeeding
5. **Missing prerequisites**: Missing dependencies, packages, or configs discovered mid-task
6. **Silent failures**: Actions that appeared to succeed but produced wrong results

For each error found, document:

| | Error Example | Root Cause | Resolution | Prevention Guideline |
|---|---|---|---|---|
| 1 | `jq: command not found` | System tool unavailable in environment | created a python script to resolve the problem | Save the python script and use it in similar scenarios |
| 2 | `git push` rejected (no upstream) | Branch not tracked to remote | Added `-u origin branch` | Always set upstream when pushing a new branch |
| 3 | Tried regex parsing of HTML, got wrong results | Regex cannot handle nested tags | Switched to BeautifulSoup | Use a proper HTML parser, never regex |

### Step 3: Decide Whether To Save The Pipeline

Before writing entities, determine whether the successful approach should be saved as a reusable artifact.

Create or update a local reusable artifact when any of these are true:
- the final solution required more than a trivial one-liner
- the final solution worked around missing tools, libraries, or permissions
- the solution is likely to recur on similar tasks

Prefer one of these artifact forms:
- a small script, saved to a stable path in the workspace or plugin, such as `scripts/`, `tools/`, or another obvious helper location.
- a documented local workflow if code is not appropriate

When turning an ad hoc command or script into a reusable artifact, remove
incidental one-off inputs such as literal file names, IDs, answer values, or
temporary paths. Keep the reusable procedure that was actually exercised in the
session, and do not add capabilities that were not validated by the work.

If you create an artifact, record:
- its path
- what it does
- when future agents should use it first

### Step 4: Extract Entities

If Step 3 produced an artifact, at least one entity must explicitly point to that artifact, which is likely the only entity that needs to be produced.
Otherwise, extract 3-5 proactive entities. **Prioritize failure-derived entities first** - these get higher priority scores and are more valuable for future retrieval.

**Important**: The system will automatically:
- Detect the product/domain for each entity
- Assign priority scores based on failure indicators and specificity
- Filter out common-sense or trivial skills
- Organize entities by product in the directory structure

Choose the right granularity for each entity:

1. **Use `guideline` for declarative approach preferences**
    - zero executable steps — tells the agent *what to prefer*, not *what to do*
    - covers tool/approach choices: "prefer CLI over MCP server", "prefer BeautifulSoup over regex for HTML"
    - does NOT cover naming conventions, dependencies, or how-to instructions — those belong in `atomic-skill`
    - examples: "prefer CLI over MCP server when both are available", "avoid regex for structured data parsing"

2. **Use `atomic-skill` for any executable procedure solving one sub-problem**
    - must produce one concrete output, state change, or result through explicit steps
    - include everything needed to execute it successfully:
        - step-by-step instructions
        - naming conventions relevant to the skill
        - required dependencies (pip packages, CLI tools, env vars)
        - concrete examples showing expected inputs, commands, and outputs
        - any additional context that reduces ambiguity
        - a **success rubric**: 1–3 explicit, observable criteria that confirm the skill ran correctly (e.g. "exit code 0", "output file exists at expected path", "API returns status 200")
    - examples: importing a Watson agent YAML, extracting a JSON field without `jq`, validating a config file

3. **Use `skill-flow` for named recurring ordered sequences**
    - an ordered sequence of 2+ steps where order matters and skipping a step would break the outcome
    - each step references an existing atomic skill, another skill-flow, or an inlined description of a well-known operation
    - include necessary dependencies (tools, packages, env vars), at least one concrete example of the full flow in action, and a **success rubric**: explicit, observable criteria that confirm every step completed and the overall flow succeeded
    - do NOT create trivial atomic skills just to fill out a flow — inline well-known steps (e.g. "activate the venv", "cd into the repo") directly in the flow content
    - examples: save trajectory → review existing entities → extract entities → persist entities; authenticate → import agent YAML → verify agent status

Follow these principles:

4. **Reframe failures as proactive recommendations**
    - If an approach failed due to permissions, recommend the working permission-aware approach first
    - If a system tool was unavailable, recommend the saved artifact or fallback workflow first
    - If an approach hit environment constraints, recommend the constraint-aware approach

5. **Prioritize known working local artifacts over general advice**
    - If the successful solution produced or reused a concrete local artifact, at least one saved entity must:
    - Bad: "Use Python to parse EXIF if exiftool is missing"
    - Better: "Use `/abs/path/json_get.py` for JSON field extraction when `jq` is unavailable in minimal environments."
    - name the artifact by path
    - state exactly when to use it
    - state that it should be tried before generic tool discovery or fallback exploration
    - describe the artifact by capability, not just by the original incident

6. **Triggers should describe the broad task context that the artifact solves, not the narrow details of the original request.**
    - Bad trigger: "When jq fails"
    - Good trigger: "When extracting fields from JSON in constrained shells or stripped-down environments"
    The trigger should generalize the working solution without becoming vague.

7. **For retry loops, recommend the final working approach as the starting point**
    - Eliminate trial and error by creating a concrete local artifact out of the successful workflow or script

8. **Prefer entities that save future time**
    - A pointer to a saved working script is more valuable than a generic reminder if both are available

9. **Decompose before composing**
    - If individual steps of a flow are independently useful in other contexts, save those as `atomic-skill` entities first
    - Save a `skill-flow` only when the full ordered sequence recurs as a named unit
    - Do not create trivial atomic skills (e.g. "activate venv", "cd into directory") just to satisfy a flow — inline those steps in the flow file instead
    - Do not save a `skill-flow` for a one-off sequence better represented by a single `atomic-skill`

10. **Skill-flow content format**
    - Write skill-flow content as an ordered numbered list (e.g., "1) First step. 2) Second step.")
    - Each step should be a complete, actionable instruction that references an existing skill by name or describes the operation inline
    - The system will parse these steps to create atomic skills automatically where appropriate

### Step 5: Output Entities JSON

Output entities in this JSON format. Include a `trajectory` field on every entity, set to the `saved_trajectory_path` extracted in Step 0 — this records which session produced the entity.

```json
{
  "entities": [
    {
      "name": "Short verb-noun label used as the filename slug",
      "content": "Proactive entity stating what TO DO",
      "rationale": "Why this approach works better",
      "type": "guideline",
      "trigger": "Situational context when this applies",
      "trajectory": ".evolve/trajectories/claude-transcript_<session-id>.jsonl",
      "success_rubric": "For atomic-skill and skill-flow only: 1–3 observable criteria confirming successful execution",
      "requirements": "pyyaml>=6.0\nrequests",
      "imports": "import yaml\nimport requests",
      "dependencies": "watson-orchestrate-ensure-authentication",
      "documentation": "https://docs.example.com/api"
    }
  ]
}
```

Allowed type values:
- guideline
- atomic-skill
- skill-flow

#### `name` field — required for all entities

The `name` field controls the filename slug and must follow these rules:

- **Format**: short verb-noun phrase, 2–5 words, lowercase, no punctuation
- **Do NOT start with**: "when", "if", "how to", the product name, or any trigger-style phrasing
- **Do NOT include**: commands, flags, file paths, or full sentences
- **Captures**: the capability, not the situation

| Bad `name` | Good `name` |
|---|---|
| `when-a-watson-orchestrate-agent-requires-multiple-tools` | `import-multi-tool-python-file` |
| `watson-orchestrate-import-agent` | `import-agent-yaml` |
| `handle-token-expiration-by-piping-api-key-to-orchestrate-env-activate` | `reauth-expired-token` |
| `general-in-evolve-lite-skill-setup-steps-only-gitignore` | `gitignore-evolve-artifacts` |

The product prefix is added automatically from the folder — do not include it in `name`.

#### `trigger` field — required for all entities

The `trigger` describes **the situation the user is in** — not what the skill does, not the solution, not a command.

Triggers are matched by keyword overlap against user messages. A trigger that omits the words a user would naturally type will lose to other entities even if the skill is correct.

**Rules:**

1. **Write from the user's perspective, not the author's.** The user does not know the solution yet. They describe a symptom, error, or task. Use the words they would use.

2. **Include the specific error message, flag, or symptom** that distinguishes this scenario. Vague triggers match everything and win nothing.

3. **Include the task keywords** — the nouns and verbs the user types when describing what they are trying to do ("import", "token expiration", "not found", "spec_version", "hyphens").

4. **Do NOT describe what the skill does.** Triggers like "When activating the venv" or "When uploading an agent file" describe the solution. The user doesn't know they need to activate the venv — they know the CLI is broken.

5. **When two skills could match the same generic symptom** (e.g. "import failing"), make the trigger specific enough to distinguish them — include the exact error text, flag name, or field name involved.

| Anti-pattern | Why it failed | Fixed trigger |
|---|---|---|
| `When uploading an agent definition file` | User typed "import" not "upload"; no error keywords | `When importing an agent YAML file into Watson Orchestrate using the orchestrate agents import CLI command` |
| `When a CLI session has expired mid-workflow` | User typed "token expiration error" — trigger has no overlap | `When the Watson Orchestrate CLI reports a token expiration error or expired token mid-session` |
| `When a Python venv needs to be activated` | User asked "what do I need before running CLI commands" — no symptom words | `When the orchestrate command is not found after opening a new terminal, or when preparing to run CLI commands and the venv needs activating` |
| `When authoring an agent definition file from scratch` | User asked about specific fields (spec_version, parameters) — trigger has no field names | `When creating a Watson Orchestrate agent YAML with required fields like spec_version, name, description, instructions, model, parameters, and tools` |
| `When configuring the CLI environment for the first time` | User said "env activate…environment doesn't exist" or "combine URL and auth" — trigger has no command keywords | `When setting up the Watson Orchestrate CLI for the first time by registering the URL with env add and authenticating with env activate` |
| `When naming an agent in a YAML file` | Too generic — lost to other import-related skills when user mentioned "import failing with validation error" | `When naming an agent in a YAML file, including rules about hyphens, underscores, and names starting with a number, or when import fails with a name validation error` |

#### Optional metadata sections

These fields are **optional** — only include them when they add real value. Each becomes a named markdown section in the saved entity file.

| Field | Applies to | When to use | Format |
|---|---|---|---|
| `success_rubric` | `atomic-skill`, `skill-flow` | Always include for these types — omit only for `guideline` | 1–3 bullet points, each an observable pass/fail criterion (e.g. "exit code 0", "output file exists at expected path", "no error lines in stdout") |
| `requirements` | all | The skill requires specific pip packages or CLI tools to work | One entry per line. Pip packages may include version specifiers (`pyyaml>=6.0`). CLI tools use `cli: <name>` prefix (e.g. `cli: orchestrate`). |
| `imports` | all | The skill involves Python code and specific imports are non-obvious | Full import statements, one per line (`import subprocess`, `from pathlib import Path`) |
| `dependencies` | all | This skill must be used after another entity (outside the `atomic_skills` composition graph) | Comma-separated entity slugs |
| `documentation` | all | There is a canonical external reference worth linking | One URL per line |

**Rules:**
- `name` and `trigger` are **required** for all entity types.
- `success_rubric` is **required** for `atomic-skill` and `skill-flow` — do not omit it for these types.
- Anything listed in `requirements` or `imports` **must** be mentioned or used somewhere in the `content` body — the quality gate will flag undeclared references that never appear.
- Leave other fields out entirely if the skill is self-contained and needs no external context.

#### Version field

Every entity carries a `version` integer in its frontmatter (set automatically to `1` on first publish, then incremented on each re-publish by the publish script).

When you **update** an existing entity (improve trigger wording, extend content, add a new section), include a `## Changelog` section in the JSON so the change is recorded:

```json
{
  "entities": [
    {
      "content": "Updated guidance here…",
      "trigger": "Improved trigger wording…",
      "type": "atomic-skill",
      "changelog": "- v2: Expanded content to cover edge case X\n- v1: Initial version"
    }
  ]
}
```

The `## Changelog` format is flexible — one bullet per version is sufficient:

```
- v3: Added ## Requirements section with pyyaml dependency
- v2: Fixed stutter in trigger; added ## Imports
- v1: Initial publish
```

The quality gate will warn (non-blocking) when `version > 1` and the `## Changelog` has fewer entries than the stated version number.

**Note**: The save script will automatically:
- Detect and set the `product` field based on content analysis
- Set `derived_from_failure` to "true" for error-related skills
- Filter out common-sense or trivial skills
- Decompose skill-flows into atomic skills with proper references
- Organize files in `{type}/{product}/{slug}.md` structure

### Step 6: Save Entities

After generating the entities JSON, save them using the helper script:

#### Method 1: Direct Pipe (Recommended)

```bash
echo '<your-json-output>' | python3 .bob/skills/evolve-lite-learn/scripts/save_entities.py
```

#### Method 2: From File

```bash
cat entities.json | python3 .bob/skills/evolve-lite-learn/scripts/save_entities.py
```

#### Method 3: Interactive

```bash
python3 .bob/skills/evolve-lite-learn/scripts/save_entities.py
```

The script will:
- Find or create the entities directory at `.evolve/entities/`
- **Detect product/domain** for each entity automatically
- **Mark failure-derived skills** with `derived_from_failure: true`
- **Filter out common-sense skills** that are too basic or well-known
- **For skill-flows**: Automatically decompose into atomic skills and create them if needed
- Write each entity as a markdown file in `{type}/{product}/` subdirectories
- Display confirmation with counts of added, filtered, and auto-created entities

### Step 7: Generate Tests from Success Rubrics

For every `atomic-skill` or `skill-flow` entity saved in Step 6, generate test cases grounded in that entity's `success_rubric`.

1. Read each newly written entity file and extract its `success_rubric` section.
2. For each entity, produce the following test cases:
    - **1 happy-path test**: executes the skill under normal conditions; `validation_criteria` populated **directly from the rubric criteria** — do not invent generic criteria
    - **1–3 edge case tests**: each exercises a realistic boundary or failure condition (e.g. missing dependency, malformed input, already-existing output, partial environment). Each edge case must still reference the rubric criteria and state which criterion is expected to fail or behave differently.
    - `scenario` describes the specific condition being tested
    - `input_context` reflects realistic preconditions including anything unusual for that edge case
3. Save tests to `.evolve/tests/pseudo_conversations/` as `{entity-slug}.json`, following the existing test file format.
4. If a test file already exists for an entity slug, **append new test cases** — never overwrite or delete existing ones.
5. Skip `guideline` entities — they have no rubric and no executable outcome to test.

### Step 7a: Run Test Gate

After generating tests in Step 7, run the quality gate to verify the newly saved skills pass at a minimum 80% rate across both the content-evaluation and recall suites.

```bash
python3 .bob/skills/evolve-lite-test/scripts/generate_skill_tests.py --all
python3 .bob/skills/evolve-lite-test/scripts/check_tests.py --threshold 0.8 --verbose
```

The gate report is written to `.evolve/tests/evaluation/gate_report.json`.

**If the gate passes** (exit 0), continue to Step 8.

**If the gate fails** (exit 1), read the `❌` lines and apply the fix that matches the failure type below. After each fix, re-run `check_tests.py`. Do not continue to Step 8 until the gate exits 0.

#### Fixing gate failures

Each `❌` line from the content evaluation (`run_skill_evaluation.py`) or recall test (`run_recall_tests.py`) falls into one of the following categories. Open the failing entity file and apply the matching fix.

---

**Content evaluation failure** — `score < 0.5` or `violated` terms found

The output shows `missed=[...]` — these are backtick-command terms that appear in the fixture's `must_include` list but are absent from the skill content.

```
❌ my-skill   score=0.33   matched=1/3   missed=['orchestrate agents import', '--kind']
```

*Fix*: Open the entity file. For each missed term, either:
- Add the missing command or flag to the content body (preferred — the skill genuinely needs it).
- If the term was extracted incorrectly and the skill is correct without it, regenerate the fixture: `python3 .bob/skills/evolve-lite-test/scripts/generate_skill_tests.py <path-to-entity.md>`

If `violated=[...]` appears, the skill content contains something it explicitly must NOT contain. Remove or rephrase that content.

---

**Recall failure** — skill not in top-3 for its own trigger scenario

The output shows `rank=N` where N > 3, or `rank=not_found`, meaning another skill outranked this one.

```
❌ my-skill   rank=7   @1=✗ @3=✗ @5=✓   score=2
     top5: ['other-skill', 'another-skill', ...]
```

*Fix*: The `trigger` field uses words the user would not type, or is too generic. Rewrite it using the rules from Step 5 (`trigger` field):
- Use the *symptom* or *error message* the user would describe, not the solution.
- Include the specific command, flag, or error text that distinguishes this skill.
- Check the `top5` list — if the skills ranked above this one share keywords with your trigger, make this trigger more specific by adding the distinguishing detail.

Example rewrites:

| Bad trigger | Why it lost | Fix |
|---|---|---|
| `When uploading an agent file` | "upload" not in user vocabulary; no error terms | `When importing an agent YAML using orchestrate agents import and the command fails` |
| `When the CLI session ends` | Vague; lost to other CLI-related skills | `When the Watson Orchestrate CLI reports token expiration or expired token mid-session` |
| `When setting up the environment` | Too generic; matches everything | `When running orchestrate env add and env activate for the first time to register a URL and authenticate` |

After rewriting the trigger, regenerate the fixture so the test question reflects the new wording:
```bash
python3 .bob/skills/evolve-lite-test/scripts/generate_skill_tests.py <path-to-entity.md>
```
Then re-run the gate.

---

**Both failures on the same skill**

Fix the content issue first (add missing commands), then fix the trigger. A skill whose content is incomplete will also tend to have a weak trigger because the two are related — the content should contain the distinguishing terms that make the trigger strong.

### Step 8: Deduplicate the Entity Library

After saving, compare the newly written entities against the full library to identify overlap and decide what to merge or delete.

1. **Enumerate all entities** using the **Glob tool**: `.evolve/entities/**/*.md`. Use the **Read tool** to skim each file's `trigger`, `type`, and first paragraph of `content`.

    **Do NOT use `cat`, `head`, `find`, a `for` loop, or an inline `python3 -c` script for this** — each shell invocation triggers a permission prompt. Use Glob + Read only.

2. **Identify candidates for merging or deletion** — look for pairs or groups that:
    - Have the same or very similar `trigger`
    - Cover the same sub-problem from slightly different angles
    - Are the same `type` and would be stronger as one combined entity

3. **Decide for each candidate group**:

    | Situation | Action |
    |---|---|
    | One entity is strictly a subset of another | Delete the weaker one |
    | Both cover the same ground with complementary detail | Merge into one entity with the richer content, delete the other |
    | They cover genuinely different sub-problems or triggers | Keep both |
    | One is higher quality (better examples, rubric, dependencies) | Keep it, delete the weaker one |

4. **To merge**: write the combined content into the surviving file using the edit tools, then delete the redundant file.

    When choosing which entity to keep as the survivor, **prefer the one that already has tests** — check `.evolve/tests/pseudo_conversations/` for a file matching the entity slug (e.g. `watson-orchestrate-import-agent.json`). Tests must never be deleted or modified during dedup — only new tests may be added.

5. **To delete**: remove the entity file only. **Do not delete or modify any files under `.evolve/tests/`** — tests are append-only. If the surviving entity has no tests but the deleted one did, copy the deleted entity's test files to match the survivor's slug before removing the original.

6. If no duplicates or near-duplicates are found, skip to the end — this step has no mandatory output.

## Best Practices
1. **ALWAYS prioritize failure-derived entities first** - if you found errors, create entities for those BEFORE anything else.
2. One distinct error should normally produce one prevention entity.
3. Keep entities specific and actionable - avoid common-sense advice.
4. Include rationale so the future agent understands why the guidance matters.
5. Use situational triggers instead of failure-based triggers.
6. **Respect the 3-5 entity limit** - if you have 4 failures, create 4 failure entities and maybe 1 other. Don't create 5 non-failure entities when failures exist.
7. If more than five distinct errors appear, merge entities with the same root cause or fix, then rank the rest by severity, frequency, user impact, and recency before dropping the weakest ones.
8. **Focus on product-specific solutions** - general advice is less valuable than concrete product workflows.
9. **Include concrete details** - file paths, commands, code snippets make skills more actionable.
10. **Let the system handle organization** - don't worry about product detection, the system does this automatically.
