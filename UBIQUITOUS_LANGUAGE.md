# Ubiquitous Language — agentcache

> This glossary defines the canonical terms used in agentcache's domain model.
> Use these exact names in code, docs, PR descriptions, ticket titles, and conversations.
> "Aliases to avoid" lists terms found in the codebase or tickets that should be replaced.
> Last updated: July 2026 — incorporates architecture review, ubiquitous language session, and ticket breakdown.
>
> **Architecture improvement tickets:** `.scratch/arch-improvements/issues/`

---

## Core Memory Model

| Term | Definition | Aliases to avoid |
|------|------------|-----------------|
| **Observation** | A single captured event from an agent's execution — one tool call, one prompt, one file edit — scoped to a Folder Pair. The atomic unit of Working Memory. | `log`, `event`, `entry`, `raw_observation`, `hook event` |
| **Memory** | A manually saved, long-term insight, decision, or pattern — not derived from a single tool call but from deliberate agent reflection. | `long-term memory`, `global memory`, `insight`, `fact` (when used alone) |
| **Lesson** | A reusable learning with a confidence score that decays over time; strengthened when the same pattern recurs. | `learning`, `tip`, `rule` |
| **Slot** | A named, pinned piece of context — manually set or auto-populated — injected into every session start. | `pinned slot`, `memory slot`, `context slot` |
| **Folder Pair** | The primary scope key: the combination of a `folderPath` and an `agentId` that uniquely identifies one agent's memory for one project directory. | `(folder, agent)`, `folder-agent pair`, `scope key`, `session` (when used to mean this) |
| **Folder Metadata** | Aggregate statistics for a Folder Pair: observation count, last updated timestamp, and optional summary. | `folder meta`, `FolderMeta` |

---

## Memory Tiers (Consolidation Pipeline)

| Term | Definition | Aliases to avoid |
|------|------------|-----------------|
| **Working Memory** | The live, unprocessed stream of Observations for a Folder Pair — raw and ephemeral. | `raw observations`, `session logs` |
| **Episodic Memory** | A compressed summary of a group of Observations for a Folder Pair, produced by the Consolidation pipeline. | `session summary`, `compressed episode`, `narrative` |
| **Semantic Memory** | Stable factual knowledge extracted from multiple Episodic Memories by the Consolidation pipeline. | `fact`, `knowledge`, `semantic fact` |
| **Procedural Memory** | A reusable workflow or decision pattern extracted from repeated cross-session patterns. | `procedure`, `workflow memory`, `pattern` (when used alone) |
| **Consolidation** | The LLM-driven pipeline that compresses Working Memory → Episodic → Semantic → Procedural. Triggered manually or by the auto-forget sweep. | `crystallize`, `compress`, `summarize` |

---

## Agents & Identity

| Term | Definition | Aliases to avoid |
|------|------------|-----------------|
| **Agent** | An AI coding assistant (Claude, Kiro, Cursor, Cline, etc.) that writes Observations and reads Memory. | `client`, `AI`, `bot` |
| **Agent ID** | A string identity for one agent instance, used as the second key in a Folder Pair. | `agentId`, `agent_id`, `agent name` |
| **Agent Scope** | The isolation boundary for an Agent's data — either `shared` (sees all agents' data) or `isolated` (sees only its own). | `scope isolation`, `agent namespace` |
| **MCP Client** | Any tool (Cursor, Claude Desktop, Kiro) that communicates with agentcache using the Model Context Protocol. | `plugin`, `integration`, `agent client` |

---

## Search & Retrieval

| Term | Definition | Aliases to avoid |
|------|------------|-----------------|
| **Recall** | A keyword-based search over Observations and Memories using the BM25 index. | `search`, `lookup`, `query` (when BM25 is implied) |
| **Smart Search** | A hybrid search combining BM25 keyword matching and vector cosine similarity, fused by Reciprocal Rank Fusion (RRF). | `semantic search`, `hybrid search`, `vector search` |
| **Folder Search** | A Recall or Smart Search scoped to one or more Folder Pairs. | `scoped search`, `project search` |
| **Timeline** | A chronological, filterable feed of Observations for a Folder Pair. | `activity feed`, `history`, `replay feed` |
| **Context Compilation** | The process of assembling Slots, Lessons, recent Episodic Memories, and a Profile into a single text block for agent injection at session start. | `context injection`, `context window prep`, `compile_context` |

---

## Lifecycle & Maintenance

