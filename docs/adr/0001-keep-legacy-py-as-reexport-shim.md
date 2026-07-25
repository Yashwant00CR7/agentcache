# Keep `legacy.py` as a re-export shim; do not extract to DI'd stores

**Status:** accepted (2026-07-25)

## Context

`src/agentcache/legacy.py` is 3579 LOC / 90 free functions covering privacy, audit, images, config flags, `remember`, `context`, slots, lessons, sessions, project profile, migration, and health. The module docstring reads *"remaining concerns pending extraction. Do not add new code here."* An earlier extraction attempt stalled (see abandoned commit `ac98b25 "Raplh 2 in process"`), and commit `a6e11a5` deleted ~19 test files, so the affected surface has essentially no behavioural coverage.

An architecture review proposed splitting `legacy.py` into ~10 leaf modules plus 3 coordinators, converting the free-function API to injected classes (`MemoryStore`, `Slots`, `Lessons`, `PromptContext`, etc.), and rewriting the deleted tests as characterisation tests first. Estimated cost: ~15 PRs, 4–6 weeks, blast radius across every route, worker, MCP tool, and CLI.

## Decision

We are **not** doing the full extraction. Instead:

1. **Ship the route auth + DI decorator refactor** (deduplicate `_check_auth` / `_get_kv` / `_get_search_service` / `_get_observation_store` copies across `routes/*.py`; use the existing `routes/auth.py:require_auth`). Afternoon-sized, visible win.
2. **File-split `legacy.py`** by `git mv`-ing chunks into new files under `src/agentcache/core/` (`privacy.py`, `audit_log.py`, `image_store.py`, `config.py`, `memory_store.py`, `lessons.py`, `slots.py`, `context_builder.py`, `session_store.py`, `project_profile.py`). **The public free-function API is preserved via re-exports from `legacy.py`.** No DI, no classes, no signature changes. ~1 day of work, no characterisation tests required because behaviour does not move.

## Why the full extraction was rejected

- **No feature driver.** No planned work is currently blocked by the shape of `legacy.py`. The friction was surfaced by static analysis, not by a wall anyone hit.
- **AI-navigability is the real goal.** The purpose of splitting `legacy.py` is so agents open a 300-LOC focused file instead of a 3579-LOC bag. File-splitting alone achieves ~90% of that win; converting to injected classes achieves the remaining ~10% at 20× the cost.
- **The previous extraction stalled for a reason.** Module-global state (`_search_service`, `_stream_broadcaster`, `_dedup_locks`) and the blast radius of switching every `legacy.remember(kv, data)` callsite to `deps.memory_store.remember(data)` are what killed `ac98b25`. Nothing about our situation makes that easier this time.
- **Zero test coverage on the affected surface.** A full extraction without first re-writing the 19 deleted test files would ship regressions to prod. File-splitting sidesteps this entirely because no behaviour moves.

## When to revisit

Reopen this decision when a concrete feature makes the current shape hurt — e.g. needing to swap `MemoryStore` for a different backend, mock `Slots` in an integration test, or add a second `SearchService` implementation. Until then, do not re-suggest the full DI'd-stores extraction.
