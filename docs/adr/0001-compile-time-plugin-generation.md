# 1. Compile-time plugin code generation

- **Status:** Accepted
- **Date:** 2026-04-29
- **Tracking issue:** [#219](https://github.com/AgentToolkit/altk-evolve/issues/219)

## Context

Each entry under `platform-integrations/` (`bob`, `claude`, `codex`, `claw-code`) carries a hand-edited copy of the same conceptual `evolve-lite` plugin — same skills, similar `lib/`, similar scripts, similar prose. Drift is the dominant failure mode: PRs #188, #196, #199, and #230 each landed a fix in one platform's copy without updating the others, and the auto-memory note `evolve-lite has three variants` exists specifically because contributors keep forgetting Bob (whose paths are also structurally different — a `evolve-lite:<skill>/` colon-prefixed flat layout that is incompatible with Windows). When `save_entities.py` is compared across the three platforms, some divergence reflects genuine per-platform need (lib-path discovery in different runtime environments) but most of it is unintentional drift (e.g., the `entity["owner"]` stamping logic differs without rationale).

There is no enforcement mechanism that the four copies stay in sync, no single source of truth for shared content, and no clear "edit here" location for maintainers or AI agents who are asked to modify a skill.

## Decision

Treat `platform-integrations/` as **generated output**. Add a new top-level `plugin-source/` directory that is the single canonical source for all four platforms' plugin code. A small Python build script renders `plugin-source/` into the per-platform trees under `platform-integrations/` using Jinja2 templating. The generated tree remains committed to the repository so that PR review, agent comprehension, and `git log` all continue to work without requiring readers to run a build first. A pre-commit hook and CI gate enforce that the committed output matches a fresh render of the source.

Per-file variation is expressed in three layered ways, applied per file as appropriate:
- *Per-platform shim modules* for code variation that's structurally factorable (e.g. lib-path discovery). The canonical script body remains valid lintable Python and imports from the shim.
- *Jinja2 conditionals* for prose variation tuned per audience LLM (e.g. `SKILL.md`).
- *Per-platform full-file overlays* for files unique to one platform (e.g. Claude's `on_stop.py` hook), and for files where prose divergence is so substantial that conditionals would harm readability.
- Files with no variation are copied verbatim by the build.

On-disk paths are colon-free everywhere (Windows compatibility). Bob's existing `evolve-lite:<skill>/` directory naming is replaced with `evolve-lite-<skill>/`. The user-facing `evolve-lite:` namespace is preserved at the invocation layer (Claude/Codex via plugin manifest; Bob via the `name:` frontmatter in `SKILL.md` if it accepts colons there, with documented fallback if not).

## Alternatives Considered

### Option A — `platform-integrations/common/` with symlinks back into per-platform trees

The shared content lives in a `common/` folder; each platform tree links to it. Backwards-compatible at the filesystem level if symlinks are followed.

Rejected because: the install step would still need to dereference symlinks (the issue raised the question of "dereference any symlinks created" up front), so this approach already implies a build step at install time — symlinks are not actually saving us a build, just hiding it. Symlinks also do not handle Bob's structural rename problem (different on-disk layout), and they cannot express per-file content variation (e.g. the lib-path discovery prelude that genuinely differs by platform). Editor and tool behavior with symlinks is also inconsistent across platforms.

### Option B — Move generated plugin code to a separate repository

The unified source lives here; the per-platform output is published to a sibling repo.

Rejected because: refactors that span the engine and the rendered output would no longer be atomic — a contributor changing a shared template plus its rendered result would need two PRs across two repos in lockstep, with no way for CI to enforce coherence between them. The "less perceived overhead" of a smaller home repo is illusory: the cross-repo sync overhead is strictly worse than local clutter that can be marked as generated.

### Option C — Generated tree gitignored, built fresh by the installer

Source is canonical; rendered output never lands in git. The installer (or a post-clone hook) builds it on demand.

Rejected because: PR reviewers (human and agent) lose visibility into what actually changed in the rendered output for a given source change. Fresh checkouts can't be inspected without first running the build. `git log -- platform-integrations/...` becomes useless. The PR-review story is the dominant cost.

### Option D — Author a build tool in Go or Rust

A standalone static binary instead of Python+Jinja2.

Rejected because: the build only runs in the developer/CI loop, never on user machines (end users continue to use `install.sh` against the committed generated tree). The project is already Python-via-`uv`, so the Python+Jinja2 path adds zero new toolchain. A Go/Rust binary would add a build/release pipeline for the tool itself with no compensating benefit at the user-facing layer.

## Consequences

### Positive

- A single canonical edit location for maintainers and agents. The auto-memory note `evolve-lite has three variants` becomes obsolete.
- Drift between platform copies is mechanically prevented (CI fails when committed output ≠ fresh render).
- Adding a new platform integration becomes a config-file addition rather than a copy-paste, lowering future maintenance cost.
- Synthesis of the three drifted versions of `save_entities.py` (and similar files) is captured once, in the canonical source, with documented rationale.
- Bob's Windows incompatibility is fixed in passing.

### Negative

- The repository now has *both* hand-edited source (`plugin-source/`) and generated output (`platform-integrations/`) in tree. Contributors must be aware of the distinction. Mitigated by `.generated` marker files in each generated subtree and `# DO NOT EDIT` headers on rendered files where comment syntax allows.
- The build step adds a small but real friction to every change. Mitigated by the `just compile-plugins` recipe and the pre-commit hook that runs it automatically.
- Rendered files are no longer plain Python (they're rendered from `.j2` templates), so IDE tooling on the generated copy is "view-only". Maintainers edit the canonical source where Python files remain valid Python (per the shim-module override pattern); the `.j2` files are mostly markdown and don't lose linting.
- Bob's user-facing slash-command surface may change (`/evolve-lite:learn` → `/evolve-lite-learn`) if Bob's `name:` frontmatter rejects colons. Verified during implementation; documented either way.

### Migration

Single PR, multi-commit, per-commit CI green. Stages: (0) this ADR; (1) introduce build pipeline rendering byte-identically to current `platform-integrations/`; (2) synthesize drifted files; (3) rename Bob colon paths; (4) decouple `custom_modes.yaml` from skill enumeration. Each stage is a commit reviewable in isolation and revertable on its own.
