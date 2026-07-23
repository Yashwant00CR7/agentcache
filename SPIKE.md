# Feature Spike: Small Improvements for Agent Cache User Experience

**Date:** 2026-07-16  
**Priority:** Low risk, high value additions to current architecture  
**Tech Stack Impact:** None — all fit within Flask/SQLite model

---

## Table of Contents
1. [Current State Analysis](#current-state-analysis)
2. [Feature Opportunities](#feature-opportunities)
3. [Recommendation Priority](#recommendation-priority)

---

## Current State Analysis

### What agentcache-python Already Has

**Core Features Implemented:**
- REST API (Flask framework on port 3111)
- WebSocket streaming (`/stream/mem-live/viewer`)
- Hybrid search: BM25 keyword + vector embeddings (Gemini/OpenAI/SentenceTransformer)
- Folder-based memory model: `(folder_path, agent_id)` scoping
- Knowledge graph construction via `folder_graph_build()`
- MCP tools for agent integration (13+ tools)
- Security redactions in observations (`strip_private_data()`)
- Token budget limits during context compilation
- Background workers for index maintenance

**Key Functions:**
| Function | Purpose | File |
|----------|---------|------|
| `folder_observe()` | Ingest observations with scoping | `functions.py` |
| `folder_search()` | BM25 + vector hybrid search | `search.py` |
| `folder_timeline()` | Chronological activity feed | `functions.py` |
| `folder_graph_build()` | Build knowledge graph nodes/edges | `functions.py` |
| `remember()/forget()` | Global memory management | `functions.py` |
| `context()` | Compile contextual blocks with token limits | `functions.py` |
| `export_data()` | v2 JSON export format | `functions.py` |

**Key Tests:**
- `test_observe_core.py` — Observation validation and redaction
- `test_folder_observe.py` — Folder-scoped ingestion
- `test_search.py` — BM25/vector search logic
- `test_context.py` — Context compilation with token budgets
- `test_graph.py` — Knowledge graph construction

---

## Feature Opportunities

### 🎯 Feature 1: Observation Prioritization / Weighting

**Why Add This?**
Users naturally write observations with different levels of importance. Currently all observations are weighted equally in search results. The search uses RRF (Reciprocal Rank Fusion) which already applies weights, but there's no user-controlled way to emphasize important observations.

**What to Implement:**
```python
def folder_observe(kv, payload, priority=0):  # 0-100 scale
    
    # Store in metadata:
    obs["priority"] = priority
    
    return {
        "observationId": fobs_{hash}_{rand},
        "priority": priority,  # Added field
        ...
    }
```

**Changes Required:**
- Add `priority` field to observation payload validation (new required/optional check)
- Modify search weighting in `HybridSearch._rank()` to include observation priority:
  ```python
  # Current RRF: rank_weight = 1 / len(relevant_docs)
  # New RRF: combined_weight = (bm25_score + vector_score) * obs_priority / 100
  ```

**User Experience:**
When a user writes an observation, they can optionally mark it as high-priority:
```python
# Tag this as critical issue - show prominently in search results
folder_observe(
    kv, {
        "folderPath": "/home/user/project/src/auth",
        "agentId": "my-ai-agent",
        "text": "JWT rotation breaks production auth endpoints",
        "timestamp": now(),
        "priority": 90  # Highest importance
    }
)
```

Search results for "jwt auth break" automatically rank this observation higher than lower-priority memories.

**Implementation Complexity:** **15 minutes**  
- Add one field to observation validation schema  
- Modify RRF weighting formula (single math operation change)  
- Update tests to verify priority affects ranking

---

### 🎯 Feature 2: Observation Source Tracking

**Why Add This?**
Users write observations from different contexts (code files, documentation, terminal logs). Knowing which file context an observation came from helps detect when user re-wrote same code after learning a lesson, and aids in identifying relevant follow-up actions.

**What to Implement:**
```python
def folder_observe(kv, payload, source_file=None):  # e.g., "src/auth.py"
    
    obs["sourceFile"] = source_file or None
    
    return {
        "observationId": fobs_{hash}_{rand},
        "sourceFile": source_file,  # New field
        "text": "...",
        ...
    }
```

**Changes Required:**
- Add `source_file` to observation payload validation (optional string)
- Store as additional JSON field in KV value column
- Update timeline query to optionally show source files
- Update search results to surface source file info

**User Experience:**
Search: "What did I learn about JWT?"  
Results:
```json
{
  "id": "fobs_abc123",
  "text": "JWT needs refresh tokens for rotation",
  "sourceFile": "src/auth.py",
  "timestamp": "2026-07-15T10:30:00Z"
}
```

The source file field tracks which files were being developed when lessons were learned. Helps detect when user re-implements same pattern after reading a tutorial, or helps identify which modules need updates based on recent observations.

**Implementation Complexity:** **15 minutes**  
- Add one optional field to observation schema  
- Store in existing KV structure (no new table/column needed)  
- Update `folder_timeline()` to surface source file info when requested  

---

### 🎯 Feature 3: Folder-Level Statistics Endpoint

**Why Add This?**
Users need quick stats on individual folders without going to the dashboard UI. The current `/health` endpoint only returns global aggregate across all folders. Users often want: "How many observations do I have in my frontend folder?" answered via simple API call.

**What to Implement:**
```python
# src/agentcache/routes/folder_stats.py or append to existing routes

@app.route('/folder/stats/<path:folder_path>')
def folder_stats(folder_path):
    kv = StateKV()  # Get current DB instance
    
    # Count observations for this folder
    obs_count = 0
    try:
        from agentcache.functions import KV
        scope = KV.folder_obs(folder_path, os.getenv("AGENT_ID", "default"))
        rows = kv.db.execute(
            "SELECT COUNT(*) FROM kv_store WHERE scope = ?",
            [scope]
        ).fetchone()
        obs_count = rows[0] if rows else 0
    except:
        pass
    
    # Get last observation timestamp
    last_obs_ts = None
    try:
        rows = kv.db.execute(
            "SELECT MAX(ts) FROM kv_store WHERE scope = ?",
            [scope]
        ).fetchone()
        last_obs_ts = rows[0] if rows else None
    except:
        pass
    
    return {
        "folderPath": folder_path,
        "observationCount": obs_count,
        "memoryCount": 123,  # Get from memories scope separately
        "lastObsTimestamp": last_obs_ts.isoformat() if last_obs_ts else None,
        "foldersKnownToThisAgent": ["home/user/projectX", ...]  # Also include this context
    }
```

**User Experience:**
REST API call returns folder stats instantly:
```bash
curl http://localhost:3111/folder/stats/home/user/my-project
# Returns:
{
  "folderPath": "home/user/my-project",
  "observationCount": 42,
  "memoryCount": 5,
  "lastObsTimestamp": "2026-07-15T14:23:00Z",
  "foldersKnownToThisAgent": ["home/user/my-project"]
}
```

**Implementation Complexity:** **10 minutes**  
- Create new endpoint following existing Flask pattern (like `/health`)  
- Query existing KV methods for observation and memory counts  
- Return simple JSON response  

**Note:** This requires importing `StateKV` instance in new route file, which means either creating a module-scoped singleton per folder or passing the current `kv` from `app.py` via Flask's application context.

---

### 🎯 Feature 4: Observation Expiration / TTL (Time-To-Live)

**Why Add This?**
Temporal observations become outdated over time. Example: "Error on port 3000" observation becomes irrelevant after user changes their app to use port 8080. Currently all observations persist indefinitely. With TTL, expired observations can auto-remove from search results or be marked as "expired".

**What to Implement:**
```python
def folder_observe(kv, payload, ttl_seconds=None):
    
    # Store observation with expiration timestamp
    obs["expiryTs"] = ts + ttl_seconds if ttl_seconds else None
    
    return {
        "observationId": fobs_{hash}_{rand},
        "ttl": ttl_seconds or null,  # New field
        ...
    }
```

**Changes Required:**
- Add `ttl_seconds` to observation payload validation (optional)
- Store as additional timestamp field in KV value column
- Modify `folder_timeline()` to filter out expired observations by default
- Optional: Add background worker that sweeps/expunges expired observations

**User Experience:**
```python
# Observation expires after 24 hours
folder_observe(
    kv, {
        "folderPath": "/home/user/app/src/server",
        "agentId": "my-agent",
        "text": "Default port is 3000 for development",
        "timestamp": now(),
        "ttl": 86400  # 24 hours
    }
)

# Timeline query automatically excludes expired observations
timeline(kv, folder_path="/home/user/app/src/server")
```

Returns timeline without "port 3000" observation after 24 hours. Users can also check for specific expiry status on an observation with additional endpoint `/folder/observation/status/<id>`.

**Implementation Complexity:** **25 minutes**  
- Add TTL field to observation payload validation  
- Store as extra JSON field in KV (existing table schema)  
- Modify `folder_timeline()` query to include optional TTL filter:
  ```python
  ts > now() - ttl_seconds if ttl_seconds else None AND expiryTs > ts
  ```  
- Update search results to exclude expired observations  
- Optional: Background worker sweeps for cleanup (simpler version doesn't need this)  

**Risk:** Medium — requires changes to timeline query logic which users expect returns chronological activity. Must ensure backward compatibility with existing payloads that don't include TTL field (use `None` default).

---

### 🎯 Feature 5: Memory Version History

**Why Add This?**
Long-term memories evolve. Currently `remember()` just overwrites the old memory. Users might accidentally overwrite important information or can't undo an incorrect memory. With versioning, each remember creates a new version tracked sequentially.

**What to Implement:**
```python
def remember(kv, data, skip_versioning=False):
    
    # Check existing memories for this content
    existing_memories = kv.get(KV.memories, "mem_abc123")
    
    if not skip_versioning and existing_memories:
        # Append version suffix to old content (preserve as text)
        current_content = json.loads(existing_memories["value"])
        current_content["_version"] = len(current_content.get("_versions", [])) + 1
        current_content["_createdTs"] = existing_memories["ts"]
        
        # Store in new version field
        versions = current_content.get("_versions", [])
        current_content["_versions"].append({
            "content": current_content["content"],
            "version": len(versions),
            "ts": now(),
            ...
        })
    
    return {
        "memoryId": new_id,
        "versions": [...],  # New field: list of version history
        ...
    }
```

**Changes Required:**
- Modify `remember()` to check for existing content match in KV memories
- Append new version as JSON object to version history array  
- Store version number and timestamp for each version change
- Optionally expose version history endpoint for viewing past versions

**User Experience:**
When a user calls:
```python
remember(
    kv, {
        "content": "JWT authentication uses bearer token in Authorization header",
        "concepts": ["jwt", "auth"],
        ...
    }
)
```

The response includes version history showing how this memory has evolved over time. Later, if the user discovers more context:
```python
remember(
    kv, {
        "content": "JWT authentication uses Bearer token and JWT_SECRET key in Authorization header",
        ...
    }
)
```

They can review past versions to see the evolution or optionally revert to earlier version by storing it again.

**Implementation Complexity:** **20 minutes**  
- Modify `remember()` to detect duplicate content (hash match on content field)  
- Store version history as JSON array in KV value column (existing table schema, just more data)  
- Optionally create endpoint `/memory/version/<id>` for viewing history  
- Update tests to verify version history is stored correctly  

**Risk:** Low — simple append operation to existing data structure. No schema migration needed since we're adding fields to existing JSON values. Backward compatible since previous versions without `_versions` field just get overwritten (which may be surprising behavior, but acceptable for safety).

---

### 🎯 Feature 6: Search Result Citation with Source Links

**Why Add This?**
When a memory is returned from search, users can see where the original observation came from and what code context had it observed. This helps understand which files are most relevant to certain topics and provides better "view in context" capability for follow-up actions.

**What to Implement:**
```python
def folder_observe(kv, payload):
    # Store source file info
    if payload.get("sourceFile"):
        obs["sourceFile"] = payload["sourceFile"]
        obs["lineNumber"] = payload.get("lineNumber") or None
    
    return {
        "observationId": fobs_{hash}_{rand},
        "sourceFile": "src/auth.py",  # New field: where this came from
        ...
    }
```

**Search Result Enhancement:**
Modify search result response format:
```python
def folder_search(kv, query, limit, folder_path, agent_id):
    
    results = [
        {
            "id": obs_id,
            "text": "...",
            "folderPath": "/home/user/project",
            "score": 0.85,  # Original BM25 score or vector similarity
            "sourceFile": "src/auth.py",   # NEW: where this came from
            "timestamp": ts.isoformat(),
            ...
        }
    ]
```

**User Experience:**
Search query: "JWT authentication"  
Results now show source file links:
```json
[
  {
    "id": "fobs_abc123",
    "text": "JWT needs refresh tokens for token rotation",
    "sourceFile": "src/auth.py",  // Click link to see code context!
    "score": 0.92,
    ...
  },
  {
    "id": "fobs_def456",  
    "text": "Using 'jwt' import from auth package",
    "sourceFile": "src/main.py",  // Another file
    "score": 0.78,
    ...
  }
]
```

**Implementation Complexity:** **20 minutes**  
- Add `source_file` field to observation schema (same as Feature 2)  
- Update search result formatting in `folder_search()` to include source file info from observations  
- Optionally: Show which files are most-relevant based on sources of highest-scoring memories  

**Risk:** Low — just need to read the new field from observations and format it in search results. No complex data structure change needed.

---

### 🎯 Feature 7: Memory Impact / Related Memories Endpoint

**Why Add This?**
When a user remembers something important, they might want to know which other memories relate semantically or contextually. Currently all memories exist independently with no explicit linking mechanism. Adding semantic similarity scores helps users understand how new memories connect to existing knowledge and spot conflicts.

**What to Implement:**
```python
def memory_related(kv, memory_id):
    
    # Get original memory content from KV
    original_memory = kv.get(KV.memories, memory_id)
    original_content = json.loads(original_memory["value"])
    
    # Search for semantically similar memories
    search_results = hybrid_search.search(
        query=original_content["content"],
        limit=5,  # Return top 5 related memories
        filter_scope=[KV.memories],  # Only search within memories scope
    )
    
    # Score by similarity + rank in results list
    return {
        "memoryId": memory_id,
        "relatedMemories": [
            {
                "id": "mem_abc123",
                "score": 0.92,  # Semantic similarity score (0-1)
                "reason": "Same concepts: JWT, authentication",
                ...
            },
            ...
        ]
    }
```

**User Experience:**
Request: `/agentcache/memories/related/mem_xyz123`  
Response shows how this memory connects to existing knowledge:
```json
{
  "memoryId": "mem_xyz123",
  "relatedMemories": [
    {
      "id": "mem_abc456",
      "score": 0.94,
      "reason": "Same concepts (JWT) and file context (src/auth.py)",
      "content": "JWT uses Bearer token format in Authorization header"
    },
    {
      "id": "mem_def789",
      "score": 0.78,
      "reason": "Related to auth module but different concepts",
      "content": "Using Express.js for REST API development"
    }
  ],
  ...
}
```

**Implementation Complexity:** **30 minutes**  
- Use existing `HybridSearch.search()` method with original memory content as query  
- Score by rank in results (1st highest = most related) + cosine similarity score  
- Format response as JSON with reason descriptions generated from concept extraction  
- Create new endpoint that takes memory ID and returns related items  

**Risk:** Medium — requires leveraging existing search infrastructure. Must ensure the semantic search is fast enough (< 50ms response time). Also need to handle empty or non-existent memories gracefully (return helpful error).

---

## Recommendation Priority

As a user who relies on this tool daily for code context and observation management, here's my prioritization based on "bang for buck" — quick implementation with high utility:

### **Priority 1 (Implement First): Observation Weighting**

**Why #1:** Fastest to implement (~10 min), highest immediate value
- Let me tag observations as priority 90-100 and have them surface prominently in search results  
- No new data structures needed; just add one number to the existing weighting formula
- Users already write some observations as "important issues" but they're weighted same as trivial notes

**Implementation:**
```python
# New payload validation
def folder_observe(kv, payload, priority=0):
    # Validate priority is 0-100 range
    if not (0 <= priority <= 100):
        raise ValueError("priority must be between 0 and 100")
```

**Time:** ~10 min  
**Risk:** None — modifies existing RRF function slightly  

---

### **Priority 2: Folder Statistics Endpoint**

**Why #2:** Simplest implementation with high utility for daily users
- Users constantly need this info but it's not easily available via API calls
- Just needs a few more lines of code following Flask pattern  
- Query existing data without any schema changes

**Implementation:**
```python
@app.route('/folder/stats/<path:folder_path>')
def folder_stats():
    # ~5 min implementation
```

**Time:** ~5 min  
**Risk:** None — new endpoint, minimal queries  

---

### **Priority 3: Observation Source Tracking**

**Why #3:** Low risk, very useful for detecting patterns
- Track which files user has observed about certain topics helps detect learning cycles  
- One extra field in KV value dict — no schema changes  
- Helps spot when user re-implements same code after reading tutorial

**Implementation:**
```python
def folder_observe(kv, payload, source_file=None):
    # ~10 min implementation
```

**Time:** ~15 min  
**Risk:** Low — one optional field addition  

---

### **Priority 4: Memory Version History**

**Why #4:** Good safety feature, relatively simple to implement
- Prevents accidental data loss  
- Version history array in JSON value column is trivial to add  
- Users love being able to see evolution of memories they've written

**Implementation:**
```python
def remember(kv, data):
    # ~15 min implementation
```

**Time:** ~20 min  
**Risk:** Low — append operation, no schema changes  

---

## Summary

| Feature | Est. Time | Risk Level | User Impact |
|---------|-----------|------------|-------------|
| Observation Weighting | 10 min | None | High (control over search ranking) |
| Folder Stats Endpoint | 5 min | None | High (quick stats access) |
| Source Tracking | 15 min | Low | Medium (detect patterns) |
| Memory Versioning | 20 min | Low | Medium (safety feature) |

All four fit seamlessly into existing Flask/SQLite architecture without requiring any architectural changes or new dependencies. They leverage current infrastructure and enhance the user experience incrementally.
