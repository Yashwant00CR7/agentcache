# 06 — Split god module batch 2: `indexing.py` + `retrieval.py`

**Blocked by:** 05 — split batch 1 (observations + memories)
**Status:** ready-for-agent

## What to build

Move the second major domain area out of `functions.py`.

**`indexing.py`** receives: `IndexPersistence`, `rebuild_index()`, `backfill_obs_lookup_if_needed()`, `vector_index_add_guarded()`, `clip_embed_input()`, and all index-related helpers. This module owns the Index Rebuild lifecycle (see `UBIQUITOUS_LANGUAGE.md`).

**`retrieval.py`** receives: `folder_search()`, `folder_timeline()`, `compile_context()`, and `export_data()`. This module owns the Recall, Smart Search, Folder Search, Timeline, and Context Compilation operations.

`functions.py` continues to re-export everything. CI stays green.

## Acceptance criteria

- [ ] `indexing.py` and `retrieval.py` exist as standalone modules
- [ ] All moved functions use `AppContext` from ticket 01 rather than reaching into globals directly
- [ ] `functions.py` re-exports every moved symbol unchanged
- [ ] `IndexPersistence` is importable from `indexing` in `workers.py` without changing any other worker code
- [ ] `folder_search()` and `compile_context()` are callable from a test by constructing an `AppContext` with an in-memory KV and a fresh `SearchIndex` — no Flask app required
- [ ] All existing tests pass
- [ ] No logic is changed
