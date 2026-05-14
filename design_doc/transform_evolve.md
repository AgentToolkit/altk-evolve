# Transforming Evolve: From Vector DB to Agentic Memory

**Status:** Discussion document — not a decision yet
**Date:** 2026-05-11
**Audience:** Evolve core team
**Goal:** Align on whether / how to transform Evolve from its current vector-DB-centric architecture into a modern agentic memory system, and pick one direction to pursue.

---

## 1. Why are we considering this?

Evolve today stores everything — trajectories, facts, guidelines — inside a vector database (Milvus / pgvector / filesystem-backed), with semantic similarity as the primary retrieval mechanism. That design has real costs:

- **Opaque storage.** Memories live in proprietary index formats. Humans cannot read, diff, or edit them without going through the UI or API.
- **No git workflow.** Guidelines cannot be reviewed, branched, or rolled back the way code is.
- **Vector-DB operational burden.** Milvus / pgvector are infrastructure we maintain and scale.
- **Industry drift.** Claude's managed memory, OpenAI Codex memories, and Zilliz's own Memsearch have all moved toward `.md`-first designs. Staying on a pure vector-DB stack risks looking dated and fighting the grain of where agent platforms are heading.

The question this document frames: **does Evolve reconstruct itself around `.md` files, and if so, how radically?**

### 1.1 What is Evolve's actual moat?

Before answering the architecture question, name the moat. **Evolve's differentiator is the guidelines track, not the facts track.**

- **Facts are commodity.** Every modern agent-memory system does facts: Mem0, Zep, Letta, Codex memories, Claude auto memory. Declarative knowledge ("the user prefers VSCode", "the API endpoint is X") is well-served by existing tools — using Mem0 or the Memory Tool API for facts is increasingly a "buy, don't build" decision.
- **Guidelines are uniquely Evolve.** Evolve mines **procedural rules from agent trajectories** with outcome context: *"in situation X, calling tool A then tool B succeeded; calling A alone failed"*. The output is a `trigger` + `implementation_steps` + `category` (strategy/recovery/optimization) — i.e. *how to do things, learned from what worked and what didn't*. None of the surveyed memory systems (§3) do this at trajectory-mining depth. Most carry "facts about users"; Evolve carries **"playbooks distilled from outcomes."**

This reframes the architecture question. Asymmetric investment is justified:
- **Guidelines deserve the deepest architectural investment** — this is the layer that earns the product its right to exist.
- **Facts can be commoditized** — adopt the simplest paradigm that works, defer complexity until telemetry proves it pays for itself, or outsource to a memory-tool-style backend.

The rest of this document keeps both tracks in scope, but §7's recommendation now treats them asymmetrically.

---

## 2. Current Evolve at a Glance

### 2.1 Two parallel memory tracks

Evolve is **not** a single fact→guideline pipeline. It maintains two parallel tracks drawn from classical memory-systems theory, and each track has its own extraction, storage, and retrieval profile:

| Track | Maps to | What it captures | Module | Today's usage profile |
|---|---|---|---|---|
| **Facts** | Semantic memory | Decontextualized, categorized declarative knowledge — "what is known" (tools, domains, capabilities, observed properties), extracted and typed via categorization | `llm/fact_extraction/` (incl. `categorization.py`) | Medium-volume after dedup/categorization, knowledge-base-like, retrieved by **similarity to a query or task context** ("what do I know about X?"), stable once extracted |
| **Guidelines** | Episodic / procedural memory | **Trajectory-mined procedural rules with outcome context**: each guideline carries a `trigger` (situational anchor) + `implementation_steps` (what to do — tool calls, sequences, behaviors) + `category` (strategy / recovery / optimization, where recovery encodes failure-learned playbooks). Distilled from real trajectories where tasks succeeded or failed, not declarative facts. Produced by segmenting trajectories and clustering similar episodes. **This is the moat (§1.1).** | `llm/guidelines/` (incl. `segmentation.py`, `clustering.py`) | Potentially high before clustering, reduced after; heavily curated; retrieved by **trigger-match + situational similarity** ("a situation like this just arose, what playbook worked last time?"); benefits from human review of strategic quality |

The mapping is straightforward once you see the schemas: facts are *decontextualized "knowing-that"* (semantic), while guidelines are *episode-anchored "knowing-when-and-what-to-do"* (episodic/procedural — the trigger IS the episode anchor).

