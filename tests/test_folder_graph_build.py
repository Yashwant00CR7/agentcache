"""Unit tests for folderColor() and folder_graph_build() — REQ-023–REQ-028."""

import pytest

from agentcache.db import StateKV
from agentcache.functions import KV, folder_graph_build
from agentcache.functions import folder_color as folderColor

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def kv(tmp_path):
    """Return a fresh in-file StateKV backed by a temp SQLite database."""
    db_file = str(tmp_path / "test.db")
    return StateKV(db_path=db_file)


def _write_pair(
    kv: StateKV,
    folder_path: str,
    agent_id: str,
    obs_texts: list = None,
    obs_count: int = None,
) -> None:
    """Insert a (folder_path, agent_id) entry into KV.folders and optionally write observations."""
    obs_texts = obs_texts or []
    count = obs_count if obs_count is not None else len(obs_texts)

    # Write folders index entry
    index_key = f"{folder_path}:{agent_id}"
    kv.set(
        KV.folders,
        index_key,
        {
            "folderPath": folder_path,
            "agentId": agent_id,
            "obsCount": count,
            "lastUpdated": "2025-01-15T12:00:00.000Z",
        },
    )

    # Write observation objects if text supplied
    for i, text in enumerate(obs_texts):
        obs_id = f"obs_{folder_path.replace('/', '_')}_{agent_id}_{i}"
        obs = {
            "id": obs_id,
            "folderPath": folder_path,
            "agentId": agent_id,
            "timestamp": "2025-01-15T12:00:00.000Z",
            "text": text,
            "type": "other",
            "title": f"title {i}",
            "concepts": [],
            "files": [],
            "importance": 5,
        }
        kv.set(KV.folder_obs(folder_path, agent_id), obs_id, obs)


# ---------------------------------------------------------------------------
# Tests — folderColor helper
# ---------------------------------------------------------------------------


def test_folder_color_returns_hsl_string():
    """folderColor should return a string matching hsl(...) format."""
    color = folderColor("projects/alpha")
    assert color.startswith("hsl(")
    assert color.endswith(")")


def test_folder_color_deterministic():
    """Same path always returns the same color."""
    assert folderColor("projects/alpha") == folderColor("projects/alpha")


def test_folder_color_different_paths_produce_different_colors():
    """Different paths should (almost always) produce different colors."""
    # Use very distinct paths to ensure hash difference
    assert folderColor("projects/alpha") != folderColor(
        "projects/omega-completely-different"
    )


def test_folder_color_hsl_values_in_range():
    """HSL values should be within expected ranges."""
    color = folderColor("some/path")
    # Strip "hsl(" and ")" then parse
    inner = color[4:-1]  # e.g. "200, 70%, 55%"
    parts = [p.strip().rstrip("%") for p in inner.split(",")]
    hue, sat, lig = int(parts[0]), int(parts[1]), int(parts[2])
    assert 0 <= hue < 360
    assert 55 <= sat <= 79  # 55 + (h % 25)
    assert 38 <= lig <= 51  # 38 + (h % 14)


def test_folder_color_empty_string():
    """folderColor on empty string should not raise."""
    color = folderColor("")
    assert color.startswith("hsl(")


# ---------------------------------------------------------------------------
# Tests — empty KV returns empty graph (REQ-023)
# ---------------------------------------------------------------------------


def test_empty_kv_returns_empty_graph(kv):
    """Empty KV returns {nodes: [], edges: []}."""
    result = folder_graph_build(kv)
    assert result == {"nodes": [], "edges": []}


# ---------------------------------------------------------------------------
# Tests — node construction (REQ-023, REQ-024)
# ---------------------------------------------------------------------------


def test_one_node_per_unique_folder_path(kv):
    """Two agents in the same folder produce a single node (REQ-023)."""
    _write_pair(kv, "projects/alpha", "kiro", obs_count=3)
    _write_pair(kv, "projects/alpha", "claude", obs_count=2)

    result = folder_graph_build(kv)
    assert len(result["nodes"]) == 1
    node = result["nodes"][0]
    assert node["folderPath"] == "projects/alpha"


