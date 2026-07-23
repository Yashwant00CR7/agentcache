# 07 — Split god module batch 3: `consolidation.py` + `slots.py` + `lessons.py`

**Blocked by:** 06 — split batch 2 (indexing + retrieval)
**Status:** ready-for-agent

## What to build

Move the final major domain areas out of `functions.py`.

**`consolidation.py`** receives: `consolidate()`, `auto_forget()`, `folder_graph_build()`, and all LLM-call helpers used exclusively by the Consolidation pipeline. This module owns the Consolidation lifecycle (Working Memory → Episodic → Semantic → Procedural).

**`slots.py`** receives: all Slot CRUD functions (`get_slots`, `set_slot`, `delete_slot`, `reflect_slot`, `append_slot`, etc.).

**`lessons.py`** receives: all Lesson CRUD functions (`get_lessons`, `save_lesson`, `search_lessons`, `strengthen_lesson`, lesson decay logic).

`functions.py` re-exports everything. After this ticket, `functions.py` is a pure shim — no domain logic remains in it.

## Acceptance criteria

- [ ] `consolidation.py`, `slots.py`, and `lessons.py` exist as standalone modules
- [ ] `functions.py` contains no domain logic — only re-export statements
- [ ] All moved functions use `AppContext` from ticket 01
- [ ] `auto_forget()` is callable from a test without importing `functions` directly
- [ ] All existing tests pass
- [ ] No logic is changed
