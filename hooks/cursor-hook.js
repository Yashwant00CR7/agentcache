/**
 * agentmemory Cursor hook
 * 
 * Add to your .cursorrules or Cursor MCP config to log observations.
 * Uses fetch() (available in Node 18+).
 * 
 * Environment variables (set in your shell profile):
 *   AGENTMEMORY_URL     — default: http://127.0.0.1:3111
 *   AGENTMEMORY_SECRET  — optional bearer token
 *   AGENTMEMORY_AGENT_ID — default: cursor
 */

const AGENTMEMORY_URL = process.env.AGENTMEMORY_URL || 'http://127.0.0.1:3111';
const AGENTMEMORY_SECRET = process.env.AGENTMEMORY_SECRET || '';
const AGENT_ID = process.env.AGENTMEMORY_AGENT_ID || 'cursor';

/**
 * Log an observation to agentmemory.
 * Call this from your Cursor hook with the relevant text.
 *
 * @param {string} text - Observation content (max 4000 chars)
 * @param {string} [folderPath] - Working directory (defaults to process.cwd())
 * @returns {Promise<void>}
 */
async function logObservation(text, folderPath) {
  const payload = {
    folderPath: folderPath || process.cwd(),
    agentId: AGENT_ID,
    text: String(text).slice(0, 4000),
    timestamp: new Date().toISOString(),
  };

  const headers = { 'Content-Type': 'application/json' };
  if (AGENTMEMORY_SECRET) {
    headers['Authorization'] = `Bearer ${AGENTMEMORY_SECRET}`;
  }

  try {
    const res = await fetch(`${AGENTMEMORY_URL}/agentmemory/agent/observe`, {
      method: 'POST',
      headers,
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) {
      console.error(`[agentmemory] observe failed: ${res.status}`);
    }
  } catch (err) {
    // Non-fatal — agentmemory is a best-effort sidecar
    console.error(`[agentmemory] observe error: ${err.message}`);
  }
}

module.exports = { logObservation };

// Example: log every tool call from Cursor's hook system
// In your .cursorrules or MCP hook config, call:
//   const { logObservation } = require('/path/to/cursor-hook.js');
//   logObservation(`Tool: ${toolName}\nInput: ${JSON.stringify(toolInput)}`);