def test_multiple_folders_produce_multiple_nodes(kv):
    """Each distinct folder_path produces exactly one node."""
    _write_pair(kv, "projects/alpha", "kiro")
    _write_pair(kv, "projects/beta", "kiro")
    _write_pair(kv, "projects/gamma", "claude")

    result = folder_graph_build(kv)
    folder_paths = {n["folderPath"] for n in result["nodes"]}
    assert folder_paths == {"projects/alpha", "projects/beta", "projects/gamma"}


def test_node_fields_present(kv):
    """Each node contains all required fields (REQ-024)."""
    _write_pair(kv, "projects/alpha", "kiro", obs_count=5)

    result = folder_graph_build(kv)
    node = result["nodes"][0]
    assert "id" in node
    assert "label" in node
    assert "folderPath" in node
    assert "agentIds" in node
    assert "obsCount" in node
    assert "color" in node


def test_node_id_equals_folder_path(kv):
    """Node id is the folderPath string."""
    _write_pair(kv, "projects/alpha", "kiro")

    result = folder_graph_build(kv)
    node = result["nodes"][0]
    assert node["id"] == "projects/alpha"
    assert node["folderPath"] == "projects/alpha"


def test_node_label_is_basename(kv):
    """Node label is the last path component."""
    _write_pair(kv, "home/user/projects/myapp", "kiro")

    result = folder_graph_build(kv)
    node = result["nodes"][0]
    assert node["label"] == "myapp"


def test_node_agent_ids_aggregated_and_sorted(kv):
    """agentIds is the sorted union of all agents for that folder."""
    _write_pair(kv, "projects/alpha", "zorro", obs_count=1)
    _write_pair(kv, "projects/alpha", "alice", obs_count=1)
    _write_pair(kv, "projects/alpha", "bob", obs_count=1)

    result = folder_graph_build(kv)
    node = result["nodes"][0]
    assert node["agentIds"] == ["alice", "bob", "zorro"]


def test_node_obs_count_summed_across_agents(kv):
    """obsCount is the sum across all agents for that folder."""
    _write_pair(kv, "projects/alpha", "kiro", obs_count=4)
    _write_pair(kv, "projects/alpha", "claude", obs_count=6)

    result = folder_graph_build(kv)
    node = result["nodes"][0]
    assert node["obsCount"] == 10


def test_node_color_is_hsl(kv):
    """Node color comes from folderColor and is an HSL string."""
    _write_pair(kv, "projects/alpha", "kiro")

    result = folder_graph_build(kv)
    node = result["nodes"][0]
    assert node["color"].startswith("hsl(")
    # Must match folderColor directly
    assert node["color"] == folderColor("projects/alpha")


# ---------------------------------------------------------------------------
# Tests — same-parent edges (REQ-025)
# ---------------------------------------------------------------------------


def test_same_parent_edge_created(kv):
    """Two folders with the same parent get a same-parent edge."""
    _write_pair(kv, "projects/alpha", "kiro")
    _write_pair(kv, "projects/beta", "kiro")  # both under "projects"

    result = folder_graph_build(kv)
    same_parent_edges = [e for e in result["edges"] if e["type"] == "same-parent"]
    assert len(same_parent_edges) == 1
    edge = same_parent_edges[0]
    assert set([edge["source"], edge["target"]]) == {"projects/alpha", "projects/beta"}


def test_no_same_parent_edge_for_different_parents(kv):
    """Folders with different parents do not get a same-parent edge."""
    _write_pair(kv, "projects/alpha", "kiro")
    _write_pair(kv, "work/beta", "kiro")

    result = folder_graph_build(kv)
    same_parent_edges = [e for e in result["edges"] if e["type"] == "same-parent"]
    assert same_parent_edges == []


