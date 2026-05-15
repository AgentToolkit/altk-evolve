# Implementation Plan: Evolve Paradigm-A Transformation

**Status:** Plan for review
**Scope:** Six-phase rollout (~3–4 months) implementing Paradigm A from `design_doc/transform_evolve.md`, with Paradigm B preserved as an architectural escape hatch via measurable gates.
**Branch in use:** `file-based-memory` (current branch on origin)

---

## 1. Context

The team has reviewed `design_doc/transform_evolve.md` (committed at `c816252` on `file-based-memory`) and is committing to **Paradigm A** as the core direction:
- `.md` files become the source of truth for both tracks (guidelines + facts)
- Vector infrastructure (Milvus / pgvector) retired from runtime
- Asymmetric investment: deep architectural depth on the **guidelines track** (the moat — trajectory-mined procedural rules with outcome context), commodity treatment on the **facts track**

**Paradigm B is kept as an option.** If telemetry from Phase 2+ shows trigger-match recognition is insufficient, we graduate to a shadow vector index for content (see §11 graduation gates). The design must keep this path open without requiring a rewrite.

**Existing architecture facts (validated by exploration agents):**
- `BaseEntityBackend` (`altk_evolve/backend/base.py:16–187`) is type-agnostic: facts and guidelines share one mutation path, schema, conflict-resolution call, and retrieval path. The transformation forks this — owned cost.
- Existing concrete backends: `FilesystemEntityBackend` (JSON blobs, NOT MD), `MilvusEntityBackend`, `PostgresEntityBackend`. Selection via `EvolveConfig.backend: Literal[...]` (`altk_evolve/config/evolve.py:5–15`) and factory in `EvolveClient.__init__` (`altk_evolve/frontend/client/evolve_client.py:21–43`).
- `Guideline` schema (`altk_evolve/schema/guidelines.py:8–13`) carries `content`, `rationale`, `category` (strategy/recovery/optimization), `trigger`, `implementation_steps`. `task_description` lives in entity metadata and is the current clustering signal.
- Conflict resolution (`altk_evolve/llm/conflict_resolution/conflict_resolution.py`) is invoked from `BaseEntityBackend.update_entities` (line 156 of base.py); single LLM prompt handles any entity batch.
- Phoenix span sync (`altk_evolve/sync/phoenix_sync.py`) is the only trajectory ingress today; offline batch, no hot-path extraction.
- Retrieval surface: `EvolveClient.search_entities`, `get_entity_by_id`, `get_all_entities`, `get_public_entities`, `cluster_guidelines`, `consolidate_guidelines`. MCP tools layered on top (`altk_evolve/frontend/mcp/mcp_server.py`).
- 9 plugin skills exist per platform under `platform-integrations/{claude,codex,bob,claw-code}/plugins/evolve-lite/skills/evolve-lite/`. No template/macro generation — skills are static SKILL.md + scripts.

---

## 2. Constraints / non-goals

**Constraints:**
1. Existing extraction pipelines (`llm/fact_extraction/`, `llm/guidelines/`) must keep producing entities throughout the transition.
2. Plugin contracts (MCP tool signatures, skill scripts) must keep working during cutover; breaking changes deferred to a separate v2.
3. Retrieval `inject(task)` shape (free-form task description in, ranked guidelines out) is preserved.
4. Vector backends (`milvus`, `postgres`) remain selectable via `EvolveConfig.backend` after Paradigm A ships — they are deprecated, not deleted.

**Non-goals (explicitly out of scope for this plan):**
- Rewriting plugin generation or moving `platform-integrations/`.
- Changing the Phoenix capture pipeline beyond adding outcome-signal extractors.
- Supporting multimodal memory (image/video/audio).
- Repositioning Evolve as a generic memory backend (Paradigm C).
- UI redesign — minimal frontend changes only.

---

## 3. Architecture summary

```
┌──────────────── INGRESS (unchanged in shape) ────────────────┐
│ Phoenix spans → phoenix_sync.py → trajectory entities        │
│                                 → guideline extraction       │
│                                 → fact extraction            │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────── EXTRACTION (Phase 2 upgrades) ──────────────┐
│ llm/guidelines/          llm/fact_extraction/                │
│ + outcome_extraction/    (signal-source-aware enrichment)    │
└──────────────────────────────────────────────────────────────┘
                              │ EvolveClient.update_entities
                              ▼
┌──────────────── STORAGE (NEW: MarkdownEntityBackend) ───────┐
│  evolve_memory/                                              │
│  ├── guidelines/{ns}/authoritative/{cat}/{stable_id}.md     │  Phase 4
│  ├── guidelines/{ns}/generated/{cat}/{stable_id}.md         │  Phase 1
│  ├── facts/{ns}/canonical/{domain}/{stable_id}.md           │  Phase 4 (opt-in)
│  ├── facts/{ns}/{domain}/{stable_id}.md                     │  Phase 1
│  ├── .indexes/                                              │  Phase 3
│  │   ├── trigger.sqlite-vss                                 │
│  │   ├── content.sqlite-vss                                 │
│  │   └── manifest.json   (per-namespace high-watermarks)   │
│  └── telemetry/{date}.jsonl                                 │  Phase 2
│                                                              │
│  CONTRACT: filename = stable_id (ULID) for ALL phases.      │
│  trigger slug, category, authority live in YAML frontmatter │
│  and/or directory PATH — never in filename. Path encodes    │
│  authority + category; identity is always stable_id.        │
│                                                              │
│  Paradigm B escape hatch: existing milvus/postgres backends │
│  remain selectable via EVOLVE_BACKEND env var.              │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────── RETRIEVAL (Phase 3 cutover, HYBRID) ─────────┐
│ recognition/recognizer.py     mode="hybrid" (default)        │
│   STEP 1a: trigger embedding kNN (~3–12 MB SQLite-VSS)      │
│   STEP 1b: content embedding kNN (~10–15 MB SQLite-VSS)     │
│   STEP 1c: blend scores: α·trigger + (1-α)·content           │
│           OR llm-as-router fallback for low-confidence       │
│   STEP 2: filesystem lookup BY STABLE ID (not slug)          │
│   STEP 3: outcome-aware ranking (using outcome_evidence)     │
│ + linking: 1-hop expansion via YAML `related: [...]`        │
│                                                              │
│ Trigger-only-primary is a v2 simplification gated on the     │
│ labeled benchmark (§11). Until then content index stays.    │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
            EvolveClient.search_entities → MCP tools → plugins
                              │
                              ▼
                   Phase 5: EvolveWatcher sidecar
                   (proactive mid-session injection)
```

---

## 4. Phase 0 — Schema lock + spike (3–5 days, no production code)

**Goal:** unblock Phase 1 by freezing every shape that downstream code depends on. No production code lands here; output is decisions + signed-off schemas.

**Deliverables:**
1. **Outcome-evidence schema** as a Pydantic model. Place: `altk_evolve/schema/outcome_evidence.py` (new).
   - Mirror the schema in `design_doc/transform_evolve.md` §7.1.1: `observations: list[OutcomeObservation]` + `aggregated: AggregatedOutcome` + `last_observed_at`.
   - `OutcomeObservation` carries `trajectory_id`, `signal_source` (enum), `observed_outcome` (enum), `confidence` (float), `observed_at`, `detail`.
   - `AggregatedOutcome` carries the seven counters + `confidence_weighted_score`.
