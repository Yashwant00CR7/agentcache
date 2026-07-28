# Changelog

All notable changes to `agentcache` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.9.14] — 2026-07-28

### Added
- `agentcache --version` top-level flag prints the installed package version and exits. (#61)
- Viewer: **Inkwell Light** theme — paper-white surface variant of Inkwell that keeps the Caveat display type, dot-grid rhythm, and semantic accent language on a light canvas. (#60)

### Changed
- Viewer theme system now ships only `inkwell` and `inkwell-light`; the legacy Light and Dark themes have been removed. Existing users with a stored `light`/`dark` preference are migrated to `inkwell` on next load. The `data-theme="dark"` CSS block has been deleted. (#59)
- Viewer: replaced remaining upstream `rohitg00/agentmemory` links (footer, empty state, banner docs, feature-flag `docsHref`) with the current `Yashwant00CR7/agentcache` repository. (#58)
- Viewer: `/folders` page heading now sits flush with the tab bar, matching the placement of the `/graph` view header. (#58)

## [0.9.13] — 2026-07-28

### Changed
- Inkwell viewer redesigned around a "Floating Chrome" layout — header and tab bar consolidate into a single glass pill anchored top-center, footer shrinks to a pill at bottom-right, and every route gets a consistent Caveat page title. Content centers under the floating chrome up to a 1400px width.
- Inkwell tools tab is now a split view: tool list on the left, JSON args/response panel on the right. The first tool is auto-selected on load so the panel is populated by default (applies to every theme).
- Inkwell graph canvas gains its own dot-grid so it no longer reads as a flat panel over the body; tooltip and zoom controls are re-skinned to match.

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

[0.9.14]: https://github.com/Yashwant00CR7/agentcache/releases/tag/v0.9.14
[0.9.13]: https://github.com/Yashwant00CR7/agentcache/releases/tag/v0.9.13
[0.9.12]: https://github.com/Yashwant00CR7/agentcache/releases/tag/v0.9.12
[0.9.11]: https://github.com/Yashwant00CR7/agentcache/releases/tag/v0.9.11
[0.9.10]: https://github.com/Yashwant00CR7/agentcache/releases/tag/v0.9.10
[0.9.9]: https://github.com/Yashwant00CR7/agentcache/releases/tag/v0.9.9
