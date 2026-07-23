# 03 — Centralise auth middleware

**Blocked by:** None — can start immediately
**Status:** ready-for-agent

## What to build

Replace the copy-pasted `_check_auth()` function that currently lives independently in every route blueprint (`mcp.py`, `observations.py`, `memories.py`, `health.py`, and others) with a single `require_auth` decorator defined in a new `auth.py` module. Every route that previously called `_check_auth()` at the top of its handler switches to the `@require_auth` decorator. The auth logic (timing-safe `hmac.compare_digest` Bearer token check) is identical — this is purely a deduplication. RBAC can be added in one place in a future ticket.

## Acceptance criteria

- [ ] `auth.py` exists with a `require_auth(f)` decorator that performs the timing-safe Bearer token check
- [ ] Every route blueprint imports and uses `@require_auth` — no blueprint defines its own `_check_auth`
- [ ] A request with no secret configured passes through (existing behaviour preserved)
- [ ] A request with a wrong token still gets a `401` response
- [ ] All existing tests pass
