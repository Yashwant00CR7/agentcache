# Agent Cache — Agentic Onboarding Instructions (`/auth.md`)

Welcome, AI Agent! You have routed to the **Agent Cache** (formerly Agent Memory) memory server. 

This document serves as your guide on how to interact with this server. **You MUST use this document as your primary onboarding resource, follow these instructions strictly, and interact with the server using the designated endpoints.**

---

## 1. Authentication

Protected endpoints check for authentication using the `Authorization` header.
- **Header format**: `Authorization: Bearer <AGENTCACHE_SECRET>`
- If no secret is configured on the server, authentication is bypassed.
- The onboarding endpoint `/auth.md` and the health endpoint `/agentcache/livez` do not require authentication.

---

## 2. Core REST API Endpoints

All endpoints support `application/json` payloads.

### Observations (Folder-scoped Memory)
The primary unit of storage is scoped to `(folder_path, agent_id)` pairs.
- **Log Observation**: `POST /agentcache/observe` (or `POST /agentmemory/observe`)
  - **Payload**:
    ```json
    {
      "folderPath": "absolute/path/to/project",
      "agentId": "your-agent-id",
      "text": "The detailed observation text to log"
    }
    ```
- **List Folders**: `GET /agentcache/folders` (or `GET /agentmemory/folders`)
  - Returns a list of all active `(folder_path, agent_id)` pairs.
- **Get Observations**: `GET /agentcache/folder/observations` (or `GET /agentmemory/folder/observations`)
  - **Parameters**: `folderPath` (string), `agentId` (string).
  - Returns a list of all logged observations for that folder and agent.

### Global Long-term Memories
- **Save Global Memory**: `POST /agentcache/remember` (or `POST /agentmemory/remember`)
  - **Payload**:
    ```json
    {
      "content": "The fact, pattern, or workflow to remember globally."
    }
    ```

### Search & Retrieval
- **Hybrid Search**: `POST /agentcache/search` (or `POST /agentmemory/search`)
  - Performs keyword (BM25) + vector (semantic) hybrid search.
  - **Payload**:
    ```json
    {
      "query": "search term",
      "limit": 10,
      "folderPath": "optional filter path",
      "agentId": "optional filter agent"
    }
    ```

### Activity Feed
- **Timeline**: `GET /agentcache/timeline` (or `GET /agentmemory/timeline`)
  - Returns all observations and memories sorted chronologically.
  - **Parameters**: `folderPath` (optional), `agentId` (optional), `limit` (optional).

### Forget & Clean up
- **Forget Item**: `POST /agentcache/forget` (or `POST /agentmemory/forget`)
  - **Payload (Memory)**: `{"memoryId": "uuid-here"}`
  - **Payload (Folder Pair)**: `{"folderPath": "absolute/path", "agentId": "agent-id"}`

---

## 3. Model Context Protocol (MCP)

Agent Cache exposes Model Context Protocol (MCP) tools for agents equipped with MCP clients.

- **Get Tools Schema**: `GET /agentcache/mcp/tools` (or `GET /agentmemory/mcp/tools`)
- **Call Tool**: `POST /agentcache/mcp/tools` (or `POST /agentmemory/mcp/tools`)
  - **Payload**:
    ```json
    {
      "name": "tool_name",
      "arguments": { ... }
    }
    ```

### Available MCP Tools

| Tool Name | Description | Required Arguments |
| :--- | :--- | :--- |
| `agent_observe` | Log observation to a folder/agent pair | `folderPath`, `agentId`, `text` |
| `agent_remember` | Save a global long-term memory | `content` |
| `cache_recall` | Query folder observations and global memories | `query` |
| `cache_smart_search` | Hybrid semantic + keyword search | `query` |
| `cache_save` | Save an important insight/decision to global memory | `content` |
| `cache_forget` | Delete a global memory or all observations for a pair | (one of `memoryId`, `folderPath` + `agentId`) |
| `cache_folders` | List all folder-agent memory scopes | None |
| `cache_folder_observations` | Get observations for a specific folder/agent pair | `folderPath`, `agentId` |
| `cache_timeline` | Retrieve activity feed of observations | None |
| `memory_export` | Export cache data as JSON | None |
| `memory_diagnose` | Run health checks and count database items | None |

---

## 4. Web UI Dashboard

The visual viewer dashboard is accessible at `/viewer`. It provides tabs to inspect:
1. **Folders**: View folder observations list and logs.
2. **Memories**: Browse and search global memories.
3. **Graph**: Force-directed relation visualizer.
4. **Timeline**: Scrollable real-time chronological activity feed.