> **Caveat (CoALA taxonomy).** The widely-cited [CoALA framework](https://www.langchain.com/blog/memory-for-agents) splits long-term memory into **three** types: semantic, episodic, and **procedural** (how to perform tasks — rules and instructions). Evolve's `Guideline` (`trigger` + `implementation_steps` + `category`) arguably fits *procedural* better than *episodic*. Episodic memory in CoALA is action-sequence replay (few-shot examples), not trigger-anchored playbooks. The team should explicitly decide whether guidelines are episodic or procedural — the choice changes who/what writes them: episodic memory is typically auto-extracted, while procedural memory is typically agent-rewritten or human-authored. The recommendation in §7 (pure files for guidelines) is robust to either choice.

The two tracks share capture and storage infrastructure, but their **access patterns, volume, and value to humans are fundamentally different**. A transformation that treats them identically is almost certainly wrong — the right answer may be a *different paradigm per track*.

### 2.2 Six cross-track responsibilities

Across both tracks, Evolve owns six responsibilities:

| # | Responsibility | How it works today |
|---|---|---|
| 1 | **Capture** | Phoenix spans stream agent trajectories into Evolve |
| 2 | **Extract (semantic)** | `llm/fact_extraction/` pulls facts from traces and categorizes them |
| 3 | **Extract (episodic)** | `llm/guidelines/` produces trigger-anchored guidelines (segmentation + clustering + synthesis) — runs in parallel to fact extraction, not downstream of it |
| 4 | **Resolve** | `llm/conflict_resolution/` merges contradictory *guidelines* (the episodic track) |
| 5 | **Store** | Pluggable backends (filesystem, PostgreSQL+pgvector, Milvus) under `BaseEntityBackend`; structured `Entity` / `Guideline` Pydantic schemas; both tracks currently share the same backend |
| 6 | **Inject** | Semantic search at agent-start finds relevant facts and/or guidelines; injected via platform plugins (Claude / Codex / Bob / Claw) |

Evolve's moat, stated bluntly: **trajectory-mined procedural playbooks (guidelines) — running rules with outcome context that no surveyed competitor produces.** The facts (semantic) track is real and useful, but it's table stakes; the guidelines track is what makes Evolve worth building. See §1.1 for why the architecture should treat them asymmetrically.

Any transformation must answer, *per track*: which of these six responsibilities stay, change, or disappear?

---

## 3. Prior Art — How the Industry is Doing This

Research summary from four reference systems. The pattern that emerges is more nuanced than "everyone uses `.md`" — each system explicitly separates **a human-authored authoritative layer** from **an LLM-generated recall layer**.

### 3.1 Anthropic — Claude Code (CLAUDE.md + Auto memory)

Claude Code ships **two complementary** memory systems, both loaded at session start:

**CLAUDE.md files (human-written authority):**
- Four scopes, most-specific wins: **managed policy** (`/etc/claude-code/CLAUDE.md`, org-wide), **project** (`./CLAUDE.md` or `./.claude/CLAUDE.md`, team-shared via VC), **user** (`~/.claude/CLAUDE.md`, all projects), **local** (`./CLAUDE.local.md`, gitignored).
- Walked up the directory tree and concatenated; subdirectory CLAUDE.md files load lazily when Claude reads files there.
- Imports via `@path/to/file` syntax (up to 5 hops). Imports load fully into context at launch.
- Companion: `.claude/rules/` directory of topic files with YAML frontmatter `paths:` globs for path-scoped rules.
- Recommended size: under 200 lines per file (longer files reduce adherence).

**Auto memory (Claude-written recall):**
- Location: `~/.claude/projects/<project>/memory/`, with a `MEMORY.md` index + topic files (`debugging.md`, `api-conventions.md`, etc.).
- **First 200 lines / 25 KB of `MEMORY.md` loaded every session**; topic files read on demand by Claude's own file tools.
- Claude decides what's worth saving based on corrections and preferences — not rule-based extraction.
- Machine-local; shared across worktrees of the same repo; no cross-machine sync.
- Configurable: `autoMemoryEnabled`, `autoMemoryDirectory`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY`.

**Philosophy:** Explicit authority split. CLAUDE.md is the *source of truth* humans maintain; auto memory is *emergent accumulation* the model maintains. They coexist; neither replaces the other.

### 3.2 Anthropic — Memory Tool API (client-side primitive)

The developer-facing memory primitive for agents built on the Claude API (separate from Claude Code):

- **Client-side storage:** the developer's app owns the files and their location (filesystem, DB, encrypted blob, cloud — all fine). Anthropic does not host anything.
- **Tool interface:** six commands — `view`, `create`, `str_replace`, `insert`, `delete`, `rename` — all scoped (by convention) to a `/memories/` directory. Path-traversal validation is the developer's responsibility.
- **Writing:** Claude decides autonomously via tool calls. A system-prompt instruction is auto-injected: *"ALWAYS VIEW YOUR MEMORY DIRECTORY BEFORE DOING ANYTHING ELSE… record status / progress / thoughts as you work."*
- **Retrieval:** agent calls `view /memories` first, then reads specific files. There is **no built-in semantic search** — retrieval IS navigation.
- **Pairs with context editing + compaction** to manage long conversations without losing durable state.
- **Eligible for Zero Data Retention** — data is not stored after the response returns.

**Philosophy:** give the model a filesystem with a narrow tool surface; let the developer control backing store.

### 3.3 Anthropic — Claude Managed Agents Memory (hosted service)

A **managed/hosted** layer on top of the memory-tool primitive:

- Anthropic hosts the memory files; developers get full programmatic export + API management.
- **Detailed audit log**, version rollback, content redaction, per-memory session/agent provenance.
- **Scope:** org-wide stores (often read-only) + per-user stores (read/write); multiple agents can work concurrently without conflicts.
- Surfaced in the Claude Console as session events for visibility.

**Philosophy:** same primitive as §3.2, with enterprise-grade governance bolted on.

### 3.4 OpenAI — Codex Memories

Codex also implements an explicit authority split:

**`AGENTS.md` (human-written authority):**
- Treated as the *authoritative* checked-in guidance file.
- Documentation is explicit: *"Keep required team guidance in `AGENTS.md` or checked-in documentation. Treat memories as a helpful local recall layer, not as the only source for rules that must always apply."*

**`~/.codex/memories/` (LLM-generated recall, off by default):**
- Parent configurable via `CODEX_HOME`. Four entry types: **summaries**, **durable entries**, **recent inputs**, **supporting evidence**.
- Treated as "**generated state**" — users can inspect but shouldn't hand-edit as a primary control surface.
- Generation is **asynchronous**: waits for thread idle (avoids summarizing work-in-progress), skips active/short-lived sessions, pauses under rate-limit pressure (`memories.min_rate_limit_remaining_percent`).
- **Opt-in**: off by default; enabled via app settings or `memories = true` in `config.toml`. **Not available in EEA / UK / Switzerland.**
- Per-thread control via `/memories` command: whether the thread can read existing memories, whether it can contribute to future memories.
- **Secrets are redacted** during generation.

**Philosophy:** conservative, privacy-first, explicitly complementary to `AGENTS.md`.

### 3.5 Zilliz — Memsearch

- **Storage:** `.md` files are **source of truth**; Milvus is an explicit "shadow index" — derived, rebuildable cache.
- **Writing:** Plugins capture each turn → LLM summarizes → appended to a daily `.md` file → indexer picks up change; SHA-256 content-addressed dedup skips unchanged chunks.
- **Retrieval (3-layer progressive recall):**
  - L1 — Hybrid search: dense vectors + BM25, fused via RRF
  - L2 — Expand around a chunk hash to fetch full MD section
  - L3 — Parse raw session transcripts for original dialogue
- **Philosophy:** Human-authorable + semantic — "`.md` files are human-readable, editable, version-controllable; the index is disposable."

### 3.6 Adjacent Systems Worth Knowing About

The four systems above were chosen because they're directly comparable to Evolve's scope. But the broader agent-memory landscape has a few more reference points whose architectural choices should at least be acknowledged:

- **[Letta / MemGPT](https://github.com/letta-ai/letta)** — virtual-memory paging metaphor. Treats the context window as "main memory" and external storage as "disk"; the agent itself decides what to page in via tools (core memory blocks always in context; recall and archival memory queried on demand). This is essentially the conceptual frame behind §3.2's Memory Tool. The lens makes one question vivid that this doc currently doesn't answer: **what is the per-session "core memory" budget for injected facts/guidelines, and what's the paging policy when budget is exceeded?**

- **[Zep (Graphiti)](https://help.getzep.com/concepts)** — temporal knowledge graphs with **fact invalidation**. Each factual edge carries valid-from / valid-to timestamps. When new info contradicts an old fact, the prior fact isn't overwritten — its valid-to is set. Agents can then reason about what *was* true vs *is* true now. **Evolve's facts (semantic) face exactly this problem and the rest of this doc is silent on it.** See §8 open questions.

- **[A-MEM (NeurIPS 2025)](https://arxiv.org/abs/2502.12110)** — Zettelkasten-style agentic note-linking. Each new memory triggers updates to *related* old memories, refining the network over time. The closest analog in Evolve is `cluster_entities` (one-shot embedding clustering), which is more rigid. A-MEM's pattern would handle the trigger-slug-collision problem (`auth_failed_401` vs `auth_failed_unauthorized`) by *linking* rather than forcing a canonical slug.

- **[Mem0](https://docs.mem0.ai/core-concepts/memory-types)** — production memory layer scoped by **lifetime/owner**: conversation < session < user < organizational. This is *orthogonal* to the human-vs-LLM authority axis in §4.3. A real system needs both: scope by owner/lifetime AND mark by authorship. Today this maps onto Evolve's `Namespace` + `visibility`; in an MD-tree paradigm it becomes directory layout.

- **[LangGraph / CoALA memory model](https://www.langchain.com/blog/memory-for-agents)** — the source for the three-type long-term memory taxonomy referenced in §2.1, and for the **hot-path vs background** extraction distinction (does the agent save memory mid-turn via tool, blocking the reply, or does a separate process write during/after the turn?). Evolve today is background-only (offline batch from Phoenix spans). The transformation should make this explicit. See §8.

- **[MemU (NevaMind-AI)](https://github.com/NevaMind-AI/memU)** — ~13k★, Apache 2.0, Python. The most directly comparable system to where Evolve is heading, and the most important reference to study. Three notable moves:
  1. **"Memory as filesystem" metaphor** at the interface (`memory/preferences/`, `memory/relationships/contacts/`, `memory/knowledge/domain_expertise/`). Underneath: PostgreSQL + pgvector. Files are the *mental model*, not the source of truth — a weaker version of our recommendation, which uses real MD files.
  2. **Three-layer hierarchy: `Resource` → `Item` → `Category`** (raw conversation/doc/image → extracted fact/preference/skill → auto-organized topical summary with cross-references). Evolve has the same shape implicitly (`Trajectory → Fact/Guideline → Category`). Adopting MemU's naming would clarify the schema for the team.
  3. **Two-process architecture** with a separate **MemU Bot** that proactively monitors agent I/O, predicts intent, and pre-fetches context mid-session — rather than only injecting at session start. Combined with **dual retrieval modes** (`method="rag"` for sub-second vector recall, `method="llm"` for reasoning + intent prediction). Reports **92.09% on the Locomo benchmark**.
  
  **What MemU validates:** the MD-style agentic-memory market is hot. 13k★ means we need to ship, not perfect.

- **[OpenClaw memory](https://docs.openclaw.ai/concepts/memory)** — production-grade `.md`-first memory system with several patterns we should absorb:
  1. **Three file tiers:** `MEMORY.md` (long-term curated, only written by deep promotion), `memory/YYYY-MM-DD.md` (working/daily raw notes), `DREAMS.md` (consolidation human-review surface). Their separation of "raw observation" → "ranked candidate" → "blessed durable" is more granular than our two-tier `generated/` vs `authoritative/`. We don't need the daily-file tier (Phoenix spans cover that), but the multi-stage promotion pipeline is a useful reference.
  2. **Disk-as-truth principle** — *"the model only 'remembers' what gets saved to disk — there is no hidden state."* Strong philosophical alignment with our recommendation; worth citing.
  3. **Promotion gates use implicit usage signals** — items must pass thresholds on **score + recall_frequency + query_diversity**. This is a signal source we hadn't considered: instead of (or alongside) explicit outcome signals, you can use *retrieval patterns themselves* as quality proxies. See §7.1.1 — promoted to a sixth signal source there.
  4. **Pre-compaction memory flush** — a silent turn that runs before context compaction, reminding the agent to save important state. Hot-path safeguard. Not in our doc previously; added as §7.1 feature 7.
  5. **`memory-wiki` plugin** — compiles durable memory into a wiki vault with structured claims/evidence, **contradiction tracking**, and **freshness tracking**. Existence proof that the temporal-validity question (§8) is implementable.
  6. **Pluggable indexing backends** (builtin SQLite, QMD, Honcho, LanceDB) over plain-MD substrate. Validates our SQLite-VSS choice for the trigger-only embedding index.
  7. **Grounded backfill** with `rem-backfill --stage-short-term` and `--rollback` for safe historical replay. Concrete migration-plan template — see §8.
  
  **What Evolve still wins on:** OpenClaw memories are user-facts/preferences/observations free-form — not trajectory-mined procedural rules with `trigger`+`implementation_steps`+`category`. Their proxy signals (recall frequency, query diversity) are useful but **complementary** to our explicit outcome signals — not a replacement. They have heartbeat/dreaming sweep but not a proactive sidecar that recognizes triggering situations mid-task.
  
  **What Evolve still wins on:** MemU is in the *facts/preferences/skills* camp (like Mem0/Letta/Zep). It has no first-class outcome metadata, no authority split (generated and curated co-mingle — same flaw as Memsearch), no trajectory-mined procedural rules with explicit `trigger`+`implementation_steps`, no conflict resolution as a documented primitive. The procedural-memory-with-outcome-context moat (§1.1) holds. But see §7.1 — MemU's proactive-bot pattern is genuinely novel and we should adopt it.

### 3.7 Common Themes Across the Surveyed Systems

1. **Authority split is universal.** Every system separates a *human-authored authoritative layer* (CLAUDE.md, AGENTS.md) from an *LLM-generated recall layer* (auto memory, Codex memories). Memsearch is the outlier — it conflates them into one MD store — and this is worth noting when we compare to Evolve.
2. **MD is the medium for both layers.** Everyone converged on markdown as the shared surface for humans and agents.
3. **Retrieval divides sharply into two camps.**
   - **Navigation-only** (Claude Code auto memory, Memory Tool, Codex): agent uses file-ops or directory listing; no vector search.
   - **Hybrid / indexed** (Memsearch, conceptually Claude Code's subdirectory lazy-load): MD as source + a derived retrieval layer.
4. **Privacy + regionality matter.** Codex is opt-in and region-restricted; Memory Tool is ZDR-eligible. Any Evolve redesign should account for these as first-class concerns, not afterthoughts.
5. **Conservative generation beats aggressive.** Both Claude's auto memory ("Claude doesn't save something every session") and Codex ("waits until idle") explicitly avoid over-capturing. This is a direct rebuke to systems that extract from every trajectory unconditionally.
6. **Size discipline is enforced.** CLAUDE.md recommends <200 lines per file; auto memory's `MEMORY.md` is capped at the first 200 lines / 25 KB for context loading. Evolve should plan for bounded-size indexes, not unbounded accretion.
7. **Procedural memory at trajectory-mining depth is an open frontier.** Every system surveyed handles facts well. Procedural memory — *trajectory-mined "what tool, what sequence, what worked vs failed" rules* — is barely addressed. Claude auto memory captures informal "build commands and debugging insights"; LangGraph/CoALA names procedural as a category but the dominant pattern there is "agent rewrites its own system prompt" (not trajectory-mined extraction); A-MEM's note-linking is closest in spirit but operates on free-form notes, not structured trigger+steps+outcome playbooks. **This is exactly Evolve's territory** (§1.1). Architectural decisions on the guidelines track should be made with the assumption that we are inventing the design, not adopting one.

---

## 4. The Three Paradigms for New Evolve

We frame three concrete options. They differ on one load-bearing axis: **what happens to the vector DB and semantic retrieval?**

Because Evolve has **two parallel tracks** (episodic facts + semantic guidelines), each paradigm below is evaluated *per track*. A paradigm may be a strong fit for one track and a poor fit for the other — that asymmetry is the most important thing to internalize from this section.

### Paradigm A — Pure File-Based (Claude-style)

- `.md` files are the only store. Zero embeddings, zero Milvus, zero pgvector.
- Retrieval is by **filesystem convention**: hierarchical directory structure (`{track}/{namespace}/{topic}/*.md`) plus static injection (load a subtree) or agent-driven read/grep.
- Evolve's extraction pipelines still run, but output `.md` files in place of DB rows.
- Conflict resolution (semantic track) operates **at the file level** — one file per topic, LLM rewrites it in place.

**Per-track fit:**
- *Guidelines (episodic):* **Strong fit.** Trigger-anchored playbooks map cleanly onto a directory structure like `guidelines/{category}/{trigger-slug}.md`. Retrieval is trigger-match, not similarity — filesystem lookup does the job. Heavy human curation is exactly what pure files enable.
- *Facts (semantic):* **Weaker fit.** The natural query is "what do I know about X?" — a similarity question. Without embeddings, you fall back on categorization and grep, which miss latent conceptual matches ("auth" vs. "authentication" vs. "JWT middleware").

**Wins:** Radical simplicity. Drop half the stack. Full git workflow. No embedding costs.
**Losses:** On the semantic (facts) track, similarity-based recall collapses unless agents are expected to do clever multi-term grep.
**Effort:** Medium. Pipelines stay; storage + retrieval rewritten.

### Paradigm B — Hybrid (MD source of truth + shadow vector index)

- `.md` files are **source of truth**. A vector index (Milvus or pgvector) is rebuildable from the MD tree.
- File watcher detects MD changes → re-indexes affected chunks → SHA-256 dedup avoids redundant embedding calls.
- Retrieval keeps today's semantic injection, augmented by BM25 and chunk-expand (Memsearch's 3-layer model).
- Extraction + conflict-resolution pipelines output MD; index follows.

**Per-track fit:**
- *Guidelines (episodic):* **Good fit, but arguably overkill.** Guidelines are retrieved by trigger-match, not similarity — the shadow index is doing work a directory lookup could do for free.
- *Facts (semantic):* **Strong fit.** Similarity search is exactly the retrieval pattern for "what do I know about X?" — flipping MD to source of truth preserves this while adding human-editability + auditability.

**Wins:** Preserves Evolve's semantic-similarity moat where it matters most (facts). Humans can edit MD directly; index catches up.
**Losses:** Complexity stays — we still operate Milvus/pgvector, plus add file watcher + re-indexer. It's an *evolution*, not a *revolution*.
**Effort:** High. Every layer changes; new file watcher + re-indexer subsystems.

### Paradigm C — Agent-Managed Memory (LLM-curated at runtime)

- Evolve becomes a minimal **runtime memory service**. `.md` files are written by the *agent itself* during/after sessions — not by centralized extraction pipelines.
- Evolve's role shrinks to: hosting the memory store, providing read/write/list APIs, enforcing namespacing and visibility.
- Fact extraction, guideline synthesis, and conflict resolution all become **optional** (or removed entirely). Agents decide what's worth remembering.

**Per-track fit:**
- *Guidelines (episodic):* **Viable.** Agents maintaining their own trigger-anchored playbooks is directly analogous to how Claude/Codex treat `AGENTS.md` / `CLAUDE.md` today.
- *Facts (semantic):* **Awkward.** A centralized knowledge base benefits from accretion across *many* agents — per-agent fact stores fragment the corpus and lose the deduplicating power of categorization.

**Wins:** Most aligned with Claude/Codex direction. Smallest server surface.
**Losses:** **Evolve's differentiator disappears.** No more automated synthesis. No more centralized episodic corpus. Effectively a new product.
**Effort:** Medium. Less code to write than B, but a bigger product rethink.

### 4.1 Mixed Paradigms (the interesting option)

The two-track structure opens a design space the original three paradigms don't capture: **pick a different paradigm per track.** Concrete mixes worth considering:

| Mix | Guidelines (episodic) | Facts (semantic) | Rationale |
|---|---|---|---|
| **A-for-guidelines + B-for-facts** | Pure `.md` files, trigger-keyed directory, git-reviewed | MD source + shadow vector index | Trigger-match handles playbook retrieval via filesystem; vector similarity handles knowledge lookup over facts. Lowest-complexity mix that preserves both tracks' strengths. |
| **A-for-both** | Pure `.md`, trigger-keyed directory | Pure `.md`, category-keyed directory + grep | Eliminates vector infra entirely. Acceptable if latent-concept matches on facts aren't load-bearing. |
| **B-for-both** | MD + shadow index | MD + shadow index | Uniform architecture; simplest mental model; highest ongoing complexity; pays for a shadow index on the episodic track that trigger-match could handle for free. |

The **A-for-guidelines + B-for-facts** mix is worth the team's most serious consideration — it cleanly matches each track's access pattern to the paradigm that fits it best.

### 4.2 A Fifth, Hybrid-of-Hybrids (B+C for one track)

Mentioned for completeness: keep the extraction pipeline *and* let agents write directly *and* maintain a shadow index on the same track. Maximum capability, maximum complexity. Probably a trap unless we have clear evidence all three layers pay for themselves.

### 4.3 Authority Split — an orthogonal dimension every paradigm should adopt

The prior-art review (§3.6) surfaced a pattern that's **independent of the A/B/C paradigm choice** and should be applied to *whichever* paradigm the team picks: separate a human-authored authoritative layer from an LLM-generated recall layer **within each track**.

Applied to Evolve, this looks like:

| Track | Authoritative layer (human-written) | Recall layer (LLM-generated) |
|---|---|---|
| **Guidelines (episodic)** | `guidelines/authoritative/{category}/{trigger-slug}.md` — team-reviewed playbooks, equivalent to `AGENTS.md` / CLAUDE.md entries. Treated as source of truth for "what the team has decided". | `guidelines/generated/{category}/{trigger-slug}.md` — output of the clustering/synthesis pipeline. Treated as candidates; promoted to `authoritative/` via human review. |
| **Facts (semantic)** | *No separate authoritative layer (single tier).* | `facts/{domain}/*.md` — extraction pipeline output. Authority is implicit ("blessed when the team merges/edits the file"); `git blame` is the audit trail. |

**Why this matters for Evolve specifically (guidelines track):**
- Today, extracted guidelines and curated guidelines are co-mingled in the same store. A team can't easily say "this one is blessed, this one is just a candidate." Authority split fixes that without requiring a paradigm change.
- Conflict resolution changes meaning: authoritative entries *win*; generated entries are merged/pruned against them.
- Injection can prioritize: authoritative layer loaded eagerly (like CLAUDE.md), generated layer retrieved lazily (like auto memory topic files).
- Team workflows get a PR-review surface on the authoritative layer without gumming up the high-volume generated layer.

**Why facts get a single tier:** the authoritative layer for facts would be near-empty (canonical API/tool references only) and creating it imposes three real costs — two directories, dual retrieval path, and a promotion workflow with no clear owner — for near-zero benefit. Codex follows the same asymmetry: `AGENTS.md` is procedural authority; `~/.codex/memories/` has no separate "authoritative facts" layer. For facts, authority is implicit (whoever merges the PR), with `git blame` as the audit trail.

**Open: promotion workflow for guidelines.** Authority split for guidelines requires answering: who promotes, at what cadence, with what UI? Is promotion a copy or a move? When a generated guideline contradicts an authoritative one, what wins, who decides? See §8.

**Regionality/privacy** (mentioned in §3.7): the generated layer is the natural place to gate by region or disable per-thread, like Codex.

This is **strictly additive** to the paradigm choice in §4 / §4.1. All five listed options (A, B, C, mixed, hybrid-of-hybrids) are compatible with an authority split on the guidelines track.

---

## 5. Side-by-Side Comparison

| Dimension | A — Pure Files | B — Hybrid | C — Agent-Managed |
|---|---|---|---|
| Source of truth | `.md` | `.md` | `.md` |
| Vector DB | Removed | Shadow index (rebuildable) | Removed |
| Extraction pipeline | Stays (outputs MD) | Stays (outputs MD) | Optional / removed |
| Conflict resolution | File-level rewrite | File-level + semantic dedup | Agent's problem |
| Retrieval for injection | Directory convention / static subtree | Hybrid: vectors + BM25 + chunk-expand | Agent reads own dir with its own tools |
| Semantic "latent match" recall | ❌ Gone | ✅ Preserved | ⚠️ Agent-dependent |
| Human-editable memories | ✅ Native | ✅ Native | ✅ Native |
| Git-friendly | ✅ | ✅ | ✅ |
| Infra to operate | Filesystem + git | Filesystem + Milvus/pgvector + watcher + re-indexer | Filesystem + git |
| Embedding cost | $0 | ~same as today (re-index on change) | N/A |
| UI implications | File browser + diffs | File browser + semantic search box | Minimal — possibly no UI |
| Alignment with industry | Strong (Claude) | Strong (Memsearch) | Strong (Codex) |
| Preserves Evolve's moat | Partial — synthesis stays, retrieval weakens | Yes — synthesis + semantic retrieval intact | No — moat is abandoned by design |
| **Fit for guidelines (episodic)** | Strong — trigger-match via directory structure; heavy human curation welcome | Good but possibly overkill — shadow index does work trigger-match could do for free | Viable — agents maintain their own playbooks, like `AGENTS.md` |
| **Fit for facts (semantic)** | Weak — grep/categorization can't substitute for conceptual similarity recall | Strong — similarity search is the natural retrieval pattern; MD adds auditability | Awkward — per-agent knowledge stores fragment the centralized corpus |
| Biggest risk | Injection misses latent conceptual matches, esp. on the facts (semantic) track | Rebuild cost for marginal delta over today | Lose the thing that made Evolve Evolve |
| Biggest reward | Radical simplification + transparency | Human-editable + retrieval both preserved | Smallest, most aligned, most portable backend |

---

## 6. Decision Framework — Questions the Team Needs to Answer

Before picking a paradigm, the team should take a position on these seven questions. Each one pushes toward a different paradigm.

0. **Track-split question (ask this first).** Should the episodic (guidelines) and semantic (facts) tracks receive the *same* paradigm, or *different* ones?
   - Same → pick a single paradigm from §4.
   - Different → pick from the mixes in §4.1. The strongest candidate there is **A-for-guidelines + B-for-facts**.

0b. **Authority-split question (orthogonal, answer regardless).** Within each track, should we separate a team-curated *authoritative* layer from the LLM-*generated* recall layer (as every prior-art system except Memsearch does)?
   - Yes → see §4.3. Recommended default.
   - No → accept that extracted output and blessed rules will co-mingle (today's model).

1. **Moat question.** §1.1 takes a position: Evolve's moat is **trajectory-mined procedural guidelines with outcome context**, not facts. Does the team agree?
   - Yes → §7's asymmetric recommendation (deep on guidelines, commodity on facts) applies.
   - No (the moat is something else, e.g. portable memory, generic agent backend, or facts-at-scale) → revisit §7. Paradigm B for facts becomes plausible again, or Paradigm C if repositioning as infrastructure.

2. **Retrieval question.** How much do we believe semantic similarity is load-bearing for injection quality today? Do we have telemetry?
   - If we have evidence semantic recall catches latent matches humans would miss → B.
   - If most injections are topic-tagged and could be served by directory structure → A.

3. **Operational-cost question.** Do we *want* to keep operating Milvus/pgvector, or is reducing infra a goal in itself?
   - Reduce infra is a goal → A or C.
   - We're fine with vector infra if it earns its keep → B.

4. **Authorship question.** Who should *author* a memory entry?
   - The Evolve pipeline, always → A or B.
   - The agent itself, often → C (or B+C).

5. **Compatibility question.** Does the existing plugin ecosystem (Claude / Codex / Bob / Claw) need to keep working during and after the transition, or is a breaking rewrite acceptable?
   - Must keep working → B (safest evolution).
   - Breaking rewrite acceptable → A or C both viable.

6. **Timeline question.** Is this a one-quarter project, a two-quarter project, or a full-year re-platform?
   - One quarter → A (smallest surface change).
   - Two quarters → B (evolution with retrieval preserved).
   - Full year → C (new product definition).

---

## 7. Recommendation (Author's View)

The recommendation is now **asymmetric by design** (per §1.1): invest deeply in the guidelines track because that's the moat; commoditize the facts track because that's table stakes.

### 7.1 Guidelines track — the moat (deep investment)

**Paradigm A as the substrate**, plus three procedural-memory-specific features that no surveyed competitor offers:

1. **Trigger-keyed directory layout.** `guidelines/{namespace}/{category}/{trigger-slug}.md` (categories: strategy / recovery / optimization). Pure `.md`, git-reviewed, no vector DB for content. The filesystem hierarchy serves as the **lookup index** (trigger-slug → file in O(1)). It does *not* serve as the recognition index — see feature 5 for that.
2. **Authority split** (from §4.3): `authoritative/` (team-curated playbooks, PR-reviewed) vs `generated/` (extraction-pipeline output, candidates for promotion). Promotion workflow needs to be specified — see §8.
3. **Outcome metadata as a first-class field.** Each guideline carries `outcome_evidence`: signal-source-aware confidence-weighted observations (see §7.1.1), with confirmed/inferred/unknown buckets, source-trajectory back-refs, and recovery markers. Conflict resolution and retrieval ranking both prioritize by outcome evidence. This is what makes Evolve different from "file-of-tips."
4. **Situational linking (A-MEM-inspired).** When a new guideline is generated, the system links it to situationally adjacent existing guidelines (similar trigger, same category, overlapping tools). This solves the trigger-slug-collision problem without forcing a brittle canonical normalizer. Links live in YAML frontmatter (`related: [trigger-slug-1, trigger-slug-2]`).
5. **Trigger-only embedding index (Day-1 critical — not deferred).** In realistic injection, the agent rarely sends an exact trigger slug; it sends a *task description* and the system needs to recognize which triggers apply. The filesystem hierarchy handles **lookup** (trigger-slug → guideline file). It does *not* handle **recognition** (free-form task → candidate triggers). A tiny embedding index over `trigger + category + short description` (~500–2000 entries × 1536 dims ≈ 3–12 MB, in-memory or SQLite-VSS) makes recognition tractable while staying ~10–50× smaller than Paradigm B's "embed every chunk of every fact." Optional MemU-style dual mode: cheap embedding kNN for hot-path/continuous monitoring, LLM-as-router fallback when embedding confidence is low.
6. **Proactive sidecar / mid-session injection (MemU-inspired).** Today Evolve injects guidelines once at agent-start. A separate `EvolveWatcher` process subscribed to Phoenix spans live can recognize triggering situations *as they happen* and surface relevant guidelines mid-session — turning guidelines from boot-time injection into mid-task coaching. This is a strategic upgrade to the moat: procedural rules are most valuable *exactly when* the matching scenario is unfolding, not 30 minutes earlier when context was loaded. MemU implements this for facts; Evolve doing it for procedural rules is a stronger product position. Roadmap: Phase 4 or 5 (after the storage substrate stabilizes).
7. **Pre-compaction extraction flush (OpenClaw-inspired).** Long agent sessions trigger context compaction. When a host (Claude Code, Codex) signals impending compaction, the plugin can request a silent extraction turn — Evolve reads the recent trajectory window, runs targeted fact/guideline extraction *before* the in-flight context is summarized away, and persists the result. Without this, valuable trajectory detail collapses into a generic compaction summary and the extraction pipeline only ever sees the post-compaction blur. Each plugin (Claude/Codex/Bob/Claw) needs a small hook for the compaction signal; Evolve handles the rest. Roadmap: Phase 4 (alongside authority-split work, since both touch plugin contracts).

#### The canonical retrieval flow (three steps)

```
task description ("user is retrying a payment that failed")
        │
        ▼  STEP 1: RECOGNITION  (feature 5 — trigger-only embedding kNN
        │                       OR LLM-as-router for high-stakes calls)
        │
        ▼   candidates: [auth_failed_401, payment_retry, card_declined_recovery]
        │
        ▼  STEP 2: LOOKUP  (feature 1 — filesystem path resolution, O(1))
        │  Path(f"guidelines/{ns}/{cat}/{slug}.md") for each candidate
        │
        ▼   guideline MD files loaded; expand 1 hop via feature 4 `related: [...]` if results are thin
        │
        ▼  STEP 3: RANK  (feature 3 — outcome-aware confidence-weighted score)
        │
        ▼   top-K guidelines injected
```

The three steps have different costs and different acceleration structures: step 1 is the *small-index* problem, step 2 is the *no-index-needed* problem, step 3 is the *metadata-sort* problem.

**Why Paradigm A here, not B:** the recognition step uses a tiny trigger-only embedding index (~3–12 MB total). Paradigm B by contrast embeds every chunk of every fact (≫ MB and ongoing churn cost). We pay for embedding *only on the trigger metadata*, not on full guideline content. Spend the complexity budget on outcome metadata, linking, and proactive injection — not on a shadow content index.

#### 7.1.1 Outcome signal extraction strategy

The naive `success_count` / `failure_count` schema implies binary ground truth that **most trajectories will not have**. A realistic distribution looks more like:

| Source | Confidence | Coverage | Cost |
|---|---|---|---|
| Explicit user feedback (👍/👎, "that worked", ratings) | very high | low (~5–15%) | free |
| Tool-level hard signals (HTTP errors, exceptions, schema mismatches, retries) | high | medium-high (depends on instrumentation) | free |
| Trajectory-shape patterns (retry → success = recovery; reached `terminate()` cleanly = likely success; hit max-iters = likely failure) | medium | high | free |
| User-reply patterns ("no, instead try X" = previous turn failed; same follow-up question = previous answer didn't land) | medium | medium | cheap LLM |
| LLM-as-judge (post-hoc: "did the agent achieve the goal?") | medium-low | 100% if you pay for it | expensive, rate-limit-aware |
| **Implicit usage signals** (recall frequency, query diversity, retrieval-then-no-correction pattern) — borrowed from [OpenClaw](https://docs.openclaw.ai/concepts/memory) | medium | 100% (free byproduct of retrieval telemetry) | free |

**On implicit usage signals.** OpenClaw promotes guidelines from short-term to durable storage when they pass thresholds on *recall frequency* (was this guideline retrieved often?), *query diversity* (across diverse contexts?), and *retrieval-then-no-correction* (was the agent's subsequent action consistent with the guideline?). These are zero-cost — they're a free byproduct of telemetry we already need for §8.3. They're complementary to explicit outcome signals: a guideline can be *frequently retrieved and well-correlated with success* (high explicit + high implicit), or *frequently retrieved but never followed by success* (high implicit but low explicit, suggesting the guideline is wrong but situationally salient), or *rarely retrieved despite explicit success on training trajectories* (low implicit, suggesting the trigger-recognition step is failing). All three patterns matter and are signal sources for different fixes.

**Schema** — `outcome_evidence` carries observations with provenance, not raw counts:

```yaml
outcome_evidence:
  observations:
    - trajectory_id: traj-abc-123
      signal_source: tool_error            # explicit_feedback | tool_error |
                                           # trajectory_shape | reply_pattern | llm_judge
      observed_outcome: failure            # success | failure | unknown
      confidence: 0.95
      observed_at: 2026-05-10T14:22Z
      detail: "auth_handler raised 401 after retry exhaustion"
  aggregated:
    confirmed_successes: 8                 # confidence ≥ 0.8 (explicit/tool sources)
    confirmed_failures: 2
    inferred_successes: 4                  # 0.4 ≤ confidence < 0.8 (shape/reply)
    inferred_failures: 1
    judge_successes: 6                     # llm_judge source
    judge_failures: 1
    unknown: 12                            # zero usable signal — counted for frequency only
    confidence_weighted_score: 0.78
    last_observed_at: 2026-05-10T14:22Z
```

**Three properties this preserves:**

1. **`unknown` is first-class.** A guideline with `confirmed_successes=0, confirmed_failures=0, unknown=50` is *a trigger that fires often but we have no data on* — very different from `confirmed_successes=0, confirmed_failures=10` (a bad rule). Conflict resolution and ranking must distinguish them.
2. **Confidence-weighted aggregation.** `confidence_weighted_score = Σ(confidence_i × outcome_value_i) / Σ(confidence_i)`, where `outcome_value` is `+1`/`-1`/`0` for success/failure/unknown. Falls back gracefully when only weak signals exist.
3. **Source diversity is itself a quality signal.** A guideline with outcomes from 3 different signal sources is more trustworthy than the same numeric score from one.

**Conflict resolution behavior:**

| Situation | Behavior |
|---|---|
| Two contradictory guidelines, both with strong evidence | higher confidence-weighted score wins; loser annotated |
| Strong vs all-unknown | strong wins; unknown is *not* deleted, tagged "needs validation" |
| Both all-unknown | don't auto-resolve; flag for human review; both stay |
| New guideline, zero observations (cold start) | category-based prior: `recovery` starts at 0.5 (we tried it because something failed); `strategy` at 0.6 (someone wrote it deliberately); LLM-extracted at 0.4. Refines from there as observations accumulate. |
| Unknown count grows unboundedly | background LLM-judge sweep on the K most-frequent unknown-only triggers |

**Build order in Phase 2** (each ships independently):

1. Tool-level signals — Phoenix spans already capture errors/retries; just write the extractor (~1 week, highest ROI).
2. Trajectory-shape patterns — retry detection, recovery detection, max-iter detection (~1–2 weeks). Free coverage on top of #1.
3. Reply-pattern classifier — small LLM pass over user messages, batched (~2 weeks, optional).
4. LLM-judge fallback — only on high-frequency unknown triggers, rate-limit-aware (~1 week + ongoing cost).
5. Explicit user feedback — opportunistic; plumb through whenever Claude/Codex/Bob/Claw expose it.

Coverage projection after #1+#2 alone: probably 50–70% of trajectories have *some* signal — enough to make the moat feature real before #3–#5.

### 7.2 Facts track — commodity (defer complexity)

**Paradigm A as the starting point**, *not* B. Until telemetry proves latent-concept semantic recall is load-bearing, don't pay the shadow-index complexity cost.

- `facts/{domain}/*.md`, no authority split (single tier per §4.3 update), no shadow index initially.
- Retrieval: directory walk + grep + categorization. If this proves insufficient, *then* add a shadow vector index (i.e. graduate to Paradigm B). Don't lead with B.
- Alternative path worth evaluating: **outsource facts to a memory-tool-style commodity layer** (Memory Tool API, Mem0). Free up engineering capacity for the moat.

**Why this is the asymmetric inversion of the prior recommendation:** the doc earlier argued "B-for-facts" because vector search is "where it earns its keep." That framing assumed facts are a primary product. If facts are commodity, the bar is "good enough to keep agents working," not "best-in-class semantic recall." Pure files clear that bar; B is over-investment in the wrong layer.

### 7.3 Reasoning

- **Capital allocation matches strategic value.** Guidelines = differentiated → architectural depth (outcome metadata, situational linking, authority split, eventually-optional embedding). Facts = commodity → minimum viable layer.
- **Migration risk drops.** Guidelines can move to pure files quickly (bounded volume). Facts go to pure files even faster (no index work). The full B paradigm is no longer Day-1 critical.
- **The current pain (opaque storage, no git workflow) gets solved on both tracks** without throwing away Evolve's synthesis pipelines.
- **Outcome metadata is the single feature that most differentiates Evolve from every other memory system.** It should be designed in from Day 1 of the new schema, not bolted on later.

### 7.4 Honest accounting of what dies in this transformation

- **`BaseEntityBackend` as a unifying abstraction goes away.** Today it's type-agnostic — facts and guidelines share one mutation path, schema, conflict-resolution call, and retrieval path (`altk_evolve/backend/base.py:110-186`, `schema/core.py:19-30`). Even though both tracks now start on Paradigm A, the per-track features (outcome metadata + linking on guidelines; nothing on facts) mean separate schemas and per-track conflict-resolution prompts.
- **Conflict resolution must be split per track.** Today one prompt handles any entity batch (`llm/conflict_resolution/conflict_resolution.py:13`). The guideline-side merger now needs to reason about outcome evidence; the fact-side can stay simple.
- **Plugin injection still gets a unified surface.** With both tracks on pure files (Paradigm A on Day 1), retrieval is no longer bimodal — both paths are filesystem-based. The `inject(task)` API in `EvolveClient` still wants to exist as the single entry point, but the implementation behind it is much simpler than the prior B-for-facts plan implied.
- **Vector infra (Milvus / pgvector) can be retired on Day 1.** The earlier "B-for-facts" recommendation kept it in the loop. The new asymmetric recommendation lets us turn it off entirely. This is a major operational simplification.

### 7.5 Honest counterarguments

- *If facts turn out to be load-bearing for differentiation* (e.g. we discover a use case where conceptual recall over millions of facts becomes the product), graduate facts to Paradigm B at that point. Don't pre-pay the complexity.
- *If the team wants to reposition Evolve as generic memory infrastructure,* Paradigm C is the answer even though it discards today's moat. That's a product decision, not an architecture one.
- *If we lack telemetry on whether outcome-metadata-ranked retrieval actually beats trigger-match-only* on the guidelines track, ship the simpler version first and add ranking after measuring (see §8).
- *If the "outcome metadata + situational linking + authority split" stack on the guidelines track is too heavy for one quarter,* sequence it: directory layout + authority split first; outcome metadata second; situational linking third. None of these are required for migration off the vector DB.

---

## 8. Open Questions / Unknowns

### 8.1 Architecture & data model

- **Memory-type taxonomy.** Are guidelines really *episodic* or are they *procedural* (CoALA's third type)? See §2.1 caveat. The choice changes who/what writes them.
- **Temporal validity / fact invalidation.** When a fact becomes false ("user's preferred IDE was VSCode, now Cursor"), how is the prior fact preserved with a valid-to timestamp instead of overwritten? Today's `resolve_conflicts` collapses to merge/replace, destroying history. Zep solves this with temporal edges (§3.6); OpenClaw's `memory-wiki` plugin (§3.6) implements *contradiction tracking + freshness tracking* over plain-MD as an existence proof. A pure-MD Evolve can adopt that pattern without inheriting graph-DB infra.
- **Backend abstraction death.** Today `BaseEntityBackend` is type-agnostic — facts and guidelines share one mutation path, one `Entity` schema, one `resolve_conflicts` call, one `search_entities` retrieval. The §7 mix forks this. Is the team committed to splitting persistence per track (and thereby retiring the shared backend abstraction), or do we want the mix to preserve a unified facade? This decision must be made *before* the paradigm choice can be acted on.
- **Per-track conflict resolution.** Today one prompt handles all conflicts. Splitting per track requires (a) a fact-side merger that doesn't exist today, (b) a separate guideline-side trigger-aware merger. Who owns the fact-side logic?
- **Memory scoping (orthogonal to authority split).** Owner/lifetime scoping (Mem0's conversation < session < user < organizational) is a *separate axis* from authoritative-vs-generated. How do these two axes compose in the new directory layout?
- **Per-session core-memory budget.** Letta's lens: what's the budget for injected facts/guidelines per session, and what's the paging policy when budget is exceeded? Not addressed today.
- **Outcome-metadata schema (guidelines track — moat-critical).** Concrete fields needed beyond today's `category`: `success_count`, `failure_count`, `last_success_at`, `last_failure_at`, source-trajectory back-refs, recovery-from-failure markers. What's the minimum viable shape? How does the schema evolve when new outcome dimensions are needed (latency? cost? user satisfaction)?
- **Situational-linking model (guidelines track).** When is a guideline "situationally adjacent" to another (similar trigger? same tools? same task pattern?)? Is linking computed at write-time, refreshed periodically, or both? Are links symmetric (A↔B) or directional (A → see also B)?
- **Outcome-aware ranking.** Does retrieval actually rank by `success_count / (success_count + failure_count)` weighted by recency? What's the cold-start behavior for a new guideline with zero outcomes?

### 8.2 Extraction & writing

- **Hot-path vs background extraction.** Mid-turn agent tool call (LangGraph hot-path) vs separate process during/after turn (background). Today Evolve is offline-batch from Phoenix spans. Should the new system support hot-path for some cases (e.g. agent says "remember this")?
- **Trigger-slug normalization.** What's the canonical normalizer for trigger strings? `auth_failed_401` vs `auth_failed_unauthorized` vs `401_response` — slug-keyed paradigm A forces an upstream normalization step that doesn't exist. What's the recall cost of getting it wrong?
- **Clustering in pure-files paradigm.** `cluster_entities` (`llm/guidelines/clustering.py`) needs embeddings on `task_description`. Does Paradigm A keep a transient embedding pass for consolidation only, or replace clustering with trigger-slug exact match plus LLM-based merge?
- **LLM-rewrite-in-place safety.** What's the cross-process locking model? What's the diff-size guardrail before accepting a rewrite (to catch hallucination/drop)? Are commits batched/squashed and authored as `evolve-bot` to keep `git log` useful?
- **Promotion workflow** (guidelines authority split). Owner role, cadence, UI surface, copy-vs-move, conflict tiebreaker rule.

### 8.3 Retrieval & injection

- **Bimodal retrieval cost.** The mix means each plugin (Claude/Codex/Bob/Claw — `frontend/mcp/mcp_server.py`, `evolve_client.py`) gains a second retrieval path (trigger-match + vector). How is this hidden behind a unified `inject(task)` API?
- **Telemetry gap.** Do we have data on which guidelines/facts are *actually* retrieved and used, and whether semantic retrieval outperforms topic-tag retrieval? Without this, A-vs-B is intuition.

### 8.4 Operations & ops budget

- **Migration plan.** §9 is a kickoff, not a plan. Need shadow-write window → backfill → traffic flip → DB read-only → DB drop. OpenClaw's `rem-backfill --stage-short-term` + `--rollback` (§3.6) is a concrete template — replay historical observations into a staged area, validate, then promote or roll back atomically. Until our migration plan exists, the recommendation is provisional.
- **Failure modes per paradigm.** What happens when:
  - File watcher dies (B drifts silently — how is sync drift detected)?
  - LLM rewrite returns garbage (A nukes a curated file)?
  - Shadow index loses chunks (silent recall hole)?
  - Promotion review is abandoned (authoritative layer rots)?
  - Multi-worker writes race for the same trigger-slug (A: no locking specified)?
- **Embedding cost reality-check.** "~same as today" assumes change rate stays similar after humans can hand-edit MD. If human edits are 5%+ of writes, embedding cost rises materially. Project the real number.
- **Steady-state size budget.** §2.1 says facts are "medium-volume after dedup"; §3.7 invokes "size discipline." What's the cap (entities × MB)?
- **Regionality mechanism.** §4.3 says privacy work "becomes simpler" — by what mechanism? Per-namespace flag? Per-user opt-in? Config gate? Codex used `memories.min_rate_limit_remaining_percent` and config flags; what's Evolve's equivalent?
- **UI cost.** The current frontend assumes structured entities. How much UI work does each paradigm imply?
- **Benchmark commitment (competitive positioning).** MemU reports 92.09% on the Locomo benchmark; we have no published number. Adopt Locomo as the table-stakes benchmark, then design or adopt a **procedural-task benchmark** where outcome-aware ranking should dominate — that's the comparison that frames our moat. Without a benchmark we can't claim differentiation; we can only describe it.

### 8.5 Strategy

- **Backwards compat.** Do any external consumers hit the current backend APIs directly? How long do we keep them working?
- **Identity concern.** If we go Paradigm C, is "Evolve" still the right name / positioning?
- **Memsearch as model.** It's a small Zilliz reference project, not production-scale. Are we comfortable adopting its architecture without an existence proof at our volume?

---

## 9. Next Steps Proposal

1. Team reads this doc and each member answers the six questions in §6 independently.
2. Compare answers — where do we agree, where do we diverge?
3. Pick a paradigm (or explicitly defer by identifying what telemetry we'd need to decide).
4. Kick off a follow-up deep-interview on the chosen paradigm to turn it into an implementation spec.

---

## References

**Primary surveyed systems:**
- Anthropic — [Claude Code memory (CLAUDE.md + auto memory)](https://code.claude.com/docs/en/memory)
- Anthropic — [Memory tool (Claude API)](https://platform.claude.com/docs/en/docs/agents-and-tools/tool-use/memory-tool)
- Anthropic — [Claude Managed Agents Memory](https://claude.com/blog/claude-managed-agents-memory)
- OpenAI — [Codex Memories](https://developers.openai.com/codex/memories)
- Zilliz — [Memsearch](https://github.com/zilliztech/memsearch)

**Adjacent systems and frameworks (§3.6):**
- Letta / MemGPT — [github.com/letta-ai/letta](https://github.com/letta-ai/letta)
- Zep / Graphiti — [help.getzep.com/concepts](https://help.getzep.com/concepts)
- A-MEM (NeurIPS 2025) — [arxiv.org/abs/2502.12110](https://arxiv.org/abs/2502.12110)
- Mem0 — [docs.mem0.ai/core-concepts/memory-types](https://docs.mem0.ai/core-concepts/memory-types)
- LangChain — [Memory for agents (CoALA-derived)](https://www.langchain.com/blog/memory-for-agents)
- MemU (NevaMind-AI) — [github.com/NevaMind-AI/memU](https://github.com/NevaMind-AI/memU)
- OpenClaw memory — [docs.openclaw.ai/concepts/memory](https://docs.openclaw.ai/concepts/memory)
- Locomo benchmark — long-conversation memory benchmark used by MemU; consider for §8.4 commitment
