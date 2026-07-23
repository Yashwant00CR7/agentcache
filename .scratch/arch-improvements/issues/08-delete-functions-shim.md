# 08 — Contract: delete `functions.py` shim, migrate all callers to direct imports

**Blocked by:** 05, 06, 07 — all three split batches must be complete
**Status:** ready-for-agent

## What to build

Delete `functions.py` entirely. Update every import across `routes/`, `workers.py`, `app.py`, and the test suite to point directly to the focused module that owns the symbol. This is the contract step of the expand–contract sequence. After this ticket the domain vocabulary from `UBIQUITOUS_LANGUAGE.md` is directly reflected in the module names — there is no god module.

Import mapping (non-exhaustive):
- `from .functions import folder_observe` → `from .observations import folder_observe`
- `from .functions import remember, forget` → `from .memories import remember, forget`
- `from .functions import folder_search, compile_context` → `from .retrieval import folder_search, compile_context`
- `from .functions import IndexPersistence, rebuild_index` → `from .indexing import IndexPersistence, rebuild_index`
- `from .functions import consolidate, auto_forget` → `from .consolidation import consolidate, auto_forget`

## Acceptance criteria

- [ ] `functions.py` does not exist in the repository
- [ ] `rg "from .functions import\|from agentcache.functions import\|import functions"` returns zero results in `src/`
- [ ] All existing tests pass with imports updated
- [ ] No test imports from `functions` — each test imports from the specific module it exercises
- [ ] CI is green