2. **Extended `Guideline` schema** in `altk_evolve/schema/guidelines.py`. Add `outcome_evidence: OutcomeEvidence | None`, `related: list[str]` (default `[]`). Keep all existing fields. Pydantic backwards-compat — old records load with `outcome_evidence=None`.
3. **MD-frontmatter serialization spec.** Document the canonical YAML frontmatter shape for `Guideline` and `Fact` files in `design_doc/markdown_schema.md` (new). Include: top-level field order, datetime ISO-8601 normalization, list serialization rules. This is the contract `MarkdownEntityBackend` implements.
4. **Canonical-tier opt-in decision.** Pick one of the two §4.3 options (directory `facts/{ns}/canonical/...` vs `canonical: true` frontmatter flag). Recommend directory-based — uses filesystem-as-index uniformly.
5. **Spike:** an interface skeleton at `altk_evolve/backend/markdown.py` (no impl) — Pydantic config class + abstract method stubs satisfying `BaseEntityBackend`. Confirms the shape works before anyone writes the impl.
6. **Decision log:** §6 questions from `transform_evolve.md` answered in writing (commit to `design_doc/`).

**Reuse:**
- Pydantic patterns from `altk_evolve/schema/core.py` and `schema/guidelines.py`.
- Backend skeleton mirrors `altk_evolve/backend/filesystem.py:32–311` patterns (especially `_active_data` and atomic-write).

**Exit criteria:**
- Outcome-evidence schema reviewed by ≥2 team members.
- All §6 questions answered in `design_doc/`.
- Spike compiles and `BaseEntityBackend` abstract conformance is verified by mypy/typing alone (no runtime tests yet).

---

## 5. Phase 1 — MarkdownEntityBackend + shadow writes (2–3 weeks)

**Goal:** ship a working MD-tree backend that runs alongside the existing backend in shadow-write mode. Reads still come from the legacy backend; every write is mirrored to MD. De-risks storage migration without breaking anything.

**Files to create:**
- `altk_evolve/backend/markdown.py` — `MarkdownEntityBackend(BaseEntityBackend)` implementing all 12 abstract methods. Key implementation choices:
  - **File identity = stable ID, NOT slug.** Filename: `evolve_memory/{namespace}/{type}/{stable_id}.md` where `stable_id` is a ULID generated at write time (default) or a hash-prefixed slug like `{slug}-{short_hash}.md` for human readability. The `trigger_slug` is stored in YAML frontmatter as `trigger:` and is queryable via the trigger index (Phase 3) — but it is **not** the identity. This prevents trigger-slug collisions from silently overwriting guidelines (Codex review §3 finding).
  - **Collision detection on write.** When persisting an entity whose `trigger_slug` already maps to ≥1 existing file, the backend must (a) emit a structured warning into the telemetry log, (b) annotate both records' frontmatter with a `slug_collision: [other_id, ...]` array, and (c) refuse silent overwrite. The trigger index in Phase 3 must support `slug → list[entity_id]`, not `slug → entity_id`.
  - **YAML frontmatter** carries all metadata + structured fields; markdown body is `content`.
  - **Concurrency:** mirror `FilesystemEntityBackend.threading.Lock` (line 42) — *single process* lock. **For multi-process safety, use `fcntl.flock` on a `.lockfile` per namespace** — addressing critic concern.
  - **Atomic writes:** mirror `filesystem.py:82–89` temp-file + `os.replace`.
  - **`_active_data` pattern:** mirror `filesystem.py:44, 295–296` so search-during-update sees uncommitted state.
  - **`update_entities` template method:** inherit from `BaseEntityBackend.update_entities` (`base.py:110–186`); override `_add_entity` / `_update_entity` / `_delete_entity` / `_post_update`.
  - **Conflict resolution:** invoke existing `llm/conflict_resolution/conflict_resolution.py` unchanged in Phase 1 (per-track split happens in Phase 2).
- `altk_evolve/config/markdown.py` — `MarkdownSettings(BaseSettings)`: `data_dir` (default `~/.evolve_memory/`), `lock_timeout_seconds`, `enable_git_commit` (bool, default false; deferred), `evolve_bot_author` (string).
- `altk_evolve/backend/_md_serialization.py` — pure functions for YAML frontmatter ↔ Pydantic; reuse the schema spec from Phase 0.
- `tests/unit/test_markdown_backend.py` — mirror `tests/unit/test_filesystem_backend.py` structure: namespace CRUD, entity CRUD, search filters, atomic-write under interruption (use `monkeypatch` to simulate kill mid-write), conflict-resolution integration.

**Files to modify:**
- `altk_evolve/config/evolve.py:5–15` — extend `backend: Literal["milvus", "filesystem", "postgres", "markdown"]`. Add new env var `EVOLVE_BACKEND_SHADOW: Literal[...] | None = None` for dual-write.
- `altk_evolve/frontend/client/evolve_client.py:21–43` — add `elif self.config.backend == "markdown"` case. Add shadow-write wrapper that invokes both primary + shadow backends on writes; reads only from primary.
- `altk_evolve/cli/cli.py` — new command `evolve dual-write-verify <namespace>` that diffs the primary backend against the shadow backend and reports drift. Run nightly via cron to validate parity.

**Reuse pointers:**
- `altk_evolve/backend/filesystem.py` — the closest template; reuse temp-file write pattern, `_active_data` pattern, lock pattern, namespace-not-found exception.
- `altk_evolve/utils/serialization.py:10` — `serialize_content` utility for content normalization.
- Test patterns from `tests/unit/test_filesystem_backend.py` and conftest fixtures (already use `tmp_path`).

**Tests:**
- Unit: 70+ tests mirroring filesystem suite.
- Integration: spin up `MarkdownEntityBackend` in `tests/e2e/test_md_backend_e2e.py` running the full extraction pipeline against a real Phoenix fixture (`tests/fixtures/appworld_venmo_task_trajectory.json`). Verify entity counts match against existing filesystem backend on the same trajectory.
- **Shadow-write parity test:** dual-write fixture, then run the new `dual-write-verify` CLI; expect zero drift.

**Exit criteria:**
- All `BaseEntityBackend` abstract methods implemented and unit-tested.
- Shadow-write mode runs nightly in dev environment for ≥1 week with `dual-write-verify` reporting zero drift.
- E2E test passes: full extraction → MD → search round-trip on the venmo fixture.

---

## 6. Phase 2 — Outcome metadata + extraction upgrade + telemetry (2–3 weeks)

**Goal:** ship the moat feature. Extracted guidelines now carry `outcome_evidence`. Retrieval telemetry exists. The `unknown` outcome state and confidence-weighted scoring are live.

**Files to create:**
- `altk_evolve/llm/outcome_extraction/__init__.py`
- `altk_evolve/llm/outcome_extraction/tool_signals.py` — extracts tool errors, retries, exceptions from Phoenix span attribute data. Detects `status_code == "ERROR"` (already used in `phoenix_sync.py`) and richer patterns.
- `altk_evolve/llm/outcome_extraction/trajectory_shape.py` — detects retry-success patterns (recovery), max-iter exhaustion (likely failure), terminate-cleanly (likely success).
- `altk_evolve/llm/outcome_extraction/aggregator.py` — folds `OutcomeObservation` records into the `aggregated` summary; computes confidence-weighted score per the formula in §7.1.1.
- `altk_evolve/llm/conflict_resolution/guideline_resolver.py` — guideline-specific conflict-resolution prompt that ranks by `confidence_weighted_score`; falls back to base resolver behavior when scores are tied or unknown. **This is the per-track split.**
- `altk_evolve/telemetry/__init__.py`
- `altk_evolve/telemetry/retrieval_log.py` — append-only JSONL writer at `evolve_memory/telemetry/{date}.jsonl`; logs the schema in `design_doc/transform_evolve.md` §"Telemetry — what gets logged". **Telemetry contract (Codex review §2 finding) — must be best-effort, non-blocking, failure-isolated:**
  - Async write via a bounded `queue.Queue` (default size 1000); overflow drops oldest record and increments a `dropped_records_total` counter.
  - Per-write timeout (default 50 ms); slow writes don't block retrieval.
  - **Telemetry exceptions never propagate** — caught and logged at WARN; the retrieval call always returns its result regardless of telemetry health.
  - Sampling option: `EVOLVE_TELEMETRY_SAMPLE_RATE` env var (default `1.0`); set to `0.1` in dev to reduce volume.
  - **Tests required (chaos cases):** retrieval succeeds when telemetry path is (a) unwritable / read-only, (b) disk-full simulated, (c) writer thread blocked, (d) JSONL file held by another process. Each chaos test asserts the retrieval return value AND the dropped-record counter increments.
  - **No frontmatter writes on the read hot path.** The aggregation job (below) batches frontmatter updates daily, NOT inline. Guideline files are not touched during retrieval.
