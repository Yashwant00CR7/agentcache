# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with this repository.

## 🚀 Development Commands

### Installation
To install all required dependencies, run:
```bash
pip install -r requirements.txt
```

### Running the Application
To start the Flask API server, run:
```bash
python src/app.py
```
The server defaults to listening on port `3111`.

### Testing
To run unit and integration tests (using pytest):
```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## 🏛️ High-Level Architecture

The application follows a modular, layered architecture designed around Flask and dynamic data services:

1. **Presentation Layer (Flask App)**: The core is orchestrated by `src/app.py`, which initializes the Flask application. It handles routing via registered blueprints, manages request processing, and sets up network services like WebSockets.
2. **Data & Persistence Layer (`src/db.py`)**: Data management is handled by `StateKV`, which wraps SQLite to provide namespaced storage for observations, memories, and metadata. This layer ensures data persistence across sessions. Key scopes include `mem:folders` (index), `mem:folder:{path}:{agent}` (observations), `mem:foldermeta:{path}:{agent}` (metadata), `mem:obs_lookup` (O(1) reverse lookup), `mem:memories` (global), and `mem:index:bm25:*` (search).
3. **Core Logic & Services (`src/functions.py`)**: This module contains all business logic implementations, including observation ingestion (`folder_observe`), timeline generation (`folder_timeline`), memory management functions (`remember`, `forget`), and the abstraction layers for embedding providers and search index operations.
4. **Search Indexing (`src/search.py`)**: This layer manages two primary search indexes: a **BM25** index (for keyword matching) and a **Vector Index** (for semantic similarity). It dynamically selects an embedding provider (Gemini, OpenAI, or SentenceTransformer) to power the Vector Index, allowing for hybrid (RRF fusion) searches.
5. **Communication Layer (WebSockets)**: The system supports real-time communication via WebSockets (`flask_sock`). This layer is responsible for broadcasting live events across connected clients, managed by functions in `src/functions.py`.

**Key Architectural Insight:** The architecture relies heavily on dynamic dependency injection. The application dynamically selects the best Embedding Provider based on environment variables (Gemini > OpenAI > Local Model), ensuring flexibility in how semantic search is performed, all orchestrated from the central `create_app()` function.

## MCP Tools (12 active)

The system exposes 12 Memory/Tool endpoints via `/mcp/tools`. These tools provide agents with access to core functionalities like observation ingestion (`agent_observe`), memory recall (`memory_recall`), and persistence operations (`memory_save`, `memory_forget`). Their schemas can be found at `GET /agentcache/mcp/tools`.

## Environment & Configuration Notes

*   **Persistence**: SQLite file lives at `~/.agentcache/agentcache.db`.
*   **Configuration**: Application settings are loaded from environment variables, often managed via a `.env` file in `~/.agentcache/`. Credentials (like API keys) must be set for secured operations. Key variables include `GEMINI_API_KEY`, `OPENAI_API_KEY`, `AGENTCACHE_SECRET`, and scope filters like `AGENTCACHE_AGENT_SCOPE=isolated`.
*   **Data Limits**: Observe ingestion is capped at `MAX_OBS_PER_FOLDER` (default 2000). Context compilation is capped by `TOKEN_BUDGET` (default 2000).

## Memory Structure Overview

The system supports folder-based observation scoping, global memories (`remember`/`forget`), and timeline tracking. All persistent data interaction flows through the `src/db.py` StateKV layer to maintain namespaced and auditable storage.

- [CLAUDE.md](file.md) — hook