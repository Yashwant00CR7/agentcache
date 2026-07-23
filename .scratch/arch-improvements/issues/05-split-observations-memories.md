# 05 — Split god module batch 1: `observations.py` + `memories.py`

**Blocked by:** 01 — AppContext dataclass (Expand)
**Status:** ready-for-agent

## What to build

Move the first two major domain areas out of `functions.py` into focused modules, using the domain vocabulary from `UBIQUITOUS_LANGUAGE.md`.

**`observations.py`** receives: `folder_observe()`, `observe()` (legacy), `dedup_folder_observations()`, `build_synthetic_compression()`, `infer_type()`, `extract_files()`, `extract_image()`, image store helpers (`save_image_to_disk`, `delete_image`, `touch_image`, `is_managed_image_path`), and `normalize_folder_path()` / `validate_agent_id()`.

**`memories.py`** receives: `remember()`, `forget()`, memory versioning logic, and `jaccard_similarity()`.

`functions.py` re-exports everything from these two modules so all existing callers continue to work unchanged. CI must stay green. This is the first batch of the expand phase of the god-module split.

## Acceptance criteria

- [ ] `observations.py` and `memories.py` exist as standalone modules
- [ ] All moved functions accept `AppContext` (from ticket 01) instead of reaching into module-level globals, while also accepting the legacy `kv: StateKV` signature for backward compat
- [ ] `functions.py` re-exports every moved symbol — no call site outside `functions.py` needs to change
- [ ] Existing route blueprints and workers continue to import from `functions` without modification
- [ ] All existing tests pass
- [ ] No logic is changed — this is a pure relocation
