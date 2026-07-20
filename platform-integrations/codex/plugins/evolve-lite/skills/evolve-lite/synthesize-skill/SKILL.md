---
name: synthesize-skill
description: Convert a saved trajectory into a reusable agent skill (SKILL.md + supporting scripts) that future agents can invoke to skip rediscovered work. Use when a session captured a non-trivial workflow worth promoting from a free-text guideline to an executable skill.
---

# Skill Synthesizer

## Overview

This skill reads a saved trajectory and produces a **reusable agent skill** — a `SKILL.md` plus any supporting scripts — that captures the *successful* workflow the session discovered. The output goes to `.evolve/skills/<skill-name>/` (canonical, evolve-managed). Future sessions on the same project can then invoke the skill directly instead of re-deriving the workflow.

This is the **executable** counterpart to the `learn` skill's free-text guidelines: `learn` writes Markdown the next agent has to *read and decide what to do*; `synthesize-skill` writes a skill the next agent can simply *call*.

## When To Use

Use this skill when a trajectory captured:

- A **non-trivial workflow** that succeeded after trial-and-error (the eventual happy path is worth promoting from free-text advice to an invocable artifact).
- A **reusable script or command sequence** the model wrote during the session — particularly one the agent had to reconstruct over multiple attempts.
- An environment-specific workaround (a missing system tool, a permissions wrinkle, a fallback pipeline) that future sessions in the same project will hit.

Skip this skill — and let `learn` cover the case with a guideline alone — when:

