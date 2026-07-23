# 02 — Extract `privacy.py` deep module

**Blocked by:** None — can start immediately
**Status:** ready-for-agent

## What to build

Extract `strip_private_data()` from `functions.py` into a dedicated `privacy.py` module. The new module exposes a single `scrub(text: str, patterns=DEFAULT_PATTERNS) -> str` interface. The regex pattern list is promoted to a public, documented constant (`DEFAULT_PATTERNS`) and is extensible via an env var (`AGENTCACHE_REDACT_PATTERNS`). `functions.py` calls `privacy.scrub()` internally — all existing callers continue working without change. First unit tests for Privacy Scrub are added in this ticket, targeting `scrub()` directly.

## Acceptance criteria

- [ ] `privacy.py` exists with a `scrub(text, patterns=DEFAULT_PATTERNS)` function
- [ ] `DEFAULT_PATTERNS` is a documented list of regex strings — visible and auditable
- [ ] An env var `AGENTCACHE_REDACT_PATTERNS` (comma-separated regex strings) appends additional patterns at startup
- [ ] `functions.py` no longer contains the scrubbing regex list — it imports and calls `privacy.scrub()`
- [ ] Unit tests cover: API key pattern, bearer token pattern, custom pattern via argument, no false positives on safe text
- [ ] All existing tests pass