def test_same_parent_edge_only_for_sharing_pairs(kv):
    """Only pairs sharing a parent get same-parent edges; non-sharing pairs do not."""
    _write_pair(kv, "a/x", "kiro")
    _write_pair(kv, "a/y", "kiro")  # shares parent "a" with a/x
    _write_pair(kv, "b/z", "kiro")  # different parent "b"

    result = folder_graph_build(kv)
    same_parent_edges = [e for e in result["edges"] if e["type"] == "same-parent"]
    assert len(same_parent_edges) == 1
    edge = same_parent_edges[0]
    assert set([edge["source"], edge["target"]]) == {"a/x", "a/y"}


# ---------------------------------------------------------------------------
# Tests — cross-reference edges (REQ-026)
# ---------------------------------------------------------------------------


def test_cross_ref_edge_when_obs_mentions_other_folder(kv):
    """A cross-ref edge is created when folder A's obs text mentions folder B's path."""
    _write_pair(
        kv, "projects/alpha", "kiro", obs_texts=["I worked on projects/beta today"]
    )
    _write_pair(kv, "projects/beta", "kiro", obs_texts=["nothing"])

    result = folder_graph_build(kv)
    cross_edges = [e for e in result["edges"] if e["type"] == "cross-ref"]
    assert len(cross_edges) >= 1
    sources_targets = {(e["source"], e["target"]) for e in cross_edges}
    assert ("projects/alpha", "projects/beta") in sources_targets


def test_no_cross_ref_edge_when_no_mention(kv):
    """No cross-ref edge when obs texts don't mention another folder path."""
    _write_pair(kv, "projects/alpha", "kiro", obs_texts=["Just some work here"])
    _write_pair(kv, "projects/beta", "kiro", obs_texts=["Unrelated content"])

    result = folder_graph_build(kv)
    cross_edges = [e for e in result["edges"] if e["type"] == "cross-ref"]
    assert cross_edges == []


def test_cross_ref_edge_from_title_mention(kv):
    """Cross-ref edges are also detected via obs titles."""
    _write_pair(kv, "projects/alpha", "kiro", obs_texts=["some text"])
    # Manually insert obs with a title that mentions the other folder
    obs = {
        "id": "obs_special",
        "folderPath": "projects/alpha",
        "agentId": "kiro",
        "timestamp": "2025-01-15T12:00:00.000Z",
        "text": "normal text",
        "type": "other",
        "title": "work on projects/beta",
        "concepts": [],
        "files": [],
        "importance": 5,
    }
    kv.set(KV.folder_obs("projects/alpha", "kiro"), "obs_special", obs)
    _write_pair(kv, "projects/beta", "kiro", obs_texts=["nothing"])

    result = folder_graph_build(kv)
    cross_edges = [e for e in result["edges"] if e["type"] == "cross-ref"]
    sources = {e["source"] for e in cross_edges}
    assert "projects/alpha" in sources


# ---------------------------------------------------------------------------
# Tests — agent-shared edges (REQ-027)
# ---------------------------------------------------------------------------


def test_agent_shared_edge_created(kv):
    """Two folders with a common agent get an agent-shared edge."""
    _write_pair(kv, "projects/alpha", "kiro")
    _write_pair(kv, "projects/beta", "kiro")  # same agent "kiro"

    result = folder_graph_build(kv)
    agent_edges = [e for e in result["edges"] if e["type"] == "agent-shared"]
    assert len(agent_edges) >= 1
    edge = agent_edges[0]
    assert set([edge["source"], edge["target"]]) == {"projects/alpha", "projects/beta"}


def test_no_agent_shared_edge_when_no_common_agent(kv):
    """Folders with no common agents do not get an agent-shared edge."""
    _write_pair(kv, "projects/alpha", "kiro")
    _write_pair(kv, "projects/beta", "claude")  # different agents

    result = folder_graph_build(kv)
    agent_edges = [e for e in result["edges"] if e["type"] == "agent-shared"]
    assert agent_edges == []