- `altk_evolve/telemetry/outcome_aggregation_job.py` — daily background job that reads the JSONL log, derives implicit-usage signals (recall frequency, query diversity, retrieval-then-no-correction), updates `outcome_evidence` in each guideline's MD frontmatter atomically. Runs out-of-band; uses its own namespace lock; never holds the lock during retrieval.
- `altk_evolve/telemetry/durable_metrics.py` — **gate-grade observability separate from the lossy retrieval log** (Codex review round 2 §4 finding). The §11 graduation/rollback gates cannot rest on the lossy queue, which drops events exactly under the stress conditions the gates care about. This module exposes:
  - **Counters** (never dropped, fail-closed): `evolve_telemetry_dropped_total`, `evolve_telemetry_queue_depth_max`, `evolve_retrieval_total`, `evolve_recognition_mode_decisions_total{mode}`, `evolve_index_stale_total`.
  - **Histograms** (sampled at low rate but durable): `evolve_retrieval_latency_seconds`, `evolve_telemetry_enqueue_latency_seconds`.
  - Backend: Prometheus / OpenTelemetry exporter (configurable). Direct writes with their own small per-process lock — bypasses the lossy queue entirely.
  - **Gate evaluation rule:** if `evolve_telemetry_dropped_total / evolve_retrieval_total > 5%` over a gate's evaluation window, the gate **fails open** (no graduation or rollback decision). Alert raised. This prevents biased graduation under telemetry degradation.
- `tests/unit/llm/test_outcome_extraction.py` — fixture-driven tests for each signal extractor.
- `tests/unit/test_telemetry.py` — log-writer + aggregator tests.

