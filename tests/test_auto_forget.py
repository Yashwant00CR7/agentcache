"""Unit tests for auto_forget() folder-based and memory-based eviction."""

import datetime
import os

from agentcache.db import StateKV
from agentcache.functions import KV, auto_forget, folder_observe, remember


def make_kv(tmp_path):
    db_path = os.path.join(str(tmp_path), "test.db")
    return StateKV(db_path=db_path)


def test_auto_forget_memories(tmp_path):
    kv = make_kv(tmp_path)

    # 1. Create a memory that expires in the past
    past_time = (
        (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1))
        .isoformat()
        .replace("+00:00", "Z")
    )
    res1 = remember(kv, {"content": "Stale memory", "forgetAfter": past_time})
    mem1_id = res1["memory"]["id"]

    # 2. Create a memory that expires in the future
    future_time = (
        (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=5))
        .isoformat()
        .replace("+00:00", "Z")
    )
    res2 = remember(kv, {"content": "Fresh memory", "forgetAfter": future_time})
    mem2_id = res2["memory"]["id"]

    # Run auto_forget
    results = auto_forget(kv, dry_run=False)
    assert len(results["evictedMemories"]) == 1

    # Verify mem1 is deleted, mem2 exists
    assert kv.get(KV.memories, mem1_id) is None
    assert kv.get(KV.memories, mem2_id) is not None


def test_auto_forget_expired_folder_observations(tmp_path):
    kv = make_kv(tmp_path)
    folder = "/home/user/myproject"
    agent = "kiro"

    # 1. Create expired folder observation
    past_time = (
        (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1))
        .isoformat()
        .replace("+00:00", "Z")
    )
    res1 = folder_observe(
        kv,
        {
            "folderPath": folder,
            "agentId": agent,
            "text": "Stale observation",
            "timestamp": past_time,
            "forgetAfter": past_time,
        },
    )
    obs1_id = res1["observationId"]

    # 2. Create fresh folder observation
    future_time = (
        (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1))
        .isoformat()
        .replace("+00:00", "Z")
    )
    res2 = folder_observe(
        kv,
        {
            "folderPath": folder,
            "agentId": agent,
            "text": "Fresh observation",
            "timestamp": past_time,
            "forgetAfter": future_time,
        },
    )
    obs2_id = res2["observationId"]

    # Run auto_forget
    results = auto_forget(kv, dry_run=False)
    assert len(results["evictedObservations"]) == 1

    # Verify eviction
    fp = "home/user/myproject"
    assert kv.get(KV.folder_obs(fp, agent), obs1_id) is None
    assert kv.get(KV.folder_obs(fp, agent), obs2_id) is not None


def test_auto_forget_low_importance_stale_observations(tmp_path):
    kv = make_kv(tmp_path)
    folder = "/home/user/myproject"
    agent = "kiro"

    # 1. Create old low-importance folder observation (importance = 1, 200 days old)
    old_time = (
        (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=200))
        .isoformat()
        .replace("+00:00", "Z")
    )
    res1 = folder_observe(
        kv,
        {
            "folderPath": folder,
            "agentId": agent,
            "text": "Stale low value observation",
            "timestamp": old_time,
            "importance": 1,
        },
    )
    obs1_id = res1["observationId"]

    # 2. Create old high-importance folder observation (importance = 8, 200 days old)
    res2 = folder_observe(
        kv,
        {
            "folderPath": folder,
            "agentId": agent,
            "text": "Stale high value observation",
            "timestamp": old_time,
            "importance": 8,
        },
    )
    obs2_id = res2["observationId"]

    # Run auto_forget
    results = auto_forget(kv, dry_run=False)
    assert len(results["evictedObservations"]) == 1

    # Verify eviction
    fp = "home/user/myproject"
    assert kv.get(KV.folder_obs(fp, agent), obs1_id) is None
    assert kv.get(KV.folder_obs(fp, agent), obs2_id) is not None
