# 11 — `BaseVectorIndex` protocol + `InMemoryVectorIndex` adapter

**Blocked by:** 06 — indexing module must exist before its interface is formalised
**Status:** ready-for-agent

## What to build

Define a `BaseVectorIndex` Protocol (or ABC) in `indexing.py` with three methods: `add(obs_id, session_id, embedding)`, `remove(obs_id)`, and `search(query_embedding, limit) -> list`. Rename the current flat-list implementation to `InMemoryVectorIndex` and have it implement `BaseVectorIndex`. Update `AppContext.vector` to be typed as `BaseVectorIndex`. Behaviour is completely unchanged — this ticket only cuts the adapter seam so future backends (HNSW, Qdrant, Chroma) can be swapped in without touching `retrieval.py` or `consolidation.py`.

## Acceptance criteria

- [ ] `BaseVectorIndex` Protocol exists in `indexing.py` with `add`, `remove`, and `search` as the only required methods
- [ ] `InMemoryVectorIndex` implements `BaseVectorIndex` and passes a `isinstance(idx, BaseVectorIndex)` check
- [ ] `AppContext.vector` is typed as `BaseVectorIndex | None`
- [ ] A test constructs a minimal stub that implements `BaseVectorIndex` and passes it to `folder_search()` via `AppContext` — confirming the seam is real and injectable
- [ ] `VectorIndex` (old name) remains as an alias for `InMemoryVectorIndex` for one release to avoid breaking any external imports
- [ ] All existing tests pass with no behaviour change
