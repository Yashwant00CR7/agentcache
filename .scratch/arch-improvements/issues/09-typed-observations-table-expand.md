# 09 — Add typed `observations` table to SQLite (Expand)

**Blocked by:** 05 — observations module must exist to own this migration
**Status:** ready-for-agent

## What to build

Add a typed `observations` table to the SQLite schema with columns for `folder`, `agent`, `timestamp`, `type`, `importance`, and `text`. When a new Observation is ingested via `folder_observe()`, write it to both the new typed table and the existing `kv_store` scope (dual-write). Reads still use `kv_store` — zero behaviour change for any query path. This is the expand step that makes SQL-level filtering possible without breaking anything.

The new table enables: `WHERE folder = ? AND agent = ? AND timestamp > ?` and `ORDER BY importance DESC LIMIT N` entirely at the SQLite layer — no Python-side filtering needed.

## Acceptance criteria

- [ ] `observations(id TEXT PK, folder TEXT, agent TEXT, timestamp TEXT, type TEXT, importance INTEGER, text TEXT)` table exists in the DB schema with indexes on `(folder, agent, timestamp)` and `(importance)`
- [ ] `folder_observe()` dual-writes: one row to `observations`, one entry to `kv_store` (existing path)
- [ ] The DB migration runs automatically on startup if the table does not exist (no manual step)
- [ ] All reads continue to use `kv_store` — no query is changed in this ticket
- [ ] All existing tests pass
- [ ] A DB integrity test verifies that for each `kv_store` Observation entry written, a matching row exists in `observations`
