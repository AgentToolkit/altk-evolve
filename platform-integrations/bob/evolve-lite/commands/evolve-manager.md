---
description: Merge GitHub fork entity libraries into the main evolve repo with regression protection.
skip_learn: true
---

# Evolve Manager — Merge Forks Workflow

Follow these steps **in strict order**. Do not skip or reorder steps.

---

## STEP 1 — Read the skill

Read `.bob/skills/evolve-manager/SKILL.md` before any tool use (once per conversation).

---

## STEP 2 — Establish main repo identity

The main repo for this workspace is **`ce-artemis-2026/evobob-test`** (IBM GHE: `github.ibm.com`).
- Use `--main-repo ce-artemis-2026/evobob-test` on every script invocation.
- GitHub token: use the `GITHUB_TOKEN` env var (or `--github-token`).

---

## STEP 3 — Discover and stage forks

List forks from the GitHub API:
```
GET /repos/ce-artemis-2026/evobob-test/forks
```
For each fork that is **not already staged** under `.evolve/tmp/fork-staging/<fork-name>/`:
```bash
git clone --depth=1 --filter=blob:none --sparse \
  <fork_ssh_or_https_url> \
  .evolve/tmp/fork-staging/<fork-name>
cd .evolve/tmp/fork-staging/<fork-name> && \
  git sparse-checkout set .evolve/entities
```

For forks that **are already staged**, pull latest changes to avoid stale data:
```bash
cd .evolve/tmp/fork-staging/<fork-name>
git fetch origin && git reset --hard origin/HEAD
```

Collect the list of all staged fork directories with at least one entity file under
`.evolve/tmp/fork-staging/<fork-name>/.evolve/entities/`.

---

## STEP 4 — Run the merge script (dry run first)

Always run a dry run first to preview decisions:
```bash
python3 .bob/skills/evolve-manager/scripts/merge_forks.py \
  --fork-dirs .evolve/tmp/fork-staging/<fork1> .evolve/tmp/fork-staging/<fork2> ... \
  --main-repo ce-artemis-2026/evobob-test \
  --dry-run
```
Show the dry-run output to the user before proceeding.

---

## STEP 5 — Confirm with user and run live merge

After the user reviews the dry run output, run the live merge:
```bash
python3 .bob/skills/evolve-manager/scripts/merge_forks.py \
  --fork-dirs .evolve/tmp/fork-staging/<fork1> ... \
  --main-repo ce-artemis-2026/evobob-test
```

---

## STEP 6 — Handle exit codes

| Exit | Meaning | Action |
|------|---------|--------|
| `0` | Success | Tell the user entities are live in `.evolve/entities/`. Show the merge report summary. |
| `1` | Hard failure | Show the full error output. STOP. Do not retry until the user fixes the error. |
| `2` | Threshold breach | Show the diff summary. Ask: "Main-repo test pass rate dropped. Keep the merge or roll back?" |

**If keep (exit 2):** re-run with `--force-commit`.
**If rollback:**
```bash
rm -rf .evolve/entities/
cp -r .evolve/tmp/pre-merge-backup/ .evolve/entities/
```

---

## RULES

- Never manually edit entity files. The script owns all writes to `.evolve/entities/`.
- Always run the dry run (Step 4) before the live merge (Step 5).
- Never re-run after exit 1 without the user fixing the reported error.
- Always show full script output before acting on the exit code.
- Fork entities are never tested against the regression threshold — only original main-repo entities are.
