"""Unit tests for forget() folder-based deletion (REQ-029–REQ-033)."""
import sys
import os
import pytest
import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from db import StateKV
from functions import folder_observe, forget, KV


def make_kv(tmp_path):
    db_path = os.path.join(str(tmp_path), 'test.db')
    return StateKV(db_path=db_path)


def add_obs(kv, folder='/home/user/proj', agent='kiro', n=1):
    ids = []
    for i in range(n):
        result = folder_observe(kv, {
            'folderPath': folder,
            'agentId': agent,
            'text': f'observation {i}',
            'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
        })
        ids.append(result['observationId'])
    return ids


class TestForgetFullPair:
    def test_full_deletion_clears_obs(self, tmp_path):
        kv = make_kv(tmp_path)
        add_obs(kv, n=3)
        fp = 'home/user/proj'
        result = forget(kv, {'folderPath': '/home/user/proj', 'agentId': 'kiro'})
        assert result['deleted'] >= 3
        obs = kv.list(KV.folder_obs(fp, 'kiro'))
        assert len(obs) == 0

    def test_full_deletion_removes_index_entry(self, tmp_path):
        kv = make_kv(tmp_path)
        add_obs(kv)
        fp = 'home/user/proj'
        forget(kv, {'folderPath': '/home/user/proj', 'agentId': 'kiro'})
        entry = kv.get(KV.folders, f'{fp}:kiro')
        assert entry is None

    def test_full_deletion_removes_meta(self, tmp_path):
        kv = make_kv(tmp_path)
        add_obs(kv)
        fp = 'home/user/proj'
        forget(kv, {'folderPath': '/home/user/proj', 'agentId': 'kiro'})
        meta = kv.get(KV.folder_meta(fp, 'kiro'), 'meta')
        assert meta is None

    def test_deleted_count_matches(self, tmp_path):
        kv = make_kv(tmp_path)
        add_obs(kv, n=5)
        result = forget(kv, {'folderPath': '/home/user/proj', 'agentId': 'kiro'})
        assert result['deleted'] == 5


class TestForgetPartial:
    def test_partial_deletion(self, tmp_path):
        kv = make_kv(tmp_path)
        ids = add_obs(kv, n=4)
        fp = 'home/user/proj'
        to_delete = ids[:2]
        result = forget(kv, {
            'folderPath': '/home/user/proj',
            'agentId': 'kiro',
            'observationIds': to_delete,
        })
        assert result['deleted'] == 2
        remaining = kv.list(KV.folder_obs(fp, 'kiro'))
        remaining_ids = {o['id'] for o in remaining}
        for oid in to_delete:
            assert oid not in remaining_ids

    def test_partial_decrements_obs_count(self, tmp_path):
        kv = make_kv(tmp_path)
        ids = add_obs(kv, n=4)
        fp = 'home/user/proj'
        forget(kv, {
            'folderPath': '/home/user/proj',
            'agentId': 'kiro',
            'observationIds': ids[:2],
        })
        meta = kv.get(KV.folder_meta(fp, 'kiro'), 'meta')
        assert meta['obsCount'] == 2


class TestForgetMemory:
    def test_delete_global_memory(self, tmp_path):
        kv = make_kv(tmp_path)
        from functions import remember
        result = remember(kv, {'content': 'Important insight', 'type': 'fact'})
        mem_id = result['memory']['id']
        forget_result = forget(kv, {'memoryId': mem_id})
        assert forget_result['deleted'] >= 1
        stored = kv.get(KV.memories, mem_id)
        assert stored is None
