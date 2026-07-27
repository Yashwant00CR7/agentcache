# Changelog

All notable changes to `agentcache` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.9.12] — 2026-07-27

### Added
- Inkwell viewer theme (dark whiteboard aesthetic) selectable from the header dropdown alongside Light and Dark. Loading skeleton, folder-memory card, and knowledge-graph surfaces are all themed. (#40)

### Fixed
- Folder-detail view now refreshes correctly when a live-broadcast observation arrives for a currently-open **empty** folder (previously silently dropped).

## [0.9.11] — 2026-07-26

### Fixed
- PyPI upload bumped from 0.9.10 → 0.9.11 to sidestep [file-name-reuse](https://pypi.org/help/#file-name-reuse) after the 0.9.10 wheel was already published.

## [0.9.10] — 2026-07-26

### Added
- `agentcache connect --verify` — read-only diff of every detected client's MCP entry against what `connect` would write, with per-field reasons when stale. (#37)
- `docs/mcp-setup.md` — common MCP setup guide covering every supported client, manual wiring, verification, and troubleshooting.
- Hermes plugin now ships inside the wheel at `agentcache.integrations.hermes`; `agentcache connect hermes` copies it via `importlib.resources` so editable installs and zipapps work.

### Changed
- `agentcache connect <target>` now auto-repairs stale MCP entries (wrong interpreter path, missing required env keys) instead of silently reporting "already wired" on broken configs. Matching entries print an explicit `(re-run with --force to overwrite)` hint. (#37)
- Extracted a shared `install_json_mcp_entry` helper — `ClaudeCode`, `Antigravity`, `Kiro`, `VSCode` adapters are now one-liners over it, eliminating four copies of the same install shape.
- Codex TOML parsing now returns `args` as a real `list[str]` instead of a raw substring, so multi-element `args` lists no longer falsely match.

### Fixed
- Bandit `B310` on the Hermes `urlopen` call: annotated with a `# nosec` justified by the existing `_validate_url()` scheme allow-list (`http`/`https` only).
- Ruff import sort + format on files newly brought under CI (the packaged Hermes plugin, `connect.py`, `test_connect_repair.py`).
- PyPI upload bumped from 0.9.9 → 0.9.10 to sidestep [file-name-reuse](https://pypi.org/help/#file-name-reuse) after the first 0.9.9 wheel was already published.

## [0.9.9] — 2026-07-26

First PyPI release. Distributed as `agentcache-core` (the bare `agentcache` name on PyPI belongs to an unrelated project); still `import agentcache` and `agentcache serve` at runtime.

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

[0.9.12]: https://github.com/Yashwant00CR7/agentcache/releases/tag/v0.9.12
[0.9.11]: https://github.com/Yashwant00CR7/agentcache/releases/tag/v0.9.11
[0.9.10]: https://github.com/Yashwant00CR7/agentcache/releases/tag/v0.9.10
[0.9.9]: https://github.com/Yashwant00CR7/agentcache/releases/tag/v0.9.9
