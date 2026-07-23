# Architectural Questions for agentcache

These are design and architecture questions raised by the current codebase.
They are not criticisms — they are things worth understanding and deciding on
before the system grows. Each one points to a real trade-off or risk.

---

## 1. Flat KV Schema

The entire data model lives in one SQLite table: `kv_store(scope, key, value TEXT)`.
Observations, memories, index shards, sessions, lessons, slots, graph edges, and
image refs all share this single namespace.

- What happens to read performance when this table grows to 100k+ rows?
- Every "query" loads an entire scope into Python and filters in memory — no
  SQL-level filtering by timestamp, type, or importance. Is that intentional?
- Why not give each data type its own table with typed columns, which would
  enable proper indexes and range queries at the DB level?

---

## 2. In-Memory Index with a Cold-Start Window

The BM25 and vector indexes live entirely in Python process memory. On restart,
the server either deserializes them from SQLite or rebuilds them from scratch in
a background thread.

- During the rebuild window, searches return results from an empty or stale
  index. Users get silently degraded results with no indication.
- Is there a "index warming" status exposed anywhere? The `/health` endpoint
  could surface this, but does it?
- For large datasets, how long does the rebuild take? Is there a bound on that?

---

## 3. Vector Index is a Flat Python List

`VectorIndex.search()` iterates over every stored vector and computes cosine
similarity one by one. This is O(n) per query.

- At 10k observations this is already slow. At 100k it is unusable.
- There is no ANN (approximate nearest neighbor) index — no HNSW, no IVF, no
  external vector DB.
- The EPIC mentions Qdrant/Chroma as future backends. What is the plan and
  threshold for switching?

---

## 4. Single Auth Token (No RBAC)

Authentication is one shared secret compared with `hmac.compare_digest`. Every
client — viewer, agent, admin — uses the same token.

- If the token leaks from one agent config, everything is exposed.
- The viewer dashboard, the `/forget` endpoint (which can delete all data), and
  the `/migrate` endpoint all share the same credential level.
- The EPIC mentions RBAC (viewer/agent/admin tokens). Is there a plan to
  implement this before exposing the server to a network?

---

## 5. Global Module-Level State

`functions.py` declares `_bm25_index`, `_vector_index`, `_embedding_provider`,
`_hybrid_search`, and `_stream_broadcaster` as module-level globals. They are
set once at startup and mutated by every request.

- This makes the application impossible to unit test in isolation (you cannot
  inject a fresh index per test).
- It also makes it impossible to run two instances in the same process (e.g.
  for testing).
- Was this a deliberate simplicity trade-off, or just how it grew?

---

## 6. No Rate Limiting or Input Size Caps on Most Endpoints

Observations are capped at 4000 characters. But memories, lessons, slots, and
graph edges have no enforced size limits. The `/agent/observe` endpoint accepts
arbitrary JSON payloads.

- A misbehaving or compromised agent could flood the server with gigabytes of
  data and exhaust disk space.
- There is no per-agent write rate limit, no total storage quota, and no
  backpressure mechanism.
- Is this acceptable for the current single-user local use case? What changes
  if this is exposed as a shared service?

---

## 7. HuggingFace Sync Backs Up the Entire SQLite File

`sync.py` uploads the whole `agentcache.db` file to a HuggingFace dataset repo,
including the BM25/vector index shards stored inside it.

- The index shards can be large and are fully reproducible from raw observations.
  Syncing them wastes bandwidth and storage.
- The backup is not incremental — it uploads the whole file every time the
  audit high-water mark changes.
- If the HF token is compromised, all user memory data (observations, memories,
  lessons) is exposed. Is that risk documented anywhere for users?

---

## 8. `functions.py` is ~2000 Lines — a God Module

All business logic lives in one file: observation ingestion, memory CRUD,
search orchestration, consolidation, auto-forget, graph building, slot
management, lessons, image store, index persistence, and privacy scrubbing.

- This makes it hard to test any single concern in isolation.
- It creates implicit coupling — a change to how observations are stored can
  accidentally affect how the index is rebuilt or how memories are versioned.
- What would it look like to split this into focused modules
  (e.g. `observations.py`, `memories.py`, `indexing.py`, `consolidation.py`)?

---

## 9. Jaccard Similarity for Memory Deduplication

`remember()` checks for duplicate memories by computing Jaccard similarity
(word overlap) between the new content and all existing memories, with a
threshold of 0.7.

- This loads all memories into Python memory on every `remember()` call.
- Jaccard on raw word sets is a weak signal for semantic similarity — two
  memories about the same concept but phrased differently score low and
  both get stored.
- With a vector embedding provider active, why not use cosine similarity
  against the vector index for deduplication instead?

---

## 10. The MCP Endpoint Duplicates Auth and Dispatch Logic

`routes/mcp.py` has its own `_check_auth()` function, its own `_get_kv()` helper,
and a large `if/elif` chain dispatching 30+ tool names. The same auth logic
exists in other route blueprints too.

- Auth is copy-pasted across at least 3 places. A future change (e.g. adding
  RBAC) would require updating each copy.
- The tool dispatch chain has no registry — adding a new tool requires editing
  a long elif block rather than registering a handler.
- Was there a reason not to use a decorator-based dispatch registry or a shared
  auth middleware?

---

## 11. Privacy Scrubbing is a Regex List — Not a Policy

`strip_private_data()` removes secrets by matching a hardcoded list of regex
patterns (API keys, tokens, passwords). It runs on raw observation text before
storage.

- If an agent logs a secret with an unusual key name (e.g. `my_service_cred`),
  it passes through unredacted.
- The scrubbing happens *after* the data is already in the Python process —
  if the process crashes mid-scrub, the raw data may already be partially
  visible in a WAL frame.
- Is the current regex set documented so users can audit and extend it?

---

## 12. No Tests

The `tests/` directory appears to be empty or minimal. For a system that
persists agent memory and is used as a source of truth for AI context:

- How do you verify that `folder_search` returns the right results after an
  observation is added and the index is updated?
- How do you catch regressions in privacy scrubbing when you add a new pattern?
- How do you verify that the HF sync does not upload stale data after a
  failed WAL checkpoint?
- What is the plan for adding test coverage before the system is used in
  production or shared with other developers?
