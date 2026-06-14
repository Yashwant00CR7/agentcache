#!/usr/bin/env bash
# agentmemory Claude Code hook
# Source this file or add it to your Claude Code hook configuration.
# Reads AGENTMEMORY_URL and AGENTMEMORY_SECRET from environment.
#
# Usage in .claude/settings.json hooks:
#   "PostToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "bash /path/to/claude-code-hook.sh"}]}]

set -euo pipefail

AGENTMEMORY_URL="${AGENTMEMORY_URL:-http://127.0.0.1:3111}"
AGENTMEMORY_SECRET="${AGENTMEMORY_SECRET:-}"
AGENT_ID="${CLAUDE_AGENT_ID:-claude-code}"
FOLDER_PATH="${PWD}"
TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Read observation text from stdin (Claude Code passes hook data via stdin)
HOOK_DATA="$(cat)"
TEXT="${HOOK_DATA:-Claude Code tool execution in ${FOLDER_PATH}}"

# Truncate text to 4000 chars
TEXT="${TEXT:0:4000}"

PAYLOAD=$(cat <<EOF
{
  "folderPath": "${FOLDER_PATH}",
  "agentId": "${AGENT_ID}",
  "text": $(echo "$TEXT" | python3 -c "import json,sys; print(json.dumps(sys.stdin.read()))"),
  "timestamp": "${TIMESTAMP}"
}
EOF
)

AUTH_HEADER=""
if [ -n "${AGENTMEMORY_SECRET}" ]; then
  AUTH_HEADER="-H \"Authorization: Bearer ${AGENTMEMORY_SECRET}\""
fi

curl -sf \
  -X POST \
  "${AGENTMEMORY_URL}/agentmemory/agent/observe" \
  -H "Content-Type: application/json" \
  ${AUTH_HEADER:+$AUTH_HEADER} \
  -d "${PAYLOAD}" \
  --max-time 5 \
  > /dev/null 2>&1 || true

exit 0
