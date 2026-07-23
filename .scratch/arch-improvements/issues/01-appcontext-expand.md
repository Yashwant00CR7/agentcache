# 01 — Introduce `AppContext` dataclass (Expand)

**Blocked by:** None — can start immediately
**Status:** ready-for-agent

## What to build

Introduce a single `AppContext` dataclass that packages all runtime state currently scattered as module-level globals in `functions.py` (`_bm25_index`, `_vector_index`, `_embedding_provider`, `_hybrid_search`, `_stream_broadcaster`, and the `kv` reference). Construct one `AppContext` instance inside `create_app()` and thread it through to every function that needs it. The existing globals stay in place as a compatibility shim — no callers are changed in this ticket. Zero behaviour change; this is the expand step that makes the god-module split (tickets 05–08) possible.

## Acceptance criteria

- [ ] `AppContext` dataclass exists in a new `context.py` module (or equivalent) with typed fields for `kv`, `bm25`, `vector`, `embedder`, and `broadcast`
- [ ] `create_app()` constructs one `AppContext` and stores it on the Flask app (e.g. `app.ctx`)
- [ ] The five existing `set_*` functions in `functions.py` remain working — globals are still set alongside the new `AppContext`
- [ ] All existing tests pass with no changes
- [ ] No route handler or worker is changed in this ticket