**Files to modify:**
- `altk_evolve/sync/phoenix_sync.py` — after each guideline write, invoke the outcome extractors over the corresponding trajectory and emit `OutcomeObservation` records into the new entity's `outcome_evidence.observations`. (Hot path: keep this fast; signals 1 + 2 only — tool errors, trajectory shape. Reply-pattern + LLM-judge are background jobs.)
- `altk_evolve/llm/conflict_resolution/conflict_resolution.py` — dispatch by entity type: guideline → `guideline_resolver`, others → existing logic. Maintains the unified template-method API used by `update_entities`.
- `altk_evolve/cli/cli.py` — new commands:
  - `evolve outcome backfill <namespace> [--since DATE]` — re-runs outcome extraction over historical trajectories (one-time after Phase 2 ships).
  - `evolve outcome aggregate [--namespace ns]` — manually triggers the aggregator (normally cron'd).
- `altk_evolve/frontend/client/evolve_client.py` — every retrieval method (`search_entities`, `get_entity_by_id`, etc.) must emit a telemetry event. Wrap with a small decorator.
- `altk_evolve/frontend/mcp/mcp_server.py` — wrap retrieval-side tools to emit telemetry; thread `injection_event_id` through.

**Reuse pointers:**
- `altk_evolve/sync/phoenix_sync.py:_extract_trajectory` — existing parser; outcome extractors run against the parsed messages.
- `altk_evolve/llm/conflict_resolution/conflict_resolution.py` — keep the template method signature; only swap the prompt.
- `altk_evolve/llm/guidelines/clustering.py` — existing SentenceTransformer cache; reuse `_get_sentence_transformer` if outcome extractors need embeddings (they shouldn't, but the cache is there).

**Tests:**
- Per-signal-source unit tests: tool errors, retry detection, max-iter detection, terminate-cleanly detection. Use `tests/fixtures/` plus new fixtures with explicit failure/success traces.
- Aggregator tests: cold-start priors per category; confidence-weighted score formula; unknown count handling.
- Conflict resolution: regression suite (existing pass) + new tests where outcome scores resolve a tie.
- E2E: full Phoenix sync → guideline + outcome → telemetry log → aggregation → updated frontmatter. Verify the round-trip in `tests/e2e/test_outcome_e2e.py`.

**Exit criteria:**
- Every new guideline written by the pipeline carries non-empty `outcome_evidence` from at least one signal source.
- Telemetry JSONL log accumulates retrieval events; aggregator runs nightly and updates frontmatter.
- Outcome-backfill on the historical corpus completes; ≥50% of pre-existing guidelines now have populated `outcome_evidence` (per coverage projection in §7.1.1).
- New unit + e2e tests pass; existing extraction/conflict-resolution tests keep passing.

---

## 7. Phase 3 — Cutover read-from-MD + hybrid recognition + content-index migrated to MD (2–3 weeks)

**Goal:** flip authority. MD becomes source of truth. **Both** a trigger embedding index and a content embedding index ship as Day-1 (hybrid recognizer). The legacy Milvus/pgvector backend is no longer the source of truth, but a content vector index — rebuilt from the MD tree — remains in the primary retrieval path. Trigger-only-primary is a v2 optimization gated on a labeled benchmark (Codex review §1 finding).

**Why hybrid, not trigger-only (revised stance):** Codex's adversarial review correctly flagged that trigger-only recognition narrows the recall surface vs the current full-content embedding (`altk_evolve/backend/milvus.py:270`). Guidelines whose `content` / `rationale` / `implementation_steps` match a task but whose `trigger` doesn't will be under-retrieved. We address this by indexing **both** signals from Day-1 of the cutover; the moat thesis (triggers matter most for procedural memory) is preserved by ranking trigger-match higher than content-match via a tunable score blend, but content fallback exists.

**Files to create:**
- `altk_evolve/recognition/__init__.py`
- `altk_evolve/recognition/trigger_index.py` — SQLite-VSS-backed (or sqlite + numpy if VSS unavailable) embedding index over `trigger + category + short_description`. Exposes `add(slug, entity_id, embedding)`, `remove(entity_id)`, `knn(query_embedding, k) -> list[(slug, entity_id, score)]`. Note the `entity_id` axis — this addresses the slug-collision concern from Phase 1.
- `altk_evolve/recognition/content_index.py` — companion SQLite-VSS index over the full guideline content (`content + rationale + implementation_steps + task_description`). **Day-1 critical, not deferred.** Same shape as `trigger_index.py` but a different signal. ~10–15 MB per ~5k guidelines, comparable to the legacy Milvus index size. Lives alongside the trigger index in `evolve_memory/.indexes/`.
- `altk_evolve/recognition/index_builder.py` — rebuilds **both** indexes from the MD tree on demand or on file-change. `watchdog` for file-watcher mode (deferrable to optional config).
- `altk_evolve/recognition/recognizer.py` — `Recognizer` class exposing:
  - `recognize(task_description: str, *, namespace: str, k: int = 10, mode: Literal["hybrid", "trigger_only", "content_only", "llm_router"]) -> list[CandidateMatch]` where each `CandidateMatch` carries `entity_id`, `slug`, `score`, `source` ("trigger" | "content" | "both").
  - **Default `mode="hybrid"` uses unweighted Reciprocal Rank Fusion (RRF)** (revised after Codex review round 3 §4 — the previously specified `w_trigger=0.6, w_content=0.4` mathematically suppressed content rank-1 vs trigger rank-10, contradicting the content-priority requirement).
  - **Initial defaults:** `RRF_score(c) = 1/(k_rrf + rank_trigger(c)) + 1/(k_rrf + rank_content(c))` with `k_rrf = 60` (standard RRF constant) and **unit weights** for both indexes. Candidates absent from one index contribute zero from that index (effectively rank=∞). Deduplicate by `entity_id`; rerank by aggregated RRF score; rerank again with outcome score in step 3.
  - **Math sanity check** (the round-3 finding's example): with unit weights, `trigger@10 = 1/70 = 0.01428` vs `content@1 = 1/61 = 0.01639` → content rank-1 wins, as required. The previously-specified weighted form gave `trigger@10 = 0.00857` vs `content@1 = 0.00656` → content lost despite being better, which is exactly the failure mode the content-priority test catches.
  - **Why RRF over alpha-blend:** alpha-blend over raw cosine scores is calibration-fragile — score scales differ between SQLite-VSS and numpy backends, between trigger embeddings (short text) and content embeddings (long text), and missing-score semantics are ambiguous. Unit-weight RRF eliminates all three issues by ranking-based fusion AND avoids the fixed-weight bias.
  - **Weight tuning is benchmark-driven, not assumption-driven.** Weights are config-gated (`EVOLVE_RRF_W_TRIGGER`, `EVOLVE_RRF_W_CONTENT`) but their default values **must be the values under which the content-priority fixture passes**. The Phase 3 cutover gate explicitly runs `evolve recognition eval` against `tests/fixtures/recognition_eval/content_priority_cases.jsonl` AND the labeled benchmark, AND blocks cutover if either fails under the shipped weights.
  - **Recommended Phase 0 spike:** sweep `(w_trigger, w_content)` over the labeled benchmark (e.g. `{(1.0, 1.0), (1.2, 1.0), (1.5, 1.0), (1.0, 1.2)}`) and pick the combination that maximizes recall@10 *while* passing the content-priority fixture. If unit weights win, ship them. If trigger-weighted wins on recall but loses content priority, ship unit weights anyway and document the tradeoff.
  - **Required test (`tests/integration/test_hybrid_recognition_eval.py`, hardened):** a content-relevant guideline (matching task on `content`/`rationale`/`implementation_steps` but not on `trigger`) **must outrank** a weak trigger-only match (defined as: trigger embedding rank ≥ 5) in hybrid mode under the SHIPPED defaults. The test enumerates `(weak_trigger_rank, content_rank, expected_winner)` tuples; the test must pass under the exact `EVOLVE_RRF_W_*` values shipped to production. CI red bar = cutover blocker.
  - Alpha-blend remains available as `mode="alpha_blend"` (with explicit normalization) for experimentation; not the default.
  - LLM-router fallback uses `llm_settings.recognition_router_model` for high-stakes calls or low-confidence hybrid results.
- `altk_evolve/recognition/eval/labeled_benchmark.py` — labeled retrieval benchmark harness: takes `(task_description, expected_relevant_entity_ids[])` pairs, computes recall@10, NDCG@5, P@5, and a held-out review set for eyeball review. Source labels from: (a) historical Phoenix sessions where a guideline was injected and the agent's subsequent action confirms relevance, (b) team-curated review set in `tests/fixtures/recognition_eval/`.
- `tests/unit/recognition/test_trigger_index.py`
- `tests/unit/recognition/test_content_index.py`
- `tests/unit/recognition/test_recognizer.py`
- `tests/integration/test_hybrid_recognition_eval.py` — runs the labeled benchmark, asserts hybrid ≥ legacy on recall@10 within a tolerance band.

**Files to modify:**
- `altk_evolve/frontend/client/evolve_client.py` — `search_entities` for `entity_type="guideline"` now uses the three-step flow: `Recognizer.recognize(mode="hybrid")` → `MarkdownEntityBackend.get_by_id()` (lookup is by stable ID, not slug) → outcome-weighted ranking blend. Other entity types fall through to filesystem-style search.
- `altk_evolve/backend/markdown.py` — add `get_by_id(namespace_id, entity_id) -> RecordedEntity | None` (filesystem `Path` lookup using stable-ID filenames from Phase 1) and `lookup_by_slug(namespace_id, slug) -> list[RecordedEntity]` (returns ALL entities sharing the slug — the collision-aware return shape).
- `altk_evolve/frontend/mcp/mcp_server.py` — `get_entities` / `get_guidelines` switch to the new flow; behavior guarded by `EVOLVE_RECOGNITION_MODE` env var (`hybrid` default, `trigger_only` opt-in for early adopters who want to test, `legacy` for emergency rollback).
- `altk_evolve/cli/cli.py` — new commands:
  - `evolve recognition rebuild <namespace>` — rebuilds both indexes from MD.
  - `evolve recognition eval <namespace>` — runs the labeled benchmark and prints recall@k / NDCG@5 / P@5 against legacy and against each mode (hybrid, trigger-only, content-only).
  - `evolve recognition test <task>` — debug: prints top-K candidates with score breakdown (trigger vs content contribution).
- Cutover script: `scripts/migrate_to_md.py` — reads the legacy backend (Milvus/Postgres/filesystem-JSON), writes to the MD backend, validates entity counts, **detects slug collisions and aborts with a structured report unless `--allow-merge` is passed**. Uses stage-to-`.staging/` + atomic-promote pattern (OpenClaw's `rem-backfill --stage-short-term --rollback`).

**Reuse pointers:**
- `altk_evolve/llm/guidelines/clustering.py:_get_sentence_transformer` — reuse this cache for both trigger and content embeddings (same model unless we deliberately diverge).
- `altk_evolve/backend/milvus.py:196–270` — index-creation + content-encoding pattern for SentenceTransformer integration; the new `content_index.py` mirrors this without the Milvus collection dependency, writing into SQLite-VSS instead.
- `altk_evolve/sync/phoenix_sync.py` — when guidelines are ingested, the new pipeline writes MD AND updates both indexes. **Note: this is NOT a single atomic step.** A namespace lock does not make filesystem-replace + two SQLite-index mutations atomic as a unit. See "Index consistency" below for the derived-state recovery model that handles partial-failure cases (Codex review round 2 §5 finding).

**Index consistency (added after Codex review round 2 §5; tightened after round 3 §2):**

Indexes are treated as **derived state with explicit recovery**, not as part of the source-of-truth atomic write. Recovery must close the "MD-written but absent from both indexes" gap, which a candidate-level generation check does NOT detect (kNN never returns a candidate it doesn't know about).

1. **Order of writes inside the namespace lock:** (a) MD file via temp-file + `os.replace` (atomic, single file), then (b) trigger-index update, then (c) content-index update. Each step is independently atomic; the sequence is not.
2. **Per-entity generation:** every MD file's frontmatter includes `index_generation: int` (monotonic, namespace-scoped, incremented on each write). Each index records its `applied_generation` per `entity_id`.
3. **Per-namespace manifest** (the round-3 fix). The backend maintains `evolve_memory/.indexes/manifest.json` carrying per-namespace high-watermarks:
   ```json
   {
     "namespaces": {
       "default": {
         "md_high_watermark": 12847,           // max index_generation across all MD files
         "md_entity_count": 5234,              // count of MD files in namespace
         "md_checksum": "sha256:abc...",       // checksum of (entity_id, index_generation) tuples sorted
         "trigger_index": { "applied_namespace_generation": 12847, "entity_count": 5234, "checksum": "sha256:..." },
         "content_index": { "applied_namespace_generation": 12847, "entity_count": 5234, "checksum": "sha256:..." },
         "updated_at": "2026-05-15T..."
       }
     }
   }
   ```
4. **Pre-kNN namespace check (THE crucial guard):** before any `recognize()` call, compare for the target namespace:
   - `index.applied_namespace_generation == md_high_watermark` AND
   - `index.entity_count == md_entity_count` AND
   - `index.checksum == md_checksum`
   If any of the three diverges, the namespace is **stale**. Stale → rebuild synchronously OR fall back to direct MD scan + on-the-fly embedding for the namespace until rebuild completes. Manifest is updated atomically (temp-file + replace) at the end of every successful indexing operation.
5. **Lazy detection on individual entities** (the original mechanism, kept as a second line of defense): if an index returns a candidate whose `applied_generation` is behind the MD file's `index_generation`, mark the namespace stale and trigger recovery.
6. **Crash tests required (`tests/integration/test_index_recovery.py`):**
   - State A: `(MD-written, neither-index-updated, manifest-not-updated)` — namespace check must catch it pre-kNN.
   - State B: `(MD-written, trigger-only-updated, content-not, manifest-not-updated)` — namespace check via `content_index.entity_count < md_entity_count`.
   - State C: `(MD-written, both-indexes-updated, manifest-not-updated)` — manifest checksum mismatch triggers rebuild on next startup.
   - State D: `(MD-written, both-indexes-updated, manifest-updated, but checksum drift)` — periodic checksum verification fires alarm.
7. **Bounded staleness:** alarm fires if any namespace stays stale > N minutes (default 5). Manifest staleness check runs on a 1-minute background tick.
8. **Mechanically checkable invariant:** `md_high_watermark == applied_namespace_generation` for each (namespace × index) pair. Violations are first-class metrics in `durable_metrics.py` (see Phase 2).

**A/B validation period (revised — labeled benchmark, not divergence-only):**
- Run the labeled retrieval benchmark on hybrid recognition vs legacy Milvus retrieval against a fixed eval set. Required gates to flip primary:
  - **recall@10 ≥ 95% of legacy's recall@10** on the eval set
  - **NDCG@5 within 5% of legacy's NDCG@5**
  - **No regression** on the team-curated "critical guideline" review set (every critical guideline must still be retrievable for its expected task descriptions)
- Plus shadow-mode logging of divergent queries during the 1–2 week period for qualitative review.

**Files retired (config-only deprecation, NOT deletion):**
- Milvus/Postgres backends remain selectable via `EVOLVE_BACKEND` for users who explicitly want a vector-backend-as-source-of-truth path (escape hatch from the MD-as-truth direction itself). Deprecation warning logged at startup; documentation flagged as deprecated.

**Rollback consistency strategy (added after Codex review round 2 §2; hardened after round 3 §3):**

The legacy backend's rollback safety depends on its data being current. `dual_write_active=true` was insufficient because it asserted *configuration*, not *consistency* — a single failed legacy write opens a stale-rollback window. We require **durable outbox semantics + explicit `legacy_rollback_safe` flag** that is true only when no pending writes exist AND a recent drift-check passes.

**Durable outbox for legacy dual-write (the round-3 fix):**

- During dual-write, every MD write enqueues a corresponding legacy write into a persistent outbox at `evolve_memory/.outbox/legacy_writes.sqlite` (single SQLite table: `id, entity_id, operation, payload_json, enqueued_at, ack_at, attempts`).
- A small worker thread drains the outbox: applies each pending write to the legacy backend; on success, sets `ack_at`; on failure, increments `attempts` and retries with exponential backoff (capped). The outbox survives process restarts.
- Two derived flags, both file-backed in `evolve_memory/.indexes/manifest.json`:
  - `pending_legacy_writes: int` — count of unacked outbox entries.
  - `last_drift_check_passed_at: datetime` — when the drift-check job last ran successfully.
  - `legacy_rollback_safe: bool` — `True` iff `pending_legacy_writes == 0` AND `(now - last_drift_check_passed_at) < drift_check_max_age_seconds` (default 3600).

**Two regimes, both supported, with explicit mechanical invariants:**

- **Bake-in window dual-write (Weeks 1–4 post-cutover; default):** every MD write enqueues a legacy write into the outbox; daily drift-check (`scripts/legacy_drift_check.py`) compares entity counts and a sample of full records against MD source of truth. Outcome aggregation, promotions, and edits all enqueue legacy writes. Rollback safe iff `legacy_rollback_safe == True` (mechanical invariant), not merely if `dual_write_active`.
- **Post-bake-in rollback (Week 5+):** dual-write turns off after the bake-in window completes. Rollback requires running `scripts/rebuild_legacy_from_md.py <namespace>` first — reads MD as source of truth and re-populates the legacy backend with everything (including post-cutover writes). Sets `legacy_rebuild_completed_at` upon checksum match against MD. Only then is the env flip safe.

**Startup-time invariant check (refused otherwise):**

If `EVOLVE_RECOGNITION_MODE=legacy` is set, the client **refuses to start** unless one of:
- (a) Bake-in: `legacy_rollback_safe == True` (i.e. zero pending outbox writes AND fresh drift-check), OR
- (b) Post-bake-in: `legacy_rebuild_completed_at` exists, is younger than `legacy_staleness_max_seconds` (default 3600), AND no MD writes have occurred since.

The refusal includes a structured reason in the error message so operators can fix the root cause. **Mechanically checkable; not aspirational.**

**Tests:**
- `tests/integration/test_rollback_freshness.py` — make post-cutover writes, attempt `EVOLVE_RECOGNITION_MODE=legacy`, assert refuse-to-start; run the rebuild; retry the env flip; assert reads succeed and match the MD source-of-truth state.
- `tests/integration/test_legacy_outbox.py` — simulate legacy backend offline; verify MD writes succeed, outbox grows, `legacy_rollback_safe = False`; bring legacy online, verify outbox drains, `legacy_rollback_safe` returns to True.
- `tests/integration/test_pending_write_rollback_block.py` — with one pending write in outbox, assert that startup in legacy mode is refused even though `dual_write_active = True`.

**Tests:**
- Recognition unit tests for both indexes + the hybrid blend.
- Labeled benchmark must run as part of CI on a small held-out fixture; full benchmark against production-scale data run manually before cutover.
- Migration script: dry-run mode + checksum validation against source backend; collision-detection test (synthetic colliding slugs aborts as expected).
- Regression: full Phoenix sync E2E now uses MD as source; entity counts and retrieval results match against pre-cutover fixture.

**Exit criteria:**
- Both trigger and content indexes cover 100% of existing guidelines; rebuilt successfully from MD.
- Labeled benchmark gates passed (recall@10 ≥ 95% legacy, NDCG@5 within 5%, no critical-guideline regression).
- Migration script run + verified on a copy of production data; rollback tested; collision-detection works.
- Vector backends still selectable via env var (escape hatch from MD-as-source verified).
- `EVOLVE_RECOGNITION_MODE=legacy` flag works as a one-flag emergency rollback if an unforeseen regression surfaces post-cutover.

---

## 8. Phase 4 — Authority split + promotion + pre-compaction flush (2–4 weeks)

**Goal:** add the curation surface. Authoritative vs generated layer for guidelines; canonical opt-in for facts. Plugin pre-compaction-flush hooks. This is the phase that touches plugins.

**Files to create:**
- `altk_evolve/promotion/__init__.py`
- `altk_evolve/promotion/promoter.py` — implements promotion: copy a `generated/` MD file to `authoritative/` (or move; configurable), record `promoted_by`, `promoted_at`, `promotion_reason` in YAML frontmatter, update outcome-resolution behavior so authoritative wins ties.
- `altk_evolve/promotion/curation_tools.py` — utility: list candidates by score threshold, by recall-frequency threshold, by query-diversity threshold (per OpenClaw pattern).
- `altk_evolve/frontend/api/promotion_routes.py` — FastAPI routes for the curation UI: list pending candidates, promote, reject (annotate with reason).
- `frontend/ui/` — minimal additions: a "Curation" tab listing pending candidates with one-click promote (deferred to UI sprint if React work is heavier than expected; CLI-first acceptable).
- `tests/unit/promotion/test_promoter.py`
- `tests/e2e/test_authority_split_e2e.py`

**Files to modify:**
- `altk_evolve/backend/markdown.py` — directory layout shifts (preserving the stable-ID invariant from Phase 1; authority + category come from PATH, identity stays in FILENAME):
  - `evolve_memory/guidelines/{ns}/authoritative/{cat}/{stable_id}.md` (new)
  - `evolve_memory/guidelines/{ns}/generated/{cat}/{stable_id}.md` (was Phase 1's `guidelines/{ns}/{cat}/{stable_id}.md`)
  - `evolve_memory/facts/{ns}/canonical/{domain}/{stable_id}.md` (opt-in, new)
  - `evolve_memory/facts/{ns}/{domain}/{stable_id}.md` (existing)
- The trigger slug remains in YAML frontmatter only; the trigger index resolves `slug → list[entity_id]`. Promotion (move from `generated/` → `authoritative/`) **preserves the stable_id**; ONLY the path changes. This addresses Codex review (round 2) §1 finding — stable-ID invariant is maintained through every phase.
- `altk_evolve/llm/conflict_resolution/guideline_resolver.py` — authoritative wins generated; ties annotated.
- `altk_evolve/recognition/recognizer.py` — authoritative loaded eagerly into a small in-memory index; generated served lazily.
- `altk_evolve/cli/cli.py`:
  - `evolve guideline promote <trigger-slug-or-id>` — accepts a trigger slug OR a stable_id. If a slug resolves to multiple entity_ids (the collision-aware return shape), the command **errors with a disambiguation list and requires `--id <stable_id>`**. Solo matches promote in one shot. Promotion is a path-move, not a regenerate; stable_id is preserved.
  - `evolve guideline list-candidates`

**Pre-compaction flush hooks (per-plugin, small):**
- `platform-integrations/{claude,codex,bob,claw-code}/plugins/evolve-lite/skills/evolve-lite/flush/SKILL.md` — new skill exposing a tool the plugin host calls when context compaction is imminent.
- Hook implementation: small Python wrapper that calls a new MCP tool `flush_pending_extraction` which kicks off targeted extraction over the recent trajectory window before compaction collapses it.
- New MCP tool: `altk_evolve/frontend/mcp/mcp_server.py` add `flush_pending_extraction(session_id, lookback_seconds)`.

**Reuse pointers:**
- Existing skill structure: `platform-integrations/claude/plugins/evolve-lite/skills/evolve-lite/save-trajectory/`. Pattern is static SKILL.md + Python script invoking the MCP API.
- `altk_evolve/sync/phoenix_sync.py` — extraction logic reused for the flush path.

**Tests:**
- Unit: promotion semantics, conflict-resolution authoritative-wins behavior.
- E2E: write generated, promote, verify retrieval prioritizes authoritative; verify generated is annotated, not deleted.
- Plugin integration: simulated compaction signal triggers `flush_pending_extraction`; verify entities materialize before the compaction window closes.

**Exit criteria:**
- At least one promotion happens via CLI in a smoke-test scenario.
- Conflict resolution honors authoritative-wins on a contrived contradiction test.
- Pre-compaction flush demonstrably captures content that legacy extraction would have missed (compare entity counts across a long-session simulation, with and without flush).
- Promotion review surface (CLI minimum, UI ideal) operational.

---

## 9. Phase 5 — Situational linking + proactive sidecar (2–4 weeks)

**Goal:** complete the moat features. A-MEM-style guideline linking; `EvolveWatcher` sidecar for proactive mid-session injection.

**Files to create:**
- `altk_evolve/linking/__init__.py`
- `altk_evolve/linking/linker.py` — at guideline write-time: find adjacent guidelines (same category + overlapping tools + similar trigger embedding), update `related: [...]` in YAML frontmatter on both sides (symmetric linking).
- `altk_evolve/recognition/recognizer.py` — extend retrieval to optionally expand 1 hop along `related` when direct lookup returns < min_results.
- `altk_evolve/watcher/__init__.py`
- `altk_evolve/watcher/evolve_watcher.py` — long-running process subscribed to Phoenix span stream; on each new tool-call event, runs lightweight trigger recognition and emits proactive injection events to subscribed plugins via WebSocket / SSE.
- `altk_evolve/watcher/event_stream.py` — event-emission API consumed by plugins.
- Plugin updates: `platform-integrations/{platform}/plugins/evolve-lite/skills/evolve-lite/proactive/SKILL.md` (new) — listens to the watcher event stream and surfaces guidelines mid-session.

**Files to modify:**
- `altk_evolve/llm/guidelines/clustering.py` — when a new guideline is generated, after clustering, invoke `linker.link_new_guideline()` to compute and persist `related` links.
- `altk_evolve/cli/cli.py` — `evolve linking rebuild <namespace>` (one-time linking pass over existing corpus).
- `altk_evolve/cli/cli.py` — `evolve watcher start` / `evolve watcher stop` daemon controls.

**Reuse pointers:**
- `altk_evolve/llm/guidelines/clustering.py:cluster_entities` — same cosine-similarity logic; linker reuses but exits earlier (no merging, just edge creation).
- `altk_evolve/sync/phoenix_sync.py:_fetch_spans` — pagination logic can be reused / adapted for the watcher's tail-stream pattern.

**Tests:**
- Linking semantics: bidirectional link creation; idempotent re-runs; broken-link detection (target missing).
- Watcher integration: simulated span stream produces expected proactive events; subscribed plugin receives them within latency budget.

**Exit criteria:**
- Existing corpus has `related` populated; manual review confirms links are situationally meaningful.
- `EvolveWatcher` runs against a live Phoenix endpoint in dev environment; produces proactive events on tool-call patterns.
- At least one platform plugin (Claude) consumes the event stream end-to-end.

---

## 10. Critical files (master list)

**New files (created across phases):**
- `altk_evolve/backend/markdown.py`
- `altk_evolve/backend/_md_serialization.py`
- `altk_evolve/config/markdown.py`
- `altk_evolve/schema/outcome_evidence.py`
- `altk_evolve/llm/outcome_extraction/{__init__,tool_signals,trajectory_shape,aggregator}.py`
- `altk_evolve/llm/conflict_resolution/guideline_resolver.py`
- `altk_evolve/telemetry/{__init__,retrieval_log,outcome_aggregation_job,durable_metrics}.py`
- `scripts/legacy_drift_check.py` — bake-in window dual-write parity check
- `scripts/rebuild_legacy_from_md.py` — post-bake-in legacy rebuild for safe rollback
- `tests/integration/test_index_recovery.py` — crash-state recovery for MD/index divergence (4 states A–D)
- `tests/integration/test_rollback_freshness.py` — refuse-to-start invariant for stale legacy
- `tests/integration/test_legacy_outbox.py` — durable outbox semantics for pending legacy writes
- `tests/integration/test_pending_write_rollback_block.py` — `legacy_rollback_safe` invariant blocks rollback while writes are pending
- `tests/fixtures/recognition_eval/content_priority_cases.jsonl` — content-only relevance test set; cutover blocker
- `evolve_memory/.outbox/legacy_writes.sqlite` — durable outbox (created at runtime; not in repo)
- `evolve_memory/.indexes/manifest.json` — per-namespace high-watermark manifest (created at runtime)
- `altk_evolve/recognition/{__init__,trigger_index,content_index,index_builder,recognizer}.py`
- `altk_evolve/recognition/eval/{__init__,labeled_benchmark}.py`
- `tests/fixtures/recognition_eval/` — labeled `(task → expected_relevant_guideline_ids)` set
- `altk_evolve/promotion/{__init__,promoter,curation_tools}.py`
- `altk_evolve/frontend/api/promotion_routes.py`
- `altk_evolve/linking/{__init__,linker}.py`
- `altk_evolve/watcher/{__init__,evolve_watcher,event_stream}.py`
- `scripts/migrate_to_md.py`
- `design_doc/markdown_schema.md`
- Per-phase tests under `tests/unit/`, `tests/e2e/`, `tests/integration/`.
- Per-platform skills: `platform-integrations/{claude,codex,bob,claw-code}/plugins/evolve-lite/skills/evolve-lite/{flush,proactive}/SKILL.md`.

**Modified files:**
- `altk_evolve/config/evolve.py` (add `markdown` to backend Literal; add shadow env var)
- `altk_evolve/frontend/client/evolve_client.py` (factory branch + retrieval rewrite + telemetry decorators)
- `altk_evolve/frontend/mcp/mcp_server.py` (telemetry wrappers + flush + proactive tools)
- `altk_evolve/cli/cli.py` (multiple new subcommands)
- `altk_evolve/sync/phoenix_sync.py` (outcome extraction integration)
- `altk_evolve/schema/guidelines.py` (outcome_evidence + related fields)
- `altk_evolve/llm/conflict_resolution/conflict_resolution.py` (per-track dispatch)
- `altk_evolve/llm/guidelines/clustering.py` (linker integration)

---

## 11. Recognition-mode graduation gates (revised after Codex review)

The post-review plan ships **hybrid recognition** (trigger + content embeddings) as the Day-1 default. The graduation question now flips: when do we *simplify* to trigger-only-primary by retiring the content index? And conversely, when does the team conclude that trigger-only is unworkable at all and the content index becomes load-bearing forever?

These gates are evaluated on telemetry from Phase 2+ over rolling 4-week windows. They write to `evolve_memory/telemetry/recognition_mode_report_{week}.md`.

### Gates to graduate FROM hybrid TO trigger-only-primary (simplification)

We retire the content index and run trigger-only-primary if **all** of the following hold sustained:

| Gate | Metric | Threshold |
|---|---|---|
| **Content rarely contributes** | `% of injection events where the top-ranked guideline came from the trigger index AND the agent's subsequent action confirmed it was correct` | > 90% over 4 weeks |
| **Content fallback unused** | `% of injection events where the top result from content_index is NOT in trigger_index's top-K and was confirmed correct by post-hoc analysis` | < 5% over 4 weeks |
| **Trigger-only benchmark** | `recall@10 in trigger-only mode against the labeled benchmark` | ≥ 95% of hybrid mode's recall@10 |
| **Cold-start health** | `New guidelines (< 7 days old) achieve same retrieval-rate as 30-day-old peers under matched category/trigger conditions` | Within 10% |

If all four hold, the team can opt to retire the content index for the operational simplicity win. Until then, **hybrid is primary**.

### Gates to revert FROM hybrid TO legacy vector-backend (rollback)

We `EVOLVE_RECOGNITION_MODE=legacy` (and treat MD-as-source-of-truth itself as in question) if any of these fire:

| Gate | Metric | Threshold |
|---|---|---|
| **Hybrid loses to legacy on recall** | `recall@10 of hybrid mode vs the legacy Milvus content embedding` | < 90% sustained |
| **Critical-guideline regression** | `Number of curated "critical" guidelines that the legacy backend retrieves but hybrid does not` | > 0 sustained |
| **Hot-path latency regression** | `p95 latency of EvolveClient.search_entities` | > 1.5× legacy p95 sustained |

Any one of these triggers a rollback decision; the env-var flip restores the legacy path while the team investigates.

### Implementation note

Gates evaluate on **durable counters + histograms** from `altk_evolve/telemetry/durable_metrics.py` (Phase 2) plus the labeled benchmark harness (Phase 3). Gates do **NOT** evaluate on the best-effort JSONL retrieval log — that pipeline is lossy by contract and the lossiness biases evaluation under exactly the stress conditions the gates care about. The lossy log is for qualitative review only.

Gate evaluation rule (added after Codex review round 2 §4): if `evolve_telemetry_dropped_total / evolve_retrieval_total > 5%` over the evaluation window, the gate **fails open** — no graduation or rollback decision is made until telemetry health is restored. Alert raised.

Aggregation derives the metrics weekly. The recommended stance for the first 4–8 weeks post-cutover: **hybrid is locked as primary**; do not graduate prematurely. The simplification gate is a future option, not an early goal.

---

## 12. Verification (end-to-end)

**Continuous (every PR):**
- `pytest tests/unit -m "not llm and not e2e"` — fast unit suite.
- `ruff check altk_evolve tests` — lint.
- `mypy altk_evolve` — type check.

**Per-phase exit:**
- Phase 1: `pytest tests/unit/test_markdown_backend.py` + `pytest tests/e2e/test_md_backend_e2e.py` pass; nightly `dual-write-verify` reports zero drift for 7 days.
- Phase 2: `pytest tests/unit/llm/test_outcome_extraction.py` + `pytest tests/e2e/test_outcome_e2e.py` pass; `evolve outcome backfill` populates ≥50% of guidelines in dev namespace.
- Phase 3: A/B divergence < 10% over a 7-day window; migration script run on staging snapshot; `EVOLVE_BACKEND=milvus` still works (escape-hatch verified).
- Phase 4: smoke test of `evolve guideline promote` succeeds; conflict-resolution authoritative-wins test passes; flush hook captures additional entities in long-session simulation.
- Phase 5: linking re-run is idempotent; `EvolveWatcher` produces proactive events against live Phoenix endpoint.

**Production rollout (after Phase 3 cutover):**
- Stage 1: deploy MD backend to staging with `EVOLVE_BACKEND=markdown`; run for 1 week.
- Stage 2: shadow-mode in production (writes mirrored, reads still legacy) for 2 weeks.
- Stage 3: flip primary to MD; legacy backend becomes read-only standby for 2 weeks.
- Stage 4: drop legacy backend (config-only retirement).

**Rollback procedure:**
- Phase 3 migration script supports reverse-replay (stage MD to a fresh legacy backend).
- `EVOLVE_BACKEND` env-var flip restores legacy in seconds.
- Worst case: revert to commit `c816252` (current `file-based-memory` HEAD before any code lands); design doc remains as the canonical record.

---

## 13. Risks & mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Cross-process write races corrupt MD files | High | `fcntl.flock` per-namespace + atomic temp-file replace (Phase 1) |
| LLM rewrites lose content during conflict resolution | High | Diff-size guardrail in `guideline_resolver.py` (Phase 2); reject rewrites > N% diff and fall back to structured ADD/UPDATE/DELETE |
| **Trigger-slug collisions silently overwrite guidelines** *(Codex review §3)* | **High** | **Stable ID as filename from Phase 1; slug stored in frontmatter only; collision-aware trigger index returns `list[entity_id]`; migration script aborts on collision unless `--allow-merge`. NOT deferred to Phase 5.** |
| **Telemetry on hot path adds latency or blocks reads** *(Codex review §2)* | **High** | **Async bounded queue + 50ms timeout + fail-isolated catch-all in `retrieval_log.py` (Phase 2). Chaos tests prove retrieval succeeds when telemetry path is unwritable / disk-full / blocked.** |
| **Hybrid recognition under-retrieves vs legacy on labeled eval** *(Codex review round 1 §1)* | **High** | **Labeled recall@10 + NDCG@5 benchmark (Phase 3 gate) blocks cutover; `EVOLVE_RECOGNITION_MODE=legacy` env var as emergency rollback under explicit freshness invariant (Phase 3 rollback strategy).** |
| **Phase 4 silently re-introduces slug-as-filename, breaking Phase 1 invariant** *(Codex review round 2 §1)* | **High** | **Phase 4 directory layout uses `{stable_id}.md` under authority/category subdirs; promotion is path-move only, stable_id preserved; CLI requires `--id` disambiguation on slug collision.** |
| **Rollback to legacy returns stale data after post-cutover writes** *(Codex review round 2 §2)* | **High** | **Bake-in dual-write window with daily drift check; post-bake-in rollback requires `rebuild_legacy_from_md.py` first; refuse-to-start invariant when legacy is stale.** |
| **Alpha-blend score fusion suppresses content fallback under uncalibrated scales** *(Codex review round 2 §3)* | **High** | **Default to Reciprocal Rank Fusion (RRF) — calibration-free; explicit content-priority test cases in benchmark; alpha-blend retained as opt-in mode only.** |
| **Lossy telemetry biases gate evaluation under stress** *(Codex review round 2 §4)* | **Medium** | **Separate durable metrics pipeline (`durable_metrics.py`) with counters/histograms outside the lossy queue; gate fails open if drop-rate > 5% over evaluation window.** |
| **MD + 2 indexes are not actually atomic; crash leaves divergent state** *(Codex review round 2 §5)* | **Medium** | **Indexes treated as derived state with explicit `index_generation` tracking; rebuild-on-stale at retrieval; bounded-staleness alarm; crash tests for partial-failure states.** |
| **Architecture diagram contradicts stable-ID invariant (silent contract leak)** *(Codex review round 3 §1)* | **High** | **§3 storage diagram updated to `{stable_id}.md` for all paths; explicit "filename = stable_id, slug in frontmatter" contract documented; consistency check before implementation.** |
| **Recovery cannot detect MD-written entities missing from both indexes** *(Codex review round 3 §2)* | **High** | **Per-namespace high-watermark manifest (`md_high_watermark`, `entity_count`, `checksum`); pre-kNN namespace check; mechanical invariant `md_high_watermark == applied_namespace_generation` exposed as a metric.** |
| **`dual_write_active=true` admits stale legacy reads on legacy-write failure** *(Codex review round 3 §3)* | **High** | **Durable outbox for pending legacy writes; `legacy_rollback_safe = (pending == 0) AND (drift-check fresh)`; refuse-to-start in legacy mode unless mechanically safe.** |
| **Fixed RRF weights mathematically suppress content fallback** *(Codex review round 3 §4)* | **Medium** | **Default unit weights (1.0/1.0); weights config-gated; content-priority fixture is a cutover blocker; benchmark-driven tuning required if weights diverge from 1.0.** |
| Telemetry log grows unbounded | Medium | Daily aggregation job rolls up + truncates raw log to last 7 days (Phase 2) |
| Plugin compaction-flush hook latency degrades agent UX | Medium | Flush capped at small lookback window; runs async with timeout |
| Hybrid recognition retrieval is slower than legacy | Medium | §11 latency gate (p95 < 1.5× legacy); two indexes parallelizable; SQLite-VSS chosen for in-process speed |
| Migration script corrupts production data | High | Stage-then-promote pattern; checksum validation; rollback path verified before Phase 3 cutover; collision-detection on slug |
| Backend abstraction split causes unforeseen test breakage | Medium | Phase 1 keeps existing backends fully functional; cutover only happens at Phase 3 with A/B validation |

---

## 14. Rough timeline

| Phase | Duration | Calendar (assuming start 2026-05-19) |
|---|---|---|
| 0 | 3–5 days | Week 1 |
| 1 | 2–3 weeks | Weeks 2–4 |
| 2 | 2–3 weeks | Weeks 5–7 |
| 3 | 2–3 weeks | Weeks 8–10 |
| 4 | 2–4 weeks | Weeks 11–14 |
| 5 | 2–4 weeks | Weeks 15–18 |

Total: ~14–18 weeks (~3.5–4.5 months).

---

## 15. Open questions to resolve in Phase 0

These should be answered as part of Phase 0 (no code blocked on them yet):

1. **Outcome-evidence schema field set** — exact final list of fields. Initial proposal in §7.1.1 of the design doc.
2. **Canonical-tier mechanism for facts** — directory-based (recommended) vs frontmatter flag.
3. **Trigger-slug normalizer rules** — what's the canonical normalization (lowercase, snake_case, strip stopwords)?
4. **Embedding model for trigger index** — same as `clustering.py` SentenceTransformer? Or a smaller model for faster recognition?
5. **Recognition mode default** — embedding-only, llm-as-router, or dual? (Dual is more expensive; embedding-only may be enough to start.)
6. **Concrete gate thresholds in §11** — initial drafts above; team may adjust.
7. **`EvolveWatcher` deployment shape** — process per host, central daemon, or library?
8. **Linking re-run cadence** — every guideline write (eager) or daily background sweep (lazy)?

---

## 16. Reuse summary (don't reinvent)

Key code already in the repo that the new modules should call into:

- `altk_evolve/backend/filesystem.py` — full template for atomic-write + locking + `_active_data` patterns.
- `altk_evolve/backend/base.py:110–186` — `update_entities` template method; the new backend overrides hooks, not this.
- `altk_evolve/llm/conflict_resolution/conflict_resolution.py` — keep the litellm + JSON-parse + EntityUpdate-list pattern; just swap prompts per track.
- `altk_evolve/llm/guidelines/clustering.py:_get_sentence_transformer` — cached embedding model; reuse for trigger index.
- `altk_evolve/sync/phoenix_sync.py:_extract_trajectory` — message normalization; reuse for outcome extractors.
- `altk_evolve/utils/serialization.py:serialize_content` — content normalization.
- `tests/fixtures/appworld_venmo_task_trajectory.json` — anchor fixture for E2E across phases.
- `altk_evolve/schema/conflict_resolution.py:EntityUpdate` — return type that all conflict resolvers must produce.

---

## 17. What this plan deliberately does NOT decide

- The wiki layer (OpenClaw `memory-wiki`-style consolidated docs with PageIndex retrieval) — bookmarked as Phase 6+ candidate per design doc §8.5; not in this plan.
- Multimodal memory — out of scope.
- Mem0 / Memory Tool API outsourcing of facts — alternative path mentioned in design doc §7.2; not picked up in this plan.
- UI rebuild — minimal additions only; full UI redesign deferred.
- Cross-machine sync — scoped per-host today; remains so.