def test_agent_shared_edge_with_partial_overlap(kv):
    """Two folders with one common agent among several agents still get an edge."""
    _write_pair(kv, "projects/alpha", "kiro")
    _write_pair(kv, "projects/alpha", "claude")
    _write_pair(kv, "projects/beta", "claude")
    _write_pair(kv, "projects/beta", "cursor")

    result = folder_graph_build(kv)
    agent_edges = [e for e in result["edges"] if e["type"] == "agent-shared"]
    endpoints = {frozenset([e["source"], e["target"]]) for e in agent_edges}
    assert frozenset({"projects/alpha", "projects/beta"}) in endpoints


# ---------------------------------------------------------------------------
# Tests — edge deduplication (REQ-028)
# ---------------------------------------------------------------------------


def test_no_duplicate_edges(kv):
    """No two edges share the same (source, target, type) pair."""
    _write_pair(kv, "projects/alpha", "kiro", obs_texts=["mentions projects/beta"])
    _write_pair(kv, "projects/beta", "kiro", obs_texts=["mentions projects/alpha"])

    result = folder_graph_build(kv)
    seen = set()
    for edge in result["edges"]:
        key = (frozenset([edge["source"], edge["target"]]), edge["type"])
        assert key not in seen, f"Duplicate edge: {edge}"
        seen.add(key)


def test_ab_and_ba_treated_as_same_edge(kv):
    """(a, b, type) and (b, a, type) are considered the same edge."""
    # Both folders reference each other — should produce only one cross-ref edge
    _write_pair(
        kv, "projects/alpha", "kiro", obs_texts=["See also projects/beta for details"]
    )
    _write_pair(
        kv, "projects/beta", "kiro", obs_texts=["Related to projects/alpha work"]
    )

    result = folder_graph_build(kv)
    cross_edges = [e for e in result["edges"] if e["type"] == "cross-ref"]
    # Should be exactly 1 cross-ref edge (not 2)
    assert len(cross_edges) == 1


def test_same_parent_and_agent_shared_are_separate_edge_types(kv):
    """same-parent and agent-shared edges between the same pair are both kept."""
    # Both folders share parent "projects" AND share agent "kiro"
    _write_pair(kv, "projects/alpha", "kiro")
    _write_pair(kv, "projects/beta", "kiro")

    result = folder_graph_build(kv)
    edge_types = {e["type"] for e in result["edges"]}
    # We expect both types to appear
    assert "same-parent" in edge_types
    assert "agent-shared" in edge_types


# ---------------------------------------------------------------------------
# Tests — return structure
# ---------------------------------------------------------------------------


def test_return_has_nodes_and_edges_keys(kv):
    """Result always has 'nodes' and 'edges' keys."""
    _write_pair(kv, "projects/alpha", "kiro")
    result = folder_graph_build(kv)
    assert "nodes" in result
    assert "edges" in result


def test_edge_has_required_fields(kv):
    """Each edge has source, target, and type fields."""
    _write_pair(kv, "projects/alpha", "kiro")
    _write_pair(kv, "projects/beta", "kiro")

    result = folder_graph_build(kv)
    for edge in result["edges"]:
        assert "source" in edge
        assert "target" in edge
        assert "type" in edge


def test_single_folder_produces_no_edges(kv):
    """A graph with only one folder produces no edges."""
    _write_pair(kv, "projects/alpha", "kiro")

    result = folder_graph_build(kv)
    assert len(result["nodes"]) == 1
    assert result["edges"] == []


def test_edge_types_are_valid(kv):
    """All edge types are one of the three valid values."""
    _write_pair(kv, "projects/alpha", "kiro", obs_texts=["mentions projects/beta"])
    _write_pair(kv, "projects/beta", "kiro")

    result = folder_graph_build(kv)
    valid_types = {"same-parent", "cross-ref", "agent-shared"}
    for edge in result["edges"]:
        assert edge["type"] in valid_types
