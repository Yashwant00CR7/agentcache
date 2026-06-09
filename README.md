---
title: AgentMemory Python
emoji: 🧠
colorFrom: blue
colorTo: indigo
sdk: docker
pinned: false
---

# AgentMemory Python (Dolt Edition)

This is the Python rewrite of the **AgentMemory** persistent memory daemon, deployed as a custom Docker Space on Hugging Face.

## Features of this Deployment
- **Python Backend**: Built with Flask and Flask-Sock, providing a lightweight, high-performance unified REST and WebSocket server.
- **Dolt SQL Database**: Fully version-controlled memory storage using Dolt, tracking which agent pushed which memory or session.
- **Automatic HF Dataset Backup**: Automatically syncs database files to a private Hugging Face Dataset repository every 5 minutes.
- **Real-Time Viewer**: Built-in interactive dashboard to search memories, view session timelines, inspect concept graphs, manage details, and edit your personal second-brain logs.

---

## Environment Configuration
Set the following Secrets and Variables in your Space Settings to configure the application:

### Secrets (Private)
- `HF_TOKEN` — Hugging Face token with **Write** access to manage automatic dataset creation and backups.
- `GEMINI_API_KEY` — Powers semantic embeddings, graph extraction, and consolidation.
- `AGENTMEMORY_SECRET` — Your HMAC password to protect the dashboard and REST API from unauthorized access.
- `GEMINI_MODEL` — The Gemini model used for reflections and crystallizations (defaults to `gemini-2.5-flash`).

### Variables (Public)
- `AGENTMEMORY_INJECT_CONTEXT` — Set to `true` to enable injecting recalled memories into coding agent sessions.
- `AGENTMEMORY_DATASET_REPO` — Set to `Yash030/agentmemory-python-data` to isolate your database files from the legacy project.

---

## Accessing the Dashboard
1. Open your Space URL in the browser.
2. Because the API endpoints are secure, the client will show a popup asking for your viewer token.
3. Enter the `AGENTMEMORY_SECRET` you configured in your Space settings to unlock the dashboard.
