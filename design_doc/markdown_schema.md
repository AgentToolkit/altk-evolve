# Markdown Frontmatter Schema (Phase 0 Contract)

**Status:** Locked for Phase 1 implementation
**Source:** `design_doc/implementation_plan.md` Phase 0 deliverable
**Implements:** the on-disk wire format for `MarkdownEntityBackend`

This document defines the canonical YAML frontmatter shape for guideline and fact `.md` files in `evolve_memory/`. It is the contract between extraction pipelines, the markdown backend, the trigger/content indexes, and any human or tool that reads these files.

---

## Filename and identity (mechanically enforced)

**Filename = stable_id (ULID) for ALL phases. No exceptions.**

- `stable_id` is a 26-character [ULID](https://github.com/ulid/spec) generated at write time.
- The trigger slug, category, authority tier, and namespace are NOT in the filename. They live in the directory PATH or in the YAML frontmatter.
- A guideline's `stable_id` is preserved across the entire lifecycle: extraction, conflict resolution, promotion (path-move), edits. Only the path changes; the filename never does.

This is a deliberate response to Codex review rounds 1–3 where slug-as-filename was repeatedly identified as a collision and silent-overwrite vector.

---

## Directory layout (Phase 1 → 4)

```
evolve_memory/
├── guidelines/
│   └── {namespace}/
│       ├── generated/
│       │   └── {category}/                # Phase 1 layout (flat under {namespace}/{type}/{stable_id}.md
│       │       └── {stable_id}.md         # is acceptable for Phase 1; the {generated,authoritative}/{cat}/
│       └── authoritative/                 # split lands in Phase 4)
│           └── {category}/
│               └── {stable_id}.md
├── facts/
│   └── {namespace}/
│       ├── canonical/                     # Phase 4 opt-in for high-stakes facts
│       │   └── {domain}/
│       │       └── {stable_id}.md
│       └── {domain}/                      # Default tier; bulk auto-extracted facts
│           └── {stable_id}.md
├── .indexes/                              # Phase 3
│   ├── trigger.sqlite-vss
│   ├── content.sqlite-vss
│   └── manifest.json                      # per-namespace high-watermarks
├── .outbox/                               # Phase 3
│   └── legacy_writes.sqlite               # durable outbox for dual-write
└── telemetry/                             # Phase 2
    └── {YYYY-MM-DD}.jsonl
```

Where `{category}` is one of `strategy`, `recovery`, `optimization` for guidelines.

---

## Guideline frontmatter

```markdown
---
schema: guideline/v1                       # version-stamped for forward-compat
stable_id: 01HXY3K2N5QPVWZ8ABCDEFGHJK      # ULID; equals the filename stem
type: guideline
namespace: default
authority: generated                       # generated | authoritative

# Core Guideline schema fields (mirrors altk_evolve/schema/guidelines.py)
trigger: "auth_failed_with_401_after_token_refresh"
category: recovery                         # strategy | recovery | optimization
content: "Re-authenticate with full credentials, not refresh-token flow"
rationale: |
  Token refresh can silently fail when the refresh token has expired even if
  the access token request returned 2xx. Full re-auth is the recovery path
  that worked across 12 of 13 observed cases.
implementation_steps:
  - "Detect 401 response from any tool call within 5 minutes of a token-refresh attempt"
  - "Discard the cached access_token AND refresh_token"
  - "Trigger interactive_login flow"
  - "Retry the failed tool call with the new access_token"

# Linking (Phase 5)
related:
  - "auth_failed_401"
  - "refresh_token_expired"

# Outcome ledger (Phase 2)
outcome_evidence:
  observations:
    - trajectory_id: traj-abc-123
      signal_source: tool_error
      observed_outcome: failure
      confidence: 0.95
      observed_at: 2026-05-10T14:22:00Z
      detail: "auth_handler raised 401 after retry exhaustion"
  aggregated:
    confirmed_successes: 12
    confirmed_failures: 1
    inferred_successes: 0
    inferred_failures: 0
    judge_successes: 0
    judge_failures: 0
    unknown: 4
    confidence_weighted_score: 0.78
    last_observed_at: 2026-05-10T14:22:00Z

# Index recovery (Phase 3)
index_generation: 12847                    # monotonic, namespace-scoped

# Provenance / curation metadata
created_at: 2026-04-29T09:11:00Z
created_by: "evolve-bot"                   # or a user id for human-authored
source_trajectory_ids:
  - traj-abc-123
  - traj-xyz-789
slug_collision: []                         # populated only when collisions are detected

# Curation history (only present in authoritative tier; Phase 4)
promoted_from: generated/recovery/01HXY3K2N5QPVWZ8ABCDEFGHJK.md
promoted_by: "@vinod"
promoted_at: 2026-05-12T16:00:00Z
promotion_reason: "Verified across 12 production sessions; no failures."
---

# Re-authenticate after token refresh failure

(Body content is human-readable elaboration of the rationale and steps.
Optional; primary signal is in frontmatter.)
```

### Field rules

| Field | Required? | Notes |
|---|---|---|
| `schema` | yes | `guideline/v1` initially. Increment major on breaking change. |
| `stable_id` | yes | Must equal the filename stem. |
| `type` | yes | Always `guideline`. |
| `namespace` | yes | Must equal the namespace component of the path. |
| `authority` | yes | `generated` or `authoritative`. Must match path tier. |
| `trigger` | yes | Free-form natural-language anchor. NOT used as filename. |
| `category` | yes | One of `strategy`, `recovery`, `optimization`. Must match path. |
| `content`, `rationale`, `implementation_steps` | yes | From the LLM extraction pipeline. |
| `related` | optional, default `[]` | Trigger-slug references; Phase 5. |
| `outcome_evidence` | optional, default `null` | Populated by Phase 2 extractors. |
| `index_generation` | yes | Monotonic; backend bumps on each write. |
| `created_at` | yes | ISO-8601 UTC. |
| `created_by` | optional | `evolve-bot` for auto-extracted; user id for human-authored. |
| `source_trajectory_ids` | optional | Provenance back-refs. |
| `slug_collision` | optional, default `[]` | List of stable_ids of other guidelines sharing the same trigger slug. |
| `promoted_*` | only on authoritative tier | Set when promotion occurs (Phase 4). |

---

## Fact frontmatter

```markdown
---
schema: fact/v1
stable_id: 01HXY4M9P3QRSWZ7BCDEFGHJKL
type: fact
namespace: default
authority: generated                       # or "canonical" for Phase 4 opt-in tier
domain: payments_api

# Core Entity schema (mirrors altk_evolve/schema/core.py)
content: "The /v1/refunds endpoint accepts an idempotency_key header that must be unique per refund attempt."
metadata:
  source_url: "https://internal.docs/payments-api#refunds"
  observed_via: "tool_call_inspection"

# Index recovery
index_generation: 12848

# Provenance
created_at: 2026-04-29T09:15:00Z
created_by: "evolve-bot"
source_trajectory_ids:
  - traj-abc-123

# Canonical tier metadata (only present when authority == "canonical"; Phase 4)
last_reviewed_by: "@vinod"
last_reviewed_at: 2026-05-12T16:00:00Z
---

(Optional body elaboration.)
```

Facts default to a **single tier** (no authority split). The opt-in `canonical/` tier is reserved for high-stakes facts (API contracts, compliance invariants, domain canonicals). See `design_doc/transform_evolve.md` §4.3.

---

## Datetime normalization

- All `*_at` fields are ISO-8601 strings in UTC with explicit timezone (`...Z` or `+00:00`).
- The serializer emits `Z` suffix; the deserializer accepts either form.
- Naive datetimes are rejected at write-time (raises `ValueError`).

## List serialization

- Empty lists are written explicitly as `[]`, not omitted.
- Single-element lists are written as a YAML list, not as a scalar.

## Reserved field names

The following frontmatter keys are reserved by the backend and may not be used in `metadata`:

- `schema`, `stable_id`, `type`, `namespace`, `authority`, `index_generation`
- `created_at`, `created_by`, `source_trajectory_ids`, `slug_collision`
- `promoted_from`, `promoted_by`, `promoted_at`, `promotion_reason`
- `last_reviewed_by`, `last_reviewed_at`

Custom metadata goes under the `metadata: { ... }` nested map.

---

## Schema versioning

- `schema: guideline/v1` is the contract for Phase 1.
- Breaking changes increment to `v2`. Backend supports reading older versions via a small migration adapter; writes always emit the latest version.
- Forward-compat: unknown frontmatter keys are preserved on read-modify-write to avoid losing data the writer didn't recognize.

---

## What this contract enables (and prevents)

**Enables:**
- O(1) lookup by `stable_id` via filesystem path resolution.
- Collision-aware lookup by `trigger` via the trigger index returning `list[entity_id]`.
- Authority + category routing via directory structure.
- Outcome-aware ranking via the populated `outcome_evidence` field.
- Index recovery via per-entity `index_generation` + per-namespace manifest.
- Promotion as a path-move with full audit trail in frontmatter.

**Prevents:**
- Slug collisions silently overwriting guidelines (filename is stable_id, not slug).
- Stale index returning outdated content (generation tracking + recovery).
- Lost provenance during conflict resolution (observations are append-only).
- Schema drift between phases (version-stamped, forward-compat).