- The successful path was a single trivial command.
- The workflow embeds secrets, tokens, or one-off user inputs that can't be safely generalized.
- A skill with the same trigger already exists in `.evolve/skills/` (use `learn`'s guideline path to refine the existing skill instead of creating a duplicate).

## Workflow

### Step 0: Locate the Trajectory

This skill runs in a forked context. **You cannot see the parent conversation directly** — read the trajectory the parent passed in via `args` or via the `Run evolve-lite:synthesize-skill on <path>` instruction.

The trajectory path is either:

- supplied directly as `args` to the skill invocation, or
- stated in the parent's invocation message as `The saved trajectory path is: <path>` — take everything after the colon, strip surrounding whitespace and quotes.

If neither is present, scan `.evolve/trajectories/` for the most recently modified `claude-transcript_<session-id>.jsonl` and use that. If `.evolve/trajectories/` does not exist or is empty, output zero artifacts and exit — do not invent a trajectory.

**Read the trajectory with the `Read` tool — do NOT shell out.** The transcript is JSONL: one JSON object per line. Filter for `"type": "assistant"` and `"type": "human"` records and reconstruct the flow from `message.content`.

### Step 1: Identify the Successful Workflow

Walk the trajectory and locate the **final, working** tool sequence — the one that actually produced the answer. Distinguish it from the trial-and-error leading up to it.

Capture:

- **What the user asked** (the original prompt).
- **What ultimately worked** — the exact tool calls, scripts, or command sequences that produced the answer. Quote them verbatim from the trajectory.
- **What didn't work** — the dead-ends. You will use these to write a `Triggers` section so the future agent knows when to reach for this skill *instead of* the failing approaches.
- **Environment assumptions** — what was missing or had to be installed (e.g. "no exiftool, pip install Pillow needed").

If no clearly successful workflow is in the trajectory (the session ended without reaching an answer, or the answer came from a single trivial call), output zero artifacts and exit.

### Step 2: Decide a Skill Name and Trigger

The skill **name** must be:

- kebab-case, action-oriented (`extract-exif-metadata`, `parse-cloudwatch-logs`, `restart-stuck-deploy`)
- specific enough that a future agent reading just the name can guess what it does
- not a duplicate of any existing entry under `.evolve/skills/`

The skill **description** (one line, in the SKILL.md frontmatter) should describe the *task* the skill solves, not the trajectory it came from. Bad: "Solves the focal-length question from session abc123." Good: "Extract EXIF metadata (focal length, GPS, lens, timestamps) from JPEG/HEIC images using Pillow when system EXIF tools are unavailable."

The **trigger** (in the SKILL.md body, under `## When To Use`) should describe the broad task context, not the narrow original request — same rule as the `learn` skill's guidelines.

Before continuing, list `.evolve/skills/` (use the `Glob` tool, not `find` / `ls`) and confirm your chosen name does not collide with an existing skill.

### Step 3: Draft the SKILL.md

Author a SKILL.md with this exact frontmatter shape — the validator in Step 5 will reject it otherwise:

```yaml
---
name: <kebab-case-name>
description: <one-line task description>
---

# <Title Case Name>

## Overview
<1–2 sentences: what the skill does and when to use it>

## When To Use
- <trigger 1>
- <trigger 2>

## Workflow
<step-by-step instructions for the agent>
```

Notes:

- `context: fork` is **omitted** for synthesized skills. They run in the parent context so they can write files into the workspace and report back.
- Do NOT inline the full successful script into the SKILL.md if it's more than ~10 lines — put it in a sibling `scripts/` file (Step 4) and reference it from the SKILL.md.
- The Workflow section should describe what to do *to solve the task*, not retell the original session. A future agent reading this should be able to act without ever seeing the trajectory.

### Step 4: Emit Supporting Scripts

If the successful workflow used a non-trivial script (more than a one-liner), write it as a sibling file under `scripts/` of your draft skill directory. Use the **already-validated code from the trajectory** — do not invent variations. Strip incidental one-off inputs (literal file names, IDs, hard-coded outputs) and replace with arguments or stdin where appropriate.

Common shape:

```text
.evolve/skills/<name>/
├── SKILL.md
└── scripts/
    └── <action>.py     # callable as `python3 scripts/<action>.py <args>`
```

If the workflow was a sequence of shell commands rather than a script, encode it as an executable shell script (`scripts/<action>.sh`) so future agents can invoke it as a single unit instead of replaying each command.

If no non-trivial script is needed (the workflow is a sequence of standard tool calls), skip this step — the SKILL.md alone is the skill.

### Step 5: Finalize

Place your draft files (SKILL.md and any scripts) under a temporary directory inside the workspace, e.g. `/tmp/synthesized-<name>/`, then call:

```bash
python3 "$(git rev-parse --show-toplevel 2>/dev/null || pwd)/plugins/evolve-lite/skills/evolve-lite/synthesize-skill/scripts/synthesize.py" finalize --src /tmp/synthesized-<name>/ --name <kebab-case-name> --trajectory <saved_trajectory_path>
```

The script will:

- Validate the SKILL.md frontmatter (`name` and `description` required; `name` must match `--name`).
- Reject the skill if a same-named skill already exists in `.evolve/skills/` (overwriting requires `--force`).
- Copy the directory into `.evolve/skills/<name>/` (canonical).
- Append a `synthesize_skill` event to `.evolve/audit.log` recording the new skill, the source trajectory, and the timestamp.
- Print the destination path(s).

If the validator rejects the draft, fix the SKILL.md and retry — do not edit files under `.evolve/skills/` directly.

### Step 6: Confirm

After the script returns, list the destination directories with the `Glob` tool to confirm the files landed. Output a short summary:

- The skill name and description.
- The destination paths.
- A one-line note on what future sessions should now be able to do that they couldn't before.

## Best Practices

1. **One skill per workflow.** If the trajectory contains two unrelated successful workflows, run synthesis twice with different names — do not pack them into one skill.
2. **Cite the trajectory.** Include the `--trajectory` flag so the audit log records provenance; future maintainers can trace the skill back to the session that produced it.
3. **Don't promote one-shots.** A skill is worth synthesizing only if the trigger is plausibly recurring. If the trajectory looks like a one-off, prefer the `learn` skill's guideline path instead.
4. **Don't paraphrase failure.** The skill describes what *worked*. If you find yourself writing "this skill avoids the problem where exiftool isn't installed," restate it as "uses Pillow to extract EXIF; works in environments without system EXIF tools." Triggers describe *when*, not *what failed*.
5. **Keep scripts minimal.** Strip incidental log lines, debug prints, and validation that wasn't actually exercised in the trajectory. If a feature wasn't validated, leave it out.
