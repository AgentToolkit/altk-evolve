---
description: Adversarial, reproduce-by-execution review of a GitHub PR (fan-out sub-agents, verify, draft review)
argument-hint: <PR#> [owner/repo] [--post] [--sandbox|--no-sandbox]
allowed-tools: Bash, Read, Grep, Glob, Agent, Write
---

Run an adversarial code review of pull request **#$1**, following `docs/adversarial-review.md`.

Method — evidence over opinion; every finding carries a reproduction or it doesn't ship. Do NOT report style nits.

## Arguments

Parse `$ARGUMENTS` positionally and by flag, independently of each other:

- **`$1`** — the PR number (required).
- **Flags** — any argument starting with `--`, in any position: `--post` (post without a second confirmation), `--sandbox` / `--no-sandbox` (force or skip container isolation; see step 3).
- **Repo override** — the first *non-flag* argument after `$1`, if there is one. Do not treat `$2` as the repo without checking it first: `/adversarial-review 289 --post` has no repo override. Default `<REPO>` to this repo's `upstream` remote, falling back to `origin`.

## Steps

1. **Fetch & size the PR.**
   - `gh pr view $1 --repo <REPO> --json title,author,authorAssociation,state,isCrossRepository,headRepositoryOwner,headRefName,headRefOid,baseRefName,baseRefOid,mergeable,mergeStateStatus,body,additions,deletions,changedFiles,commits`
   - `gh pr view $1 --repo <REPO> --json files --jq '.files[]|"\(.additions)+ \(.deletions)- \(.path)"' | sort -rn` to see the shape.
   - Record `baseRefOid` and `headRefOid` — **every diff and baseline below uses those two SHAs**, never a hard-coded `main`. A PR may target a release or feature branch; diffing against the wrong base misattributes pre-existing defects to the PR, or hides its changes entirely.
   - If the PR body claims specific bugs/fixes, note them — they become verification targets.

2. **If this is a re-review, load every prior finding.** The status table in step 7 is per finding, so the summary bodies alone are not enough:
   - `gh api --paginate repos/<REPO>/pulls/$1/reviews` — review bodies and verdicts.
   - `gh api --paginate repos/<REPO>/pulls/$1/comments` — the individual inline findings.
   - Correlate each inline comment to its review via `pull_request_review_id`, and keep `path` + `line`/`original_line`. Every prior finding must come back with a fixed / still-open / regressed verdict.

3. **Isolate — files *and* execution.** A git worktree isolates files; it does not isolate processes, credentials, host mounts or network. Anything you run from the PR head is code the PR author controls.
   - Fetch the head into a temp branch and add a worktree under the scratchpad dir; never touch the user's working tree. Clean it up at the end (`git worktree remove --force`, delete the temp branch).
   - **Decide where execution happens.** Treat the head as *untrusted* unless `isCrossRepository` is false **and** `authorAssociation` is `OWNER`/`MEMBER`/`COLLABORATOR`. `--sandbox` / `--no-sandbox` overrides this; if the head is untrusted and no sandbox is available, run no PR code at all and say so in the review rather than reviewing by reading alone and implying otherwise.
   - **Untrusted head** → run every install, test and repro inside a container, using this repo's image (`just sandbox-build claude`, or any minimal Python image). Mount only the disposable worktree, pass **no** `--env-file` and no host credentials, and drop the network once dependencies are installed:
     ```bash
     docker run --rm -it -v "<WORKTREE>":/workspace -w /workspace claude-sandbox bash
     ```
   - Never source the PR's `.env`, never run its git hooks, and never `pre-commit install` from it.
   - Keep all `gh` metadata calls and the review posting **outside** the sandbox — those hold the user's credentials.

4. **Baseline BEFORE judging.** Install extras if the PR needs them, then run the project's lint / type / test commands and record results, so PR-caused breakage is distinguishable from environmental noise. Use `git diff <baseRefOid>...<headRefOid>` for "what this PR changed".

5. **Fan out independent skeptics.** Launch 2–3 sub-agents **in parallel** (one message, multiple Agent calls), each scoped to ONE risk surface (e.g. core algorithm / integration & breaking changes / plugins-config-tests-packaging). Give each the sub-agent prompt template from `docs/adversarial-review.md`, filled in for its surface — including the base SHA and, when sandboxed, how to run commands inside the container. They must reproduce findings by execution and not duplicate each other.

6. **Verify headline claims yourself.** Re-run each agent's most severe finding with your own repro. Only keep what survives. Watch for claims that are wrong *in the author's favor* too (mis-stated breaking changes, "deleted" tests that were merely moved).

7. **Synthesize & show the user.** Rank blocker → high → medium → low; separate verified from hypothesized; **credit what's genuinely correct** (a status table is ideal on re-reviews). Present the results and STOP — do not post unless `--post` was passed or the user asks.

8. **Post (only when asked).** Build a review with a summary body + inline comments anchored to `file:line` at `headRefOid` (inline comments must land on diff lines; otherwise put them in the body). Pick the verdict deliberately (`REQUEST_CHANGES` for a real correctness drop or several mediums; else `COMMENT`/`APPROVE`). Submit as the user's own GitHub account with **no AI attribution**. Verify every inline comment anchored, then report the review URL.
