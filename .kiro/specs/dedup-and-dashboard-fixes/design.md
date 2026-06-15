# Design: Deduplication & Dashboard Fixes

## 1. Root Cause Analysis

### Duplicate observations
`folder_observe()` in `src/functions.py` calls `generate_id("fobs")` unconditionally and writes the result. There is no idempotency check. Agents that call `agent_observe` on every hook invocation with the same text (e.g. a status line logged on every file edit) produce hundreds of identical records.

### Broken viewer buttons
- The folder detail view is rendered via `innerHTML` with inline `onclick="loadFolders()"` and `onclick="deleteFolderMemory(...)"`. These work in isolation but are brittle and inconsistent with the rest of the app's `data-action` delegation pattern.
- `deleteFolderMemory` calls `apiFetchRaw('/agentmemory/forget', {...})` — the path resolution in `apiFetchRaw` prepends `REST` correctly, but the `Content-Type` header is not set in the options object passed (only in `headers` sub-key), so Flask receives an empty body and `forget` gets `{}`.
- The Tools tab "Run" button calls `apiFetchRaw(...)` and then `await result.text()`. Because `apiFetchRaw` already calls `res.json()`, `result` is a plain JS object, not a `Response`, so `.text()` is not a function → `TypeError`.

---

## 2. Deduplication Design

### 2a. Fingerprint scope
A new KV scope `mem:obs_dedup:{safe_path}:{agent_id}` stores:
- key = SHA-256 hex of `normalized_text` (lowercased, whitespace-collapsed, first 4000 chars)
- value = `{"obsId": "<first_obs_id>", "timestamp": "<ts>"}`

This is a O(1) lookup before any write.

### 2b. Concurrency
A `threading.Lock` per `(folder_path, agent_id)` pair is held for the duration of the dedup-check + write window. A module-level `dict[str, Lock]` keyed by `f"{folder_path}:{agent_id}"` with a global meta-lock for dict access is sufficient for the single-process Flask/Werkzeug model.

### 2c. `folder_observe` changes (functions.py)
```
fingerprint = sha256(safe_text[:4000].strip().lower())
existing = kv.get(KV.obs_dedup(folder_path, agent_id), fingerprint)
if existing:
    return {"observationId": existing["obsId"], "deduplicated": True}
# ... proceed with normal write ...
kv.set(KV.obs_dedup(folder_path, agent_id), fingerprint, {"obsId": obs_id, "timestamp": timestamp})
```

### 2d. New KV scope in `KV` class
```python
@staticmethod
def obs_dedup(folder_path: str, agent_id: str) -> str:
    safe_path = folder_path.replace("\\", "/").strip("/")
    safe_agent = agent_id.strip()
    return f"mem:obs_dedup:{safe_path}:{safe_agent}"
```

Mirror in `src/storage/scopes.py`.

### 2e. Cleanup endpoint `dedup_folder_observations()`
New function in `functions.py`:
1. Load all obs for a (folder, agent) pair.
2. Build fingerprint → list of obs sorted by timestamp asc.
3. Keep the first, delete the rest via the existing `forget` path (which handles BM25/vector cleanup).
4. Rebuild the dedup index from scratch for that pair.
5. Return counts.

New route in `src/routes/observations.py`: `POST /agentmemory/folder/dedup`.

---

## 3. Viewer Dashboard Fixes

### 3a. Observation cards — per-row delete button
In `loadFolderDetail`, each observation div gets:
```html
<button class="btn btn-danger" style="..."
  data-action="delete-obs"
  data-obs-id="<id>"
  data-folder-path="<fp>"
  data-agent-id="<aid>">Delete</button>
```
The global `document.addEventListener('click', ...)` handler gains a new branch:
```js
if (action === 'delete-obs') {
    var obsId = target.getAttribute('data-obs-id');
    var fp = target.getAttribute('data-folder-path');
    var aid = target.getAttribute('data-agent-id');
    confirmDeleteObs(obsId, fp, aid);
}
```
`confirmDeleteObs` shows a modal, then calls `apiPost('forget', {folderPath: fp, agentId: aid, observationIds: [obsId]})` and removes the card from the DOM.

### 3b. Checkbox bulk delete
The folder detail toolbar gets a "Select all" checkbox (`id="obs-select-all"`) and a "Delete selected" button (`data-action="delete-obs-selected"`, disabled by default).

Each observation card wrapper gets `data-obs-id`, a checkbox `class="obs-checkbox"`.

`change` events on checkboxes update a `selectedObsIds` Set and toggle the button's disabled state.

### 3c. Back button — data-action
Replace inline `onclick="loadFolders()"` with:
```html
<button class="btn" data-action="back-to-folders">← Back</button>
```
Handler in the global click dispatcher:
```js
if (action === 'back-to-folders') { loadFolders(); return; }
```

### 3d. Delete folder button — data-action + data attributes
Replace inline `onclick="deleteFolderMemory(...)"` with:
```html
<button class="btn btn-danger"
  data-action="delete-folder"
  data-folder-path="<fp>"
  data-agent-id="<aid>">Delete folder memory</button>
```
Handler:
```js
if (action === 'delete-folder') {
    var fp = target.getAttribute('data-folder-path');
    var aid = target.getAttribute('data-agent-id');
    confirmDeleteFolder(fp, aid);
}
```
`confirmDeleteFolder` shows the modal, calls `apiPost('forget', {folderPath: fp, agentId: aid})`, then `loadFolders()`.

### 3e. Tools tab run button — fix double-parse
Replace the `apiFetchRaw` call in the tool runner with a raw `fetch`:
```js
var token = getViewerToken();
var headers = {'Content-Type': 'application/json'};
if (token) headers['Authorization'] = 'Bearer ' + token;
var rawRes = await fetch(REST + '/agentmemory/mcp/tools', {
    method: 'POST',
    headers: headers,
    body: JSON.stringify({ name: name, arguments: args })
});
var text = await rawRes.text();
var ok = rawRes.ok;
```

---

## 4. Files Changed

| File | Change |
|------|--------|
| `src/functions.py` | Add `KV.obs_dedup`, dedup lock dict, dedup check in `folder_observe`, new `dedup_folder_observations` function |
| `src/storage/scopes.py` | Mirror `KV.obs_dedup` |
| `src/routes/observations.py` | Add `POST /agentmemory/folder/dedup` route |
| `src/routes/mcp.py` | Add `memory_dedup` tool schema + dispatch |
| `src/viewer/index.html` | Fix all broken buttons, add per-obs delete, add bulk delete, fix tools tab |

---

## 5. Security Notes
- The dedup fingerprint is computed over already-stripped (private-data-removed) text, so secrets are not persisted in fingerprint keys.
- The cleanup endpoint is auth-gated identical to all other write endpoints.
- `data-` attribute values are HTML-escaped via the existing `esc()` function before injection into innerHTML — no XSS risk.
- The per-pair lock prevents TOCTOU races under concurrent agent writes.
