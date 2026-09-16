# Adversarial Code Review

A method for reviewing pull requests where **every claim carries a reproduction or it doesn't ship**. Optimized for finding real correctness bugs (not style nits) in high-risk changes: security/compliance seams, algorithms, vendored code, async/concurrency, and anything that fails silently.

## The method

Five moves, in order.

### 1. Isolate & baseline
Review in a throwaway git worktree checked out at the PR head, so the working tree is never touched:

```bash
git fetch <remote> pull/<PR#>/head:pr-<PR#>
git worktree add /tmp/review-<PR#> pr-<PR#>
```

**A worktree isolates files, not execution.** Step 3 of this method runs the PR's code — its test suite, its dependency install, your repros — and that code is written by the PR author. On the reviewer's host it inherits the reviewer's processes, credentials, SSH agent, cloud tokens and network. For a PR from a fork or an unfamiliar author, that is arbitrary code execution, and the worktree does nothing about it.

So decide the trust level before running anything. A same-repo branch from a maintainer can run on the host. Anything else runs in a container — this repo ships one (`just sandbox-build claude`; see `sandbox/README.md`), but any minimal Python image works:

```bash
docker run --rm -it -v /tmp/review-<PR#>:/workspace -w /workspace claude-sandbox bash
```

Mount only the disposable worktree, pass no env file and no host credentials, and drop the network once dependencies are installed. Never source the PR's `.env`, run its git hooks, or `pre-commit install` from it. Keep `gh` calls and the review posting outside the sandbox — those are exactly the credentials you are keeping away from the code. If you cannot sandbox an untrusted PR, review it by reading and **say so in the review**; an unverified finding is the one thing this method does not allow.

Then get the base right: use the PR's own base SHA (`gh pr view <PR#> --json baseRefOid,headRefOid`), not a hard-coded `main`. A PR targeting a release or feature branch diffed against `main` will attribute pre-existing defects to it, or hide its changes entirely.

*Before* judging anything, run the project's lint / type / test commands and record the result. That's your baseline — it lets you separate PR-caused breakage from pre-existing environmental noise (a missing optional dep, deselected markers, a flaky unrelated test).

### 2. Fan out independent skeptics
Spawn several sub-agents, each scoped to **one** risk surface (core algorithm / integration / tests-and-packaging). They must not see each other's conclusions — independent agreement is signal, not echo. Tell each to **hunt for bugs and reproduce them**, not to summarize the code. Two or three agents is usually right; more just produces overlap.

### 3. Reproduce by execution, not eyeballing
A finding isn't real until it's been run. "Crashes on `tool_calls: None`" is worth nothing until you've watched it throw. Standalone repros (`uv run python -c "..."`, a scratch test, a one-off script) are the currency of the review.

### 4. Verify before you report
Re-run the agents' **headline** claims yourself. Agents are confidently wrong sometimes — catching a claim that's false *in the author's favor* matters as much as catching a bug. Only surface what survives your own repro.

### 5. Rank, separate, and credit
Blockers first, then high / medium / low. Distinguish verified from hypothesized. Say plainly which claimed fixes are genuinely correct so the author doesn't churn on the parts they nailed. A review that only lists faults is a worse review.

On a **re-review**, the status table is per *finding*, so load the individual findings and not just the review bodies — `gh api --paginate repos/<REPO>/pulls/<PR#>/reviews` for the verdicts and `.../comments` for the inline findings, correlated through `pull_request_review_id`. Paginate both; a long-running PR overflows one page, and a finding you silently dropped reads to the author as a finding you withdrew.

**Through-line: evidence over opinion.**

## The reusable sub-agent prompt

Fill in the bracketed parts, one instance per risk surface.

```text
Adversarial code review. Code is checked out at: <WORKTREE_PATH>
The PR's base commit is <BASE_SHA>; `git diff <BASE_SHA>...HEAD -- <path>`
shows only this PR's changes. Run every command <WHERE: on the host / inside
the review container, e.g. `docker exec <NAME> ...`>.

Focus ONLY on: <SPECIFIC FILES / ONE RISK SURFACE>.
(Another reviewer owns <the other areas> — do not duplicate.)

Context: <1–3 sentences on what the code does and any claims the author makes>.

If this is a RE-review, here are the previously-reported issues that were
supposedly fixed — verify each is ACTUALLY fixed AND that the fix introduced
no new bug:
  <list prior findings, or delete this block for a first review>

Your job: find REAL bugs by reasoning AND by executing. Be adversarial.
Specifically probe:
  1. Empty / degenerate / malformed inputs — write and RUN standalone repros
     (`cd <worktree> && uv run python -c "..."`). Try: empty collections,
     single elements, None/""/negative/huge values, missing keys, wrong types.
  2. Correctness of the core logic — off-by-one, shape/index bugs, wrong math,
     division-by-zero, NaN propagation, silently-swallowed exceptions,
     mutable defaults, incorrect normalization.
  3. Fail-open vs fail-closed — if this guards something (auth, PII, money,
     deletes), does an unexpected error let bad data THROUGH? State it plainly.
  4. Integration seams — every call site that reaches the risky path; anything
     that bypasses the intended choke point; breaking changes to public APIs.
  5. Test quality — are tests real assertions or over-mocked to pass trivially?
     Do they use the REAL dependency or a stub that hides the bug? What is the
     single highest-value MISSING test?

Rules:
  - Report ONLY findings you verified by reading or executing. No speculation.
  - For each finding: exact file:line, what's wrong, a concrete triggering
    input, and observed-vs-expected.
  - Rank by severity. Distinguish real bugs from cosmetic nits.
  - If a claimed fix is genuinely correct, say so plainly.
  - If the code is solid, say that — do NOT manufacture findings.
  - Be concise.
```

Two knobs to turn per PR:
- **Number of agents** = number of genuinely independent risk surfaces (usually 2–3). Overlapping scopes produce echo, not coverage.
- **"Be adversarial" + "don't manufacture findings"** always appear together. The first pushes them to dig; the second stops them inventing severity to look useful.

## Posting the review

- Draft first; show the human before posting.
- Structure: a summary body (a **status table crediting what's fixed** is great on re-reviews) plus inline comments anchored to `file:line` at the PR head SHA.
- Inline comments only attach to lines present in the diff. Files added wholesale are fully anchorable; for a change to an *unchanged* line, anchor to the nearest related diff line or put it in the body.
- Choose the verdict deliberately: `REQUEST_CHANGES` for a real correctness drop or several mediums; `COMMENT`/`APPROVE` when only nits remain.