| Term | Definition | Aliases to avoid |
|------|------------|-----------------|
| **Auto-Forget** | A background sweep that evicts Observations past their TTL or above the per-Folder-Pair cap, optionally running Consolidation before eviction. | `auto_forget`, `TTL sweep`, `pruning`, `cleanup` |
| **Deduplication** | Removal of Observations whose text fingerprint (SHA-256) matches an existing Observation in the same Folder Pair. | `dedup`, `duplicate removal` |
| **Privacy Scrub** | The process of stripping secrets, tokens, and API keys from Observation text before storage, using a regex-based policy. | `redaction`, `sanitization`, `strip_private_data` |
| **Index Rebuild** | A background process that reprocesses all stored Observations and Memories into a fresh BM25 and vector index. | `reindex`, `rebuild_index`, `warm-up` |
| **Audit Entry** | An append-only record of every write operation, stored in the audit log with agent ID and timestamp. | `audit log`, `commit`, `version` (legacy Dolt term — avoid) |

---

## Knowledge Graph

| Term | Definition | Aliases to avoid |
|------|------------|-----------------|
| **Relation** | A directed edge in the Knowledge Graph between two concepts, files, or Folder Pairs. | `graph edge`, `link`, `connection` |
| **Knowledge Graph** | The set of Relations extracted from Observations, used to augment search retrieval with structural codebase topology. | `graph`, `concept graph`, `project graph` |
| **Graph Extraction** | The LLM-driven process that reads Observations and produces Relations for the Knowledge Graph. | `graph build`, `relation extraction` |

---

## Architecture & Module Structure

These terms describe the codebase's structural concepts, used in tickets, PRs, and architecture discussions.

| Term | Definition | Aliases to avoid |
|------|------------|-----------------|
| **AppContext** | A single injectable dataclass that packages all runtime state (`kv`, `bm25`, `vector`, `embedder`, `broadcast`) — the replacement for module-level globals. Constructed once in `create_app()` and passed explicitly to every function that needs it. | `globals`, `module state`, `app state` |
| **Deep Module** | A module with a narrow public interface that hides substantial implementation — the opposite of a shallow wrapper. The goal of every split in the god-module refactor. | `service`, `component`, `helper` |
| **Adapter Seam** | The interface point where one concrete implementation can be swapped for another (e.g. `BaseVectorIndex` allows `InMemoryVectorIndex` to be replaced by an HNSW adapter). A seam becomes real once two concrete adapters exist. | `interface`, `abstraction layer`, `plugin point` |
| **Expand–Contract** | The two-phase refactor pattern used for wide changes: first *expand* (add the new form alongside the old so CI stays green), then *contract* (delete the old form once all callers are migrated). | `big bang refactor`, `flag day`, `rewrite` |
| **God Module** | An anti-pattern: a single module (`functions.py` at 4 583 lines) containing all domain logic, making it impossible to test any concern in isolation. Being eliminated via tickets 01–08. | `utils.py`, `helpers.py`, `core.py` (when used as a catch-all) |
| **Auth Middleware** | A single `require_auth` decorator in `auth.py` that performs the timing-safe Bearer token check — the replacement for copy-pasted `_check_auth()` functions in every blueprint. | `_check_auth`, `auth function`, `bearer check` |
| **MCP Tool Registry** | A `dict[str, Callable]` populated by `@register("tool_name")` decorators, replacing the 300-line `if/elif` dispatch chain in `routes/mcp.py`. | `tool dispatcher`, `elif chain`, `tool handler map` |
| **Privacy Policy** | The documented, extensible set of regex patterns that defines what text is scrubbed before an Observation is stored. Lives in `privacy.py` as `DEFAULT_PATTERNS`. | `regex list`, `scrub rules`, `redaction rules` |
| **Tracer Bullet** | A narrow, complete vertical slice of work that cuts through every layer (storage, logic, API, tests) and is demoable on its own. The unit of work in the ticket breakdown. | `task`, `story`, `sub-task` |

---

## Infrastructure & Sync

