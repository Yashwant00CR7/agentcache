"""Unit tests for folder_graph_build (REQ-023–REQ-028)."""

import datetime
import os

from agentcache.db import StateKV
from agentcache.functions import folder_graph_build, folder_observe


def make_kv(tmp_path):
    db_path = os.path.join(str(tmp_path), "test.db")
    return StateKV(db_path=db_path)


def add_obs(kv, folder, agent="kiro", text="obs"):
    folder_observe(
        kv,
        {
            "folderPath": folder,
            "agentId": agent,
            "text": text,
            "timestamp": datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        },
    )


class TestGraphEmpty:
    def test_empty_kv_returns_empty(self, tmp_path):
        kv = make_kv(tmp_path)
        result = folder_graph_build(kv)
        assert result == {"nodes": [], "edges": []}


class TestGraphNodes:
    def test_one_node_per_folder(self, tmp_path):
        kv = make_kv(tmp_path)
        add_obs(kv, "/home/user/proj-a", agent="kiro")
        add_obs(kv, "/home/user/proj-a", agent="claude")  # same folder, different agent
        add_obs(kv, "/home/user/proj-b", agent="kiro")
        result = folder_graph_build(kv)
        node_ids = [n["id"] for n in result["nodes"]]
        assert len(node_ids) == len(set(node_ids))  # no duplicates
        # 2 unique folders
        assert len(result["nodes"]) == 2

    def test_node_has_required_fields(self, tmp_path):
        kv = make_kv(tmp_path)
        add_obs(kv, "/home/user/proj")
        result = folder_graph_build(kv)
        node = result["nodes"][0]
        assert "id" in node
        assert "label" in node
        assert "folderPath" in node
        assert "agentIds" in node
        assert "obsCount" in node
        assert "color" in node

    def test_agent_ids_aggregated(self, tmp_path):
        kv = make_kv(tmp_path)
        add_obs(kv, "/home/user/proj", agent="kiro")
        add_obs(kv, "/home/user/proj", agent="claude")
        result = folder_graph_build(kv)
        node = result["nodes"][0]
        assert "kiro" in node["agentIds"]
        assert "claude" in node["agentIds"]


class TestGraphEdges:
    def test_same_parent_edge(self, tmp_path):
        kv = make_kv(tmp_path)
        add_obs(kv, "/home/user/proj/src")
        add_obs(kv, "/home/user/proj/tests")
        result = folder_graph_build(kv)
        same_parent = [e for e in result["edges"] if e["type"] == "same-parent"]
        assert len(same_parent) >= 1

    def test_no_duplicate_edges(self, tmp_path):
        kv = make_kv(tmp_path)
        add_obs(kv, "/home/user/proj/src")
        add_obs(kv, "/home/user/proj/tests")
        result = folder_graph_build(kv)
        edge_keys = [(e["source"], e["target"], e["type"]) for e in result["edges"]]
        assert len(edge_keys) == len(set(edge_keys))

    def test_agent_shared_edge(self, tmp_path):
        kv = make_kv(tmp_path)
        add_obs(kv, "/home/user/proj-a", agent="kiro")
        add_obs(kv, "/home/user/proj-b", agent="kiro")
        result = folder_graph_build(kv)
        agent_edges = [e for e in result["edges"] if e["type"] == "agent-shared"]
        assert len(agent_edges) >= 1

    def test_cross_ref_edge(self, tmp_path):
        kv = make_kv(tmp_path)
        fp_b = "home/user/proj-b"
        add_obs(kv, "/home/user/proj-a", text=f"Modified files in {fp_b}")
        add_obs(kv, "/home/user/proj-b", text="Normal work")
        result = folder_graph_build(kv)
        cross_ref = [e for e in result["edges"] if e["type"] == "cross-ref"]
        assert len(cross_ref) >= 1
