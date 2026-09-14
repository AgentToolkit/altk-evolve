---
description: Adversarial, reproduce-by-execution review of a GitHub PR (fan-out sub-agents, verify, draft review)
argument-hint: <PR#> [repo] [--post]
allowed-tools: Bash, Read, Grep, Glob, Agent, Write
---

Run an adversarial code review of pull request **#$1**, following `docs/adversarial-review.md`.

Method — evidence over opinion; every finding carries a reproduction or it doesn't ship. Do NOT report style nits.

## Steps

1. **Fetch & size the PR.**
   - `gh pr view $1 --repo <REPO> --json title,author,state,mergeable,mergeStateStatus,body,additions,deletions,changedFiles,headRefOid,commits` (default REPO to this repo's upstream/origin; a second arg to this command overrides it).
   - `gh pr view $1 --repo <REPO> --json files --jq '.files[]|"\(.additions)+ \(.deletions)- \(.path)"' | sort -rn` to see the shape.
   - If the PR body claims specific bugs/fixes, note them — they become verification targets. If this is a **re-review**, fetch your prior review (`gh api repos/<REPO>/pulls/$1/reviews`) so each old finding gets a fixed / still-open / regressed verdict.

2. **Isolate in a worktree.** Fetch the head into a temp branch and add a worktree under the scratchpad dir; never touch the user's working tree. Clean it up at the end (`git worktree remove --force`, delete the temp branch).

3. **Baseline BEFORE judging.** Install extras if the PR needs them, then run the project's lint / type / test commands and record results, so PR-caused breakage is distinguishable from environmental noise. Note the base SHA (`upstream/main`) for `git diff main...HEAD`.

4. **Fan out independent skeptics.** Launch 2–3 sub-agents **in parallel** (one message, multiple Agent calls), each scoped to ONE risk surface (e.g. core algorithm / integration & breaking changes / plugins-config-tests-packaging). Give each the sub-agent prompt template from `docs/adversarial-review.md`, filled in for its surface. They must reproduce findings by execution and not duplicate each other.

5. **Verify headline claims yourself.** Re-run each agent's most severe finding with your own repro. Only keep what survives. Watch for claims that are wrong *in the author's favor* too (mis-stated breaking changes, "deleted" tests that were merely moved).

6. **Synthesize & show the user.** Rank blocker → high → medium → low; separate verified from hypothesized; **credit what's genuinely correct** (a status table is ideal on re-reviews). Present the results and STOP — do not post unless `--post` was passed or the user asks.

7. **Post (only when asked).** Build a review with a summary body + inline comments anchored to `file:line` at the head SHA (inline comments must land on diff lines; otherwise put them in the body). Pick the verdict deliberately (`REQUEST_CHANGES` for a real correctness drop or several mediums; else `COMMENT`/`APPROVE`). Submit as the user's own GitHub account with **no AI attribution**. Verify every inline comment anchored, then report the review URL.

Extra arguments: `$ARGUMENTS` (e.g. a repo override, or `--post` to post without a second confirmation).
