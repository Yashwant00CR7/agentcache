"""Unit tests for folder_timeline (REQ-020, REQ-021, REQ-022, REQ-071)."""
import sys
import os
import pytest
import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from db import StateKV
from functions import folder_observe, folder_timeline


def make_kv(tmp_path):
    db_path = os.path.join(str(tmp_path), 'test.db')
    return StateKV(db_path=db_path)


def ts(offset_seconds=0):
    dt = datetime.datetime(2025, 1, 15, 10, 0, 0) + datetime.timedelta(seconds=offset_seconds)
    return dt.isoformat() + 'Z'


def add_obs(kv, folder='/home/user/proj', agent='kiro', timestamp=None, text='obs'):
    return folder_observe(kv, {
        'folderPath': folder,
        'agentId': agent,
        'text': text,
        'timestamp': timestamp or ts(),
    })


class TestTimelineOrdering:
    def test_results_sorted_desc(self, tmp_path):
        kv = make_kv(tmp_path)
        add_obs(kv, timestamp=ts(0))
        add_obs(kv, timestamp=ts(60))
        add_obs(kv, timestamp=ts(30))
        results = folder_timeline(kv)
        timestamps = [r['timestamp'] for r in results]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_empty_returns_empty(self, tmp_path):
        kv = make_kv(tmp_path)
        results = folder_timeline(kv)
        assert results == []


class TestTimelineLimit:
    def test_limit_respected(self, tmp_path):
        kv = make_kv(tmp_path)
        for i in range(10):
            add_obs(kv, timestamp=ts(i), text=f"obs {i}")
        results = folder_timeline(kv, limit=5)
        assert len(results) == 5

    def test_default_limit_100(self, tmp_path):
        kv = make_kv(tmp_path)
        for i in range(150):
            add_obs(kv, timestamp=ts(i), text=f"obs {i}")
        results = folder_timeline(kv)
        assert len(results) == 100


class TestTimelineFilters:
    def test_folder_filter(self, tmp_path):
        kv = make_kv(tmp_path)
        add_obs(kv, folder='/home/user/proj-a', timestamp=ts(0))
        add_obs(kv, folder='/home/user/proj-b', timestamp=ts(1))
        results = folder_timeline(kv, folder_path='home/user/proj-a')
        assert all(r['folderPath'] == 'home/user/proj-a' for r in results)
        assert len(results) == 1

    def test_agent_filter(self, tmp_path):
        kv = make_kv(tmp_path)
        add_obs(kv, agent='kiro', timestamp=ts(0))
        add_obs(kv, agent='claude', timestamp=ts(1))
        results = folder_timeline(kv, agent_id='kiro')
        assert all(r['agentId'] == 'kiro' for r in results)

    def test_before_filter(self, tmp_path):
        kv = make_kv(tmp_path)
        add_obs(kv, timestamp=ts(0))   # 10:00:00
        add_obs(kv, timestamp=ts(60))  # 10:01:00
        add_obs(kv, timestamp=ts(120)) # 10:02:00
        results = folder_timeline(kv, before=ts(90))
        # Should only include obs before 10:01:30
        for r in results:
            assert r['timestamp'] < ts(90)

    def test_after_filter(self, tmp_path):
        kv = make_kv(tmp_path)
        add_obs(kv, timestamp=ts(0))
        add_obs(kv, timestamp=ts(60))
        add_obs(kv, timestamp=ts(120))
        results = folder_timeline(kv, after=ts(30))
        for r in results:
            assert r['timestamp'] > ts(30)
