# Changelog

All notable changes to `agentcache` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.9.9] — 2026-07-26

First PyPI release. Ships the beta feature set with a hardened internal layout.

### Added
- User-facing `README.md` covering install, quickstart, programmatic use, agent wiring, auth, and deployment.
- PEP 561 `py.typed` marker so downstream projects pick up inline type hints.
- `CHANGELOG.md`.

### Changed
- Split the monolithic `legacy.py` (3579 LOC) into focused `agentcache.core.*` modules (`observation_store`, `search_service`, `kv_scopes`, …). `legacy.py` is retained as a thin re-export shim for backward compatibility (see `docs/adr/ADR-0001-legacy-shim.md`); it will be removed in 1.0. (#35)
- Consolidated route authentication onto a single `@require_auth` decorator and unified service accessors. (#34)
- Deleted the `agentcache/cache/*` shim layer. (#29, #30, #31, #32)
- Removed the `memory_store.forget` Middle Man shim in favour of a direct call path.
- Declared `readme` in `pyproject.toml` with an explicit `content-type = "text/markdown"` so PyPI renders the long description.
- Renamed `requirements.txt` → `requirements-dev.txt` to signal it is a dev-bootstrap shortcut, not a library dependency list.

### Fixed
- Reconciled version strings across `pyproject.toml`, `__init__.__version__`, the `/agentcache/health` payload, the viewer template, and the MCP `serverInfo` so a single bump keeps them coherent.

---

Older 0.9.x builds predate this changelog and were never published to PyPI.

[0.9.9]: https://github.com/Yashwant00CR7/agentcache/releases/tag/v0.9.9