| Term | Definition | Aliases to avoid |
|------|------------|-----------------|
| **Folder Path** | The absolute filesystem path of the working directory that scopes a Folder Pair. | `cwd`, `directory`, `project path` |
| **KV Store** | The SQLite-backed key-value store that persists all domain data as JSON values keyed by `(scope, key)`. | `database`, `store`, `StateKV` |
| **HF Sync** | The process of backing up the KV Store to a private HuggingFace dataset repo and restoring it on startup. | `sync`, `backup`, `dataset sync` |
| **Audit High-Water Mark** | The `MAX(id)` of the audit log, used by HF Sync to detect whether any writes have occurred since the last backup. | `HWM`, `sync cursor`, `change marker` |
| **Index Shard** | A chunk of the serialized BM25 or vector index stored in the KV Store for persistence across restarts. | `index chunk`, `bm25 shard`, `vector shard` |

---

## Relationships

- An **Observation** belongs to exactly one **Folder Pair**.
- A **Folder Pair** has exactly one **Folder Path** and exactly one **Agent ID**.
- A **Folder Pair** accumulates many **Observations** and zero or one **Folder Metadata**.
- **Consolidation** consumes **Observations** from a **Folder Pair** and produces **Episodic Memories**, which in turn feed **Semantic Memory** and **Procedural Memory**.
- A **Lesson** exists globally — not scoped to a **Folder Pair**.
- A **Slot** is scoped to either a project (matched by **Folder Path**) or global (all agents).
- A **Memory** is global — not scoped to a **Folder Pair** — and may link to source **Observations** via `sourceObservationIds`.
- **Recall** and **Smart Search** operate across all **Folder Pairs** (optionally filtered) and also include **Memories**.
- **Context Compilation** assembles **Slots** + **Lessons** + **Episodic Memories** + **Profile** for one **Folder Pair** at session start.
- An **AppContext** is constructed once per process and holds one **KV Store**, one BM25 index, one **Adapter Seam** for the vector index, one embedder, and one broadcast callable.
- The **Auth Middleware** guards every route — the **MCP Tool Registry** handles dispatch after auth passes.
- The **Privacy Policy** is applied to every **Observation** before it is written to the **KV Store** or the typed observations table.

---

## Flagged Ambiguities

- **"session"** is used in two conflicting ways:
  1. *Legacy* — a `Session` object with a `sessionId`, from the pre-folder model (now read-only, used only for migration).
  2. *Informal* — an agent's working period within a Folder Pair.
  → Use **Folder Pair** for the scope concept and avoid "session" entirely in new code.

- **"memory"** is used both as the general concept ("agent memory system") and as the specific entity (`Memory` — a saved insight). 
  → In domain conversations, always qualify: **Working Memory**, **Episodic Memory**, **Semantic Memory**, **Procedural Memory**, or the entity **Memory** (capitalised).

- **"crystallize" vs "consolidate"** — the codebase uses both for the same pipeline (LLM compression of Observations into higher-tier memories).
  → Canonical term: **Consolidation**. `crystallize` is an alias to retire.

- **"recall" vs "search" vs "smart search"** — three terms for overlapping operations.
  → **Recall** = BM25 keyword search. **Smart Search** = hybrid BM25 + vector. **Folder Search** = either, scoped to a Folder Pair.

- **"forget" vs "auto-forget" vs "evict"** — used interchangeably in code and docs.
  → **Forget** = deliberate deletion triggered by an agent. **Auto-Forget** = background TTL sweep. **Eviction** = the act of removing one Observation during Auto-Forget.

---

## Example Dialogue

> **Dev:** "When an agent finishes a task, should it call `remember` or does the system handle that automatically?"

> **Domain expert:** "Those are two different things. `remember` is for **Memories** — things the agent explicitly wants to preserve, like an architecture decision. The system automatically stores every tool call as an **Observation** in the **Folder Pair** via `folder_observe`. **Memories** are always deliberate."

> **Dev:** "So if the agent never calls `remember`, does it lose everything?"

> **Domain expert:** "No — the **Observations** are still there in **Working Memory** for that **Folder Pair**. But they're raw and noisy. **Consolidation** is what turns them into clean **Episodic Memories** that survive **Auto-Forget** eviction."

> **Dev:** "What's the difference between a **Lesson** and a **Semantic Memory**?"

> **Domain expert:** "A **Lesson** is something the agent explicitly learned and wants to reuse — it has a confidence score that decays. **Semantic Memory** is extracted automatically by the **Consolidation** pipeline from patterns across **Episodic Memories**. You save a **Lesson**; the system extracts **Semantic Memory**."

> **Dev:** "And a **Slot** is different from both?"

> **Domain expert:** "Yes. A **Slot** is pinned context — the agent or developer sets it manually and it gets injected at every session start via **Context Compilation**. Think of it as a sticky note, not a memory."
