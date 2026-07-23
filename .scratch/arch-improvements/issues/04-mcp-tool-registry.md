# 04 — MCP tool registry (replace elif chain)

**Blocked by:** 03 — centralise auth middleware
**Status:** ready-for-agent

## What to build

Replace the 300-line `if/elif` dispatch chain in `routes/mcp.py` with a `@register("tool_name")` decorator registry. Each of the 30+ MCP tool handlers becomes a standalone function decorated with `@register`. The dispatcher becomes a two-liner: look up the tool name in the registry dict and call the handler. Auth is handled by the shared `@require_auth` decorator from ticket 03. The tool schema list (`get_mcp_tools_schemas`) stays as-is. No change to the MCP wire format or agent behaviour.

## Acceptance criteria

- [ ] A `_tools: dict[str, Callable]` registry exists and a `@register(name)` decorator populates it
- [ ] Every MCP tool handler is a standalone function — not an inline block inside a giant if/elif
- [ ] The POST `/mcp/tools` handler body is ≤ 20 lines (lookup + call + error handling)
- [ ] Each tool handler function is independently importable and callable in a test without starting Flask
- [ ] Adding a new tool requires only adding a new decorated function — no editing of existing dispatch code
- [ ] All existing MCP tool calls produce the same responses as before
