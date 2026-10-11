---
name: evolve-lite:publish
description: Publish private guidelines, atomic skills, or skill flows to a configured write-scope repo.
---

# Publish Entities

## Overview

Publish one or more private entities from `.evolve/entities/guideline/`, `.evolve/entities/atomic-skill/`, or `.evolve/entities/skill-flow/`
into a configured **write-scope** repo. Each entity is stamped with
`visibility: public`, `owner`, `published_at`, and `source`, moved into
the local clone of the write repo, and committed / pushed to the remote.

The same local clone is also what `evolve-lite:sync` pulls from — so you
and anyone else publishing to the same repo stay in sync.

## Workflow

### Step 1: Require a write-scope repo

Read `evolve.config.yaml`. If no entry has `scope: write`, tell the user:

> "You need at least one write-scope repo to publish to. Run evolve-lite:subscribe with --scope write to set one up, then come back."

Then stop.

If `identity.user` is missing, ask for it and add it to the config.

### Step 2: First-time setup

Ensure `.evolve/entities/subscribed/` is gitignored at the project root (the subscribed clones are managed by evolve-lite and should not be committed). Do **not** gitignore `.evolve/` or `.bob/` — those directories must remain tracked.

```bash
grep -qxF '.evolve/entities/subscribed/' .gitignore 2>/dev/null || echo '.evolve/entities/subscribed/' >> .gitignore
```

### Step 3: Pick the target write-scope repo

Filter `repos:` to entries with `scope: write` (Step 1 already aborted if
there were zero, so at least one exists here).

- Exactly one entry → use it as default.
- Multiple entries → show a numbered list with `notes` and ask which to publish to.

Let `{repo}` be the chosen repo name and `{branch}` its configured branch (default `main`).

### Step 4: List and select entities

List files in `.evolve/entities/guideline/`, `.evolve/entities/atomic-skill/`, and `.evolve/entities/skill-flow/`, then ask the user which to publish.

### Step 4a: Test gate (optional)

The quality gate is **off by default**. Only run it when the user explicitly requests it (e.g. "publish with quality gate", "run the gate before publishing", "include quality check").

If the user did **not** ask for the gate, skip this step entirely and proceed to Step 5.

If the user **did** ask for the gate, run:

```bash
python3 .bob/skills/evolve-lite-test/scripts/check_tests.py --threshold 0.8 --verbose
```

The gate report is at `.evolve/tests/evaluation/gate_report.json`.

**If the gate passes** (exit 0), continue to Step 5.

**If the gate fails** (exit 1), tell the user publishing is blocked and work through the `❌` lines to fix each failure before retrying. Do not proceed to Step 5 until the gate exits 0.

#### Fixing gate failures

Read the `❌` lines from the gate output. Each failure is one of two types:

---

**Content evaluation failure** — `score < 0.5` or `violated` terms

The `missed=[...]` list shows command terms that are in the test fixture but absent from the skill content.

```
❌ my-skill   score=0.33   matched=1/3   missed=['orchestrate agents import', '--kind']
```

Open the entity file at `.evolve/entities/{type}/my-skill.md`.

- **Missing terms**: Add the missing command, flag, or tool name to the skill content body. If the term is a genuine part of what the skill prescribes, the content is incomplete — extend it.
- **Incorrect fixture**: If the term was over-extracted and the skill is actually correct without it, regenerate the fixture to match the current content:
  ```bash
  python3 .bob/skills/evolve-lite-test/scripts/generate_skill_tests.py .evolve/entities/{type}/my-skill.md
  ```
- **Violated terms**: If `violated=[...]` is non-empty, remove the offending phrase from the skill content or adjust the `must_not_include` list in the fixture.

---

**Recall failure** — skill not surfacing in top-3 for its own scenario

```
❌ my-skill   rank=6   @1=✗ @3=✗ @5=✓   score=2
     matched_terms: ['cli']
     top5: ['other-skill', 'third-skill', ...]
```

The `trigger` field is too vague or uses the wrong vocabulary. Rewrite it so it contains the words a user would actually type when describing the problem:

- Use the *symptom*, *error message*, or *task description* — not the solution.
- Include the specific command, flag, product name, or error text that distinguishes this skill from the others in `top5`.
- Look at `matched_terms`: if only short, generic words matched (e.g. `['cli']`), the trigger needs more specific keywords.

After editing the trigger, regenerate the fixture and re-run the gate:
```bash
python3 .bob/skills/evolve-lite-test/scripts/generate_skill_tests.py .evolve/entities/{type}/my-skill.md
python3 .bob/skills/evolve-lite-test/scripts/check_tests.py --threshold 0.8 --verbose
```

Repeat until both suites show ✅ and the gate exits 0.

### Step 5: Run publish script

For each selected file, run:

```bash
python3 .bob/skills/evolve-lite-publish/scripts/publish.py --entity "{filename}" --repo "{repo}" --user "{identity.user}"
```

### Step 6: Commit and push to a new branch

If the user specified a branch name (e.g. "publish to a new branch called `{new_branch}`"), use that. Otherwise derive one as `{identity.user}-{YYYY-MM-DD}`.

Let `{publish_branch}` be that branch name.

Build `{names}` as a comma-joined list of selected filenames, and
`{entity_paths}` as a space-joined list of the corresponding typed paths inside the clone (for example `.evolve/entities/guideline/{product}/{filename}`, `.evolve/entities/atomic-skill/{product}/{filename}`, or `.evolve/entities/skill-flow/{product}/{filename}`) for the files the publish script just wrote.

If the publish script wrote a `.gitignore` into the clone root (it will on first publish), include that path too so it lands on the remote and protects every future contributor's clone.

```bash
git -C ".evolve/entities/subscribed/{repo}" checkout -b "{publish_branch}"
git -C ".evolve/entities/subscribed/{repo}" add -- {entity_paths} .gitignore
git -C ".evolve/entities/subscribed/{repo}" commit -m "[evolve] publish: {names}"
git -C ".evolve/entities/subscribed/{repo}" push origin "{publish_branch}"
```

> **Never use `git add .` or `git add -A` here.** Only the entity files and `.gitignore` are staged explicitly. The `.gitignore` in the clone blocks `.venv/`, `.env`, `.vscode/`, `__pycache__/`, secrets, and all other project noise from ever being staged — but explicit path staging is the final guarantee.

On push success, tell the user the branch name and continue to Step 7.

### Step 6a: Recover from push rejection

If the push fails and stderr mentions `rejected` / `non-fast-forward` / `fetch first`, the branch already exists on the remote. Pull it in and retry:

```bash
git -C ".evolve/entities/subscribed/{repo}" fetch origin "{publish_branch}"
git -C ".evolve/entities/subscribed/{repo}" rebase "origin/{publish_branch}"
git -C ".evolve/entities/subscribed/{repo}" push origin "{publish_branch}"
```

If the push fails for any other reason (auth, network, missing remote ref), surface git's error and stop.

### Step 7: Confirm

Tell the user what was published and to which repo.

## Notes

- Published entities are **copied** from their private typed directories under `.evolve/entities/`
  into the matching typed directory in the write-scope clone at `.evolve/entities/subscribed/{repo}/`,
  with `visibility: public`, `owner: {user}`, `published_at`, and `source`
  stamped in frontmatter
- The original private entity is kept intact after publication
- All publish actions are logged to `.evolve/audit.log`
