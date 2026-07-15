# Contributing to agentmemory-python

Thanks for your interest. This covers the path from "I have an idea" to "it's merged."

## License

Apache-2.0. Every contribution is covered by it.

## Before opening an issue

Search [open issues](https://github.com/Yashwant00CR7/agentcache/issues?q=is%3Aopen) and [closed issues](https://github.com/Yashwant00CR7/agentcache/issues?q=is%3Aclosed) first.

**Bug reports** — include:
- Python version (`python --version`)
- OS
- Exact steps to reproduce
- What you expected vs. what happened
- Any relevant log lines from the server

**Feature requests** — describe the user problem before the implementation. "I couldn't X because Y" is more useful than "please add Z."

## Before opening a PR

1. Fork the repo and create a branch off `main`:
   - `feat/<short-name>` for new features
   - `fix/<issue-number>-<short-name>` for bug fixes
   - `docs/<topic>` for documentation
   - `refactor/<topic>`, `chore/<topic>` for everything else

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Make your change. Keep it focused — one logical change per PR.

4. Run the server and test manually:
   ```bash
   python src/app.py
   curl http://localhost:3111/agentmemory/livez
   ```

5. No test runner is configured yet. If you're adding tests, use `pytest`:
   ```bash
   pip install pytest
   pytest
   ```

6. Run linting before submitting:
   ```bash
   pip install ruff
   ruff check src/
   ruff format src/
   ```

## Pull request guidelines

- Write a clear description: what it does, why, and how to verify.
- Link the issue the PR resolves (`Fixes #NNN` or `Closes #NNN`).
- Keep PRs small — large PRs are harder to review and slower to merge.
- Address review feedback in new commits, not force-pushes.

## Code style

- Python 3.10+. No walrus operator or match/case unless there's a real reason.
- No comments that restate what the code does. Only comment the *why* — a hidden constraint, a workaround for a specific bug, a non-obvious invariant.
- No dead code or commented-out blocks.
- Validate inputs at system boundaries (REST endpoints, MCP handlers). Never pass raw request bodies to internal functions.
- Use `hmac.compare_digest` for secret comparisons — never `==`.

## Architecture rules

When adding a REST endpoint, also update:
- `README.md` API Reference table
- `AGENTS.md` if it's agent-callable

When adding an MCP tool, also update:
- The schema list in `GET /agentmemory/mcp/tools` in `src/app.py`
- The dispatch handler in `POST /agentmemory/mcp/tools` in `src/app.py`
- The tool table in `README.md`
- The tool list in `AGENTS.md`

When adding a new KV scope, also update:
- The `KV` class in `src/functions.py`
- The scope table in `AGENTS.md`

## Good first contributions

- Add pytest test coverage for `src/functions.py`
- Add a hook script for a new agent (see `INSTALL_FOR_AGENTS.md`)
- Add support for an additional embedding provider (OpenAI, Cohere, local)
- Improve error messages in the viewer
- Add a language-specific README to `READMEs/`

## Questions?

Open an issue or start a discussion. The project is small enough that a quick question is usually faster than reading all the code.
