# Tasks: Deduplication & Dashboard Fixes

## Task 1: Add dedup KV scope and per-pair locking to functions.py
- [x] Add `KV.obs_dedup(folder_path, agent_id)` static method to the `KV` class in `src/functions.py`
- [x] Add module-level `_dedup_locks: Dict[str, threading.Lock] = {}` and `_dedup_locks_meta = threading.Lock()` in `src/functions.py`
- [x] Add helper `_get_dedup_lock(folder_path: str, agent_id: str) -> threading.Lock` in `src/functions.py`
- [x] Mirror `obs_dedup` scope in `src/storage/scopes.py`

## Task 2: Add dedup check to `folder_observe` in functions.py
- [x] After `safe_text` is computed (post strip_private_data), compute fingerprint: `hashlib.sha256(safe_text[:4000].strip().lower().encode()).hexdigest()`
- [x] Acquire `_get_dedup_lock(folder_path, agent_id)` before the dedup check
- [x] Look up `kv.get(KV.obs_dedup(folder_path, agent_id), fingerprint)`
- [x] If found, release lock and return `{"observationId": existing["obsId"], "deduplicated": True}`
- [x] After the obs is written to KV (step 6 in the existing pipeline), write the dedup index entry: `kv.set(KV.obs_dedup(folder_path, agent_id), fingerprint, {"obsId": obs_id, "timestamp": timestamp})`
- [x] Release the lock after the dedup index write
- [x] Ensure the lock is always released (use try/finally)

## Task 3: Add `dedup_folder_observations` function to functions.py
- [x] Define `def dedup_folder_observations(kv: StateKV, folder_path_raw: Optional[str], agent_id_raw: Optional[str]) -> Dict[str, Any]`
- [x] If both are None, iterate all pairs from `kv.list(KV.folders)`; otherwise validate and normalize the single pair
- [x] For each pair: load all obs, group by `sha256(obs["text"][:4000].strip().lower())`, keep earliest by timestamp, delete duplicates via `forget(kv, {"folderPath": fp, "agentId": aid, "observationIds": [ids_to_delete]})`
- [x] After deletion, rebuild the dedup index for the pair from scratch
- [x] Return `{"deduplicated": total_removed, "pairs_processed": n, "kept": total_kept}`

## Task 4: Add `POST /agentmemory/folder/dedup` REST endpoint
- [x] Add route in `src/routes/observations.py`:
  ```python
  @observations_bp.route("/agentmemory/folder/dedup", methods=["POST"])
  def api_folder_dedup():
  ```
- [x] Apply `_check_auth()`
- [x] Parse `folderPath` and `agentId` from request body (both optional)
- [x] Call `functions.dedup_folder_observations(_get_kv(), folder_path, agent_id)`
- [x] Return result as JSON 200

## Task 5: Add `memory_dedup` MCP tool
- [x] Add tool schema to `GET /agentmemory/mcp/tools` in `src/routes/mcp.py`:
  ```python
  {
      "name": "memory_dedup",
      "description": "Remove duplicate observations from a (folderPath, agentId) pair or all pairs.",
      "inputSchema": {
          "type": "object",
          "properties": {
              "folderPath": {"type": "string", "description": "Folder path to deduplicate (optional — omit for all)"},
              "agentId": {"type": "string", "description": "Agent ID to deduplicate (optional — omit for all)"},
          }
      }
  }
  ```
- [x] Add dispatch branch in `POST /agentmemory/mcp/tools`:
  ```python
  elif name == "memory_dedup":
      res = functions.dedup_folder_observations(kv, args.get("folderPath"), args.get("agentId"))
      text_out = json.dumps(res, indent=2)
  ```

## Task 6: Fix viewer — Back button and Delete folder button (data-action pattern)
- [x] In `loadFolderDetail`, replace `onclick="loadFolders()"` on the Back button with `data-action="back-to-folders"`
- [x] Replace inline `onclick="deleteFolderMemory(...)"` on the Delete folder button with `data-action="delete-folder"` plus `data-folder-path` and `data-agent-id` attributes (values must go through `esc()`)
- [x] Add handler for `back-to-folders` in the global `document.addEventListener('click', ...)` dispatcher
- [x] Add handler for `delete-folder` in the dispatcher: reads data attributes, calls `confirmDeleteFolder(fp, aid)`
- [x] Implement `confirmDeleteFolder(fp, aid)`: shows modal with confirmation message, on confirm calls `apiPost('forget', {folderPath: fp, agentId: aid})` then `loadFolders()`
- [x] Remove the now-dead `deleteFolderMemory` function

## Task 7: Add per-observation delete button to folder detail view
- [x] In `loadFolderDetail`, add to each observation card:
  - A "Delete" button: `data-action="delete-obs"`, `data-obs-id`, `data-folder-path`, `data-agent-id` (all through `esc()`)
- [x] Add handler for `delete-obs` in the dispatcher: calls `confirmDeleteObs(obsId, fp, aid)`
- [x] Implement `confirmDeleteObs(obsId, fp, aid)`: shows modal, on confirm calls `apiPost('forget', {folderPath: fp, agentId: aid, observationIds: [obsId]})`, then removes the card element from the DOM

## Task 8: Add bulk checkbox delete to folder detail view
- [x] Add a toolbar row above the observations list in `loadFolderDetail` with:
  - "Select all" checkbox: `id="obs-select-all"`
  - "Delete selected" button: `data-action="delete-obs-selected"`, disabled initially
- [x] Add a checkbox to each observation card wrapper: `class="obs-checkbox"`, `data-obs-id`
- [x] Wire `change` on `#obs-select-all` to toggle all checkboxes
- [x] Wire `change` on each `.obs-checkbox` to update a `selectedObsIds` Set and enable/disable the "Delete selected" button
- [x] Add handler for `delete-obs-selected` in the dispatcher: reads `selectedObsIds`, calls `apiPost('forget', {folderPath, agentId, observationIds: [...selectedObsIds]})`, removes all deleted cards from DOM

## Task 9: Fix Tools tab double-parse bug
- [x] In `renderToolsTab`, replace the `apiFetchRaw` call inside the "Run" button handler with a direct `fetch()` call:
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
- [x] Update history recording to use `ok` and `text` from the above
