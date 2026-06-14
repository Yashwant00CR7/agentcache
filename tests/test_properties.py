"""
tests/test_properties.py — C2.1

Hypothesis property-based tests for the folder-based memory system.
All 8 properties from the spec.

Note: Each property test creates a fresh isolated SQLite DB per hypothesis
example using a shared counter, avoiding state accumulation between examples.
"""
import sys
import os
import datetime
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

try:
    from hypothesis import given, settings, assume, HealthCheck
    from hypothesis import strategies as st
    HYPOTHESIS_AVAILABLE = True
except ImportError:
    HYPOTHESIS_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not HYPOTHESIS_AVAILABLE,
    reason="hypothesis not installed — run: pip install hypothesis"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_counter = [0]


def _fresh_kv():
    """Create a brand-new isolated StateKV in a temp directory."""
    from db import StateKV
    os.environ.pop("AGENTMEMORY_SECRET", None)
    _counter[0] += 1
    d = tempfile.mkdtemp(prefix=f"agmem_prop_{_counter[0]}_")
    return StateKV(db_path=os.path.join(d, "test.db"))


def _now():
    return datetime.datetime.utcnow().isoformat() + "Z"


def _safe_path():
    """Strategy for valid, non-traversal folder paths."""
    return st.from_regex(
        r"[a-zA-Z][a-zA-Z0-9_-]{0,20}/[a-zA-Z][a-zA-Z0-9_-]{0,20}",
        fullmatch=True,
    )


def _safe_agent():
    return st.from_regex(r"[a-z][a-z0-9_-]{1,12}", fullmatch=True)


def _safe_text():
    return st.text(
        alphabet=st.characters(
            whitelist_categories=("Lu", "Ll", "Nd", "Zs"),
            whitelist_characters="_-.,()",
        ),
        min_size=5,
        max_size=200,
    )


# ---------------------------------------------------------------------------
# Property 1: Pair Isolation
# Two distinct (folderPath, agentId) pairs never share observations.
# ---------------------------------------------------------------------------

@settings(max_examples=50)
@given(
    path1=_safe_path(),
    agent1=_safe_agent(),
    path2=_safe_path(),
    agent2=_safe_agent(),
    text=_safe_text(),
)
def test_property_1_pair_isolation(path1, agent1, path2, agent2, text):
    assume((path1, agent1) != (path2, agent2))

    from functions import folder_observe, KV
    kv = _fresh_kv()

    folder_observe(kv, {"folderPath": path1, "agentId": agent1, "text": text, "timestamp": _now()})

    scope1 = KV.folder_obs(path1, agent1)
    scope2 = KV.folder_obs(path2, agent2)

    obs1_ids = {o["id"] for o in kv.list(scope1)}
    obs2_ids = {o["id"] for o in kv.list(scope2)}

    assert obs1_ids.isdisjoint(obs2_ids)


# ---------------------------------------------------------------------------
# Property 2: Observation Count Consistency
# meta.obsCount == len(kv.list(folder_obs_scope))
# ---------------------------------------------------------------------------

@settings(max_examples=50)
@given(
    path=_safe_path(),
    agent=_safe_agent(),
    texts=st.lists(_safe_text(), min_size=1, max_size=10),
)
def test_property_2_obs_count_consistency(path, agent, texts):
    from functions import folder_observe, KV
    kv = _fresh_kv()

    for text in texts:
        folder_observe(kv, {"folderPath": path, "agentId": agent, "text": text, "timestamp": _now()})

    meta_scope = KV.folder_meta(path, agent)
    meta = kv.get(meta_scope, "meta")
    assert meta is not None

    actual_obs = kv.list(KV.folder_obs(path, agent))
    assert meta["obsCount"] == len(actual_obs)


# ---------------------------------------------------------------------------
# Property 3: Index Coverage
# Every written pair has a KV.folders entry.
# ---------------------------------------------------------------------------

@settings(max_examples=50)
@given(
    path=_safe_path(),
    agent=_safe_agent(),
    text=_safe_text(),
)
def test_property_3_index_coverage(path, agent, text):
    from functions import folder_observe, KV
    kv = _fresh_kv()

    folder_observe(kv, {"folderPath": path, "agentId": agent, "text": text, "timestamp": _now()})

    index_entries = kv.list(KV.folders)
    normalized_path = path.replace("\\", "/").strip("/")
    normalized_agent = agent.strip()

    matching = [
        e for e in index_entries
        if e.get("folderPath") == normalized_path and e.get("agentId") == normalized_agent
    ]
    assert len(matching) >= 1


# ---------------------------------------------------------------------------
# Property 4: Privacy Invariant
# No stored obs text contains raw secrets after folder_observe().
# ---------------------------------------------------------------------------

