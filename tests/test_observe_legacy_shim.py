"""#52 — /observe server-side shim for the legacy post_tool_use hook payload.

The `post_tool_use` hook posts ``{sessionId, project, cwd, data:{tool_name,
tool_input, tool_output}}`` to /observe, but the route requires the folder
contract ``{folderPath, agentId, text}``. This shim teaches /observe to map the
legacy shape server-side (folderPath = cwd or project, agentId = sessionId,
text = rendered(data)) while keeping the folder contract working.
"""

from agentcache.core.observation_store import normalize_folder_path


def _read_back(client, folder_path, agent_id):
    resp = client.get(
        "/agentcache/folder/observations",
        query_string={"folderPath": folder_path, "agentId": agent_id},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["observations"]


def test_observe_accepts_legacy_hook_payload(app_client):
    payload = {
        "hookType": "post_tool_use",
        "sessionId": "sess-legacy-1",
        "project": "myproj",
        "cwd": "home/user/myproj",
        "timestamp": "2026-07-27T10:00:00Z",
        "data": {
            "tool_name": "Edit",
            "tool_input": {"file_path": "src/app.py"},
            "tool_output": "edited 3 lines",
        },
    }
    resp = app_client.post("/agentcache/observe", json=payload)
    assert resp.status_code == 201, resp.get_data(as_text=True)

    fp = normalize_folder_path("home/user/myproj")
    obs = _read_back(app_client, fp, "sess-legacy-1")
    assert len(obs) == 1
    o = obs[0]
    assert o["folderPath"] == fp
    assert o["agentId"] == "sess-legacy-1"
    # Rendered text carries the tool signal.
    assert "Edit" in o["text"]
    assert "src/app.py" in o["text"]
    assert "edited 3 lines" in o["text"]


def test_observe_legacy_prefers_cwd_over_project(app_client):
    payload = {
        "sessionId": "sess-cwd",
        "project": "projname",
        "cwd": "cwd/path/here",
        "timestamp": "2026-07-27T10:00:00Z",
        "data": {"tool_name": "Read", "tool_input": {}, "tool_output": "ok"},
    }
    resp = app_client.post("/agentcache/observe", json=payload)
    assert resp.status_code == 201, resp.get_data(as_text=True)

    assert _read_back(app_client, normalize_folder_path("cwd/path/here"), "sess-cwd")
    assert not _read_back(app_client, normalize_folder_path("projname"), "sess-cwd")


def test_observe_legacy_falls_back_to_project_when_no_cwd(app_client):
    payload = {
        "sessionId": "sess-proj",
        "project": "projonly",
        "cwd": "",
        "timestamp": "2026-07-27T10:00:00Z",
        "data": {"tool_name": "Bash", "tool_input": {}, "tool_output": "done"},
    }
    resp = app_client.post("/agentcache/observe", json=payload)
    assert resp.status_code == 201, resp.get_data(as_text=True)
    assert _read_back(app_client, normalize_folder_path("projonly"), "sess-proj")


def test_observe_new_contract_still_works(app_client):
    payload = {
        "folderPath": "newcontract",
        "agentId": "agent-x",
        "text": "an ordinary observation",
        "timestamp": "2026-07-27T10:00:00Z",
    }
    resp = app_client.post("/agentcache/observe", json=payload)
    assert resp.status_code == 201, resp.get_data(as_text=True)
    obs = _read_back(app_client, normalize_folder_path("newcontract"), "agent-x")
    assert len(obs) == 1
    assert obs[0]["text"] == "an ordinary observation"


def test_observe_missing_both_shapes_400(app_client):
    resp = app_client.post("/agentcache/observe", json={"hookType": "post_tool_use"})
    assert resp.status_code == 400
