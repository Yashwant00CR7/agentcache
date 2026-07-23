# 10 — Migrate Observation reads to typed table (Contract)

**Blocked by:** 09 — typed observations table (Expand) must be complete
**Status:** ready-for-agent

## What to build

Switch `folder_search`, `folder_timeline`, `compile_context`, and `auto_forget` to read Observations from the typed `observations` table using SQL-level filtering, replacing the current pattern of `kv.list(scope)` + Python-side filtering. Stop dual-writing new Observations to `kv_store` — the typed table is now the single source of truth. A one-time migration backfills any existing `kv_store` Observation entries into the typed table for users upgrading from a previous version.

## Acceptance criteria

- [ ] `folder_timeline()` issues a single `SELECT … WHERE folder=? AND agent=? ORDER BY timestamp DESC LIMIT ?` — no Python-side filtering loop
- [ ] `folder_search()` hydrates candidates from the typed table rather than `kv.list(scope)` for the Observation load step
- [ ] `compile_context()` fetches recent Observations using `ORDER BY importance DESC, timestamp DESC LIMIT ?` at the SQL layer
- [ ] New `folder_observe()` calls write only to the typed table (no `kv_store` Observation scope write)
- [ ] A backfill function runs once on startup to migrate existing `kv_store` Observations into the typed table
- [ ] A benchmark test (or manual note in the PR) shows query latency improvement at ≥ 10k Observations
- [ ] All existing tests pass