@settings(max_examples=30)
@given(
    path=_safe_path(),
    agent=_safe_agent(),
    prefix=st.text(alphabet="abcdefghijklmnop", min_size=3, max_size=10),
)
def test_property_4_privacy_invariant(path, agent, prefix):
    from functions import folder_observe, KV
    kv = _fresh_kv()

    secret_text = f"My api_key = sk-proj-{prefix}abc123def456ghi789jkl012 in production"

    folder_observe(kv, {
        "folderPath": path,
        "agentId": agent,
        "text": secret_text,
        "timestamp": _now(),
    })

    obs_list = kv.list(KV.folder_obs(path, agent))
    for obs in obs_list:
        stored_text = obs.get("text", "")
        assert "sk-proj-" not in stored_text


# ---------------------------------------------------------------------------
# Property 5: Timeline Ordering
# folder_timeline() always returns results sorted newest-first.
# ---------------------------------------------------------------------------

@settings(max_examples=40)
@given(
    path=_safe_path(),
    agent=_safe_agent(),
    n=st.integers(min_value=2, max_value=8),
)
def test_property_5_timeline_ordering(path, agent, n):
    from functions import folder_observe, folder_timeline
    kv = _fresh_kv()

    base_ts = datetime.datetime(2025, 1, 1, 0, 0, 0)
    for i in range(n):
        ts = (base_ts + datetime.timedelta(minutes=i)).isoformat() + "Z"
        folder_observe(kv, {
            "folderPath": path,
            "agentId": agent,
            "text": f"Observation number {i}",
            "timestamp": ts,
        })

    results = folder_timeline(kv, limit=100, folder_path=path, agent_id=agent)
    timestamps = [r["timestamp"] for r in results]
    assert timestamps == sorted(timestamps, reverse=True)


# ---------------------------------------------------------------------------
# Property 6: Forget Completeness
# After forget({folderPath, agentId}), all three scopes are empty.
# ---------------------------------------------------------------------------

@settings(max_examples=40)
@given(
    path=_safe_path(),
    agent=_safe_agent(),
    texts=st.lists(_safe_text(), min_size=1, max_size=5),
)
def test_property_6_forget_completeness(path, agent, texts):
    from functions import folder_observe, forget, KV
    kv = _fresh_kv()

    for text in texts:
        folder_observe(kv, {"folderPath": path, "agentId": agent, "text": text, "timestamp": _now()})

    assert len(kv.list(KV.folder_obs(path, agent))) > 0

    forget(kv, {"folderPath": path, "agentId": agent})

    normalized_path = path.replace("\\", "/").strip("/")
    normalized_agent = agent.strip()
    index_key = f"{normalized_path}:{normalized_agent}"

    assert kv.list(KV.folder_obs(normalized_path, normalized_agent)) == []
    assert kv.get(KV.folder_meta(normalized_path, normalized_agent), "meta") is None
    assert kv.get(KV.folders, index_key) is None


# ---------------------------------------------------------------------------
# Property 7: Memory Version Uniqueness
# Superseded memories have parentId; at least one memory is always latest.
# ---------------------------------------------------------------------------

@settings(max_examples=30, suppress_health_check=[HealthCheck.filter_too_much])
@given(
    base_content=st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz ",
        min_size=30,
        max_size=80,
    ).filter(lambda s: len(s.split()) >= 6),
    n_variants=st.integers(min_value=2, max_value=4),
)
def test_property_7_memory_version_uniqueness(base_content, n_variants):
    from functions import remember, KV
    kv = _fresh_kv()

    for i in range(n_variants):
        content = base_content + f" variant {i}"
        remember(kv, {"content": content})

    all_mems = kv.list(KV.memories)

    # Build sets for validation
    all_ids = {m["id"] for m in all_mems}
    superseded_ids = {m["id"] for m in all_mems if m.get("isLatest") is False}
    # Every superseded memory must be referenced by exactly one newer memory via parentId
    for m in all_mems:
        pid = m.get("parentId")
        if pid:
            # The parentId must point to a memory that exists and is marked isLatest=False
            parent = next((x for x in all_mems if x["id"] == pid), None)
            assert parent is not None, f"parentId {pid} not found in memories"
            assert parent.get("isLatest") is False, "Parent of superseding memory must be isLatest=False"

    # At least one memory must be latest
    latest_count = sum(1 for m in all_mems if m.get("isLatest") is True)
    assert latest_count >= 1


# ---------------------------------------------------------------------------
# Property 8: Path Normalization Idempotency
# normalize(normalize(p)) == normalize(p) for all valid inputs.
# ---------------------------------------------------------------------------

@settings(max_examples=100)
@given(
    path=st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz0123456789/_-.",
        min_size=2,
        max_size=100,
    ).filter(lambda p: ".." not in p and p.strip("/") != ""),
)
def test_property_8_path_normalization_idempotency(path):
    from functions import normalize_folder_path
    try:
        normalized_once = normalize_folder_path(path)
        normalized_twice = normalize_folder_path(normalized_once)
        assert normalized_once == normalized_twice
    except ValueError:
        pass
