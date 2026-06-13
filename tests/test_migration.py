"""Unit tests for migrate_sessions_to_folders (REQ-058–REQ-062)."""
import sys
import os
import pytest
import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from db import StateKV
from functions import migrate_sessions_to_folders, KV


def make_kv(tmp_path):
    db_path = os.path.join(str(tmp_path), 'test.db')
    return StateKV(db_path=db_path)


def seed_session(kv, session_id='sess_001', cwd='/home/user/proj', agent='kiro', obs_count=3):
    """Seed a legacy session with observations into the old schema."""
    ts = datetime.datetime.utcnow().isoformat() + 'Z'
    session = {
        'id': session_id,
        'project': cwd,
        'cwd': cwd,
        'agentId': agent,
        'startedAt': ts,
        'updatedAt': ts,
        'status': 'completed',
    }
    kv.set(KV.sessions, session_id, session)
    obs_ids = []
    for i in range(obs_count):
        obs_id = f'obs_{i}'
        obs = {
            'id': obs_id,
            'sessionId': session_id,
            'timestamp': ts,
            'type': 'file_edit',
            'title': f'Edit {i}',
            'narrative': f'Edited file {i}',
            'concepts': ['python'],
            'files': [f'src/file_{i}.py'],
            'importance': 5,
        }
        kv.set(KV.observations(session_id), obs_id, obs)
        obs_ids.append(obs_id)
    return obs_ids


class TestMigrateDryRun:
    def test_dry_run_writes_nothing(self, tmp_path):
        kv = make_kv(tmp_path)
        seed_session(kv)
        result = migrate_sessions_to_folders(kv, dry_run=True)
        assert result['migrated_sessions'] > 0
        assert result['migrated_observations'] > 0
        # No folder obs should have been written
        fp = 'home/user/proj'
        obs = kv.list(KV.folder_obs(fp, 'kiro'))
        assert len(obs) == 0

    def test_dry_run_returns_counts(self, tmp_path):
        kv = make_kv(tmp_path)
        seed_session(kv, obs_count=5)
        result = migrate_sessions_to_folders(kv, dry_run=True)
        assert result['migrated_observations'] == 5
        assert result['dry_run'] is True


class TestMigrateActual:
    def test_migrates_observations(self, tmp_path):
        kv = make_kv(tmp_path)
        seed_session(kv, obs_count=3)
        migrate_sessions_to_folders(kv, dry_run=False)
        fp = 'home/user/proj'
        obs = kv.list(KV.folder_obs(fp, 'kiro'))
        assert len(obs) == 3

    def test_skips_raw_observations(self, tmp_path):
        kv = make_kv(tmp_path)
        seed_session(kv, obs_count=2)
        # Add a raw obs
        kv.set(KV.observations('sess_001'), 'obs_0:raw', {
            'id': 'obs_0:raw', 'sessionId': 'sess_001',
            'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
        })
        result = migrate_sessions_to_folders(kv, dry_run=False)
        # Raw obs should not be counted
        assert result['migrated_observations'] == 2

    def test_nondestructive(self, tmp_path):
        kv = make_kv(tmp_path)
        seed_session(kv)
        migrate_sessions_to_folders(kv, dry_run=False)
        # Old session data still there
        session = kv.get(KV.sessions, 'sess_001')
        assert session is not None

    def test_unknown_fallback(self, tmp_path):
        kv = make_kv(tmp_path)
        # Session with no cwd or project
        ts = datetime.datetime.utcnow().isoformat() + 'Z'
        kv.set(KV.sessions, 'sess_no_path', {
            'id': 'sess_no_path', 'startedAt': ts, 'updatedAt': ts, 'status': 'completed',
        })
        kv.set(KV.observations('sess_no_path'), 'obs_x', {
            'id': 'obs_x', 'sessionId': 'sess_no_path',
            'timestamp': ts, 'type': 'other', 'title': 'x', 'narrative': 'x',
        })
        result = migrate_sessions_to_folders(kv, dry_run=False)
        # Should succeed with 'unknown' fallback
        assert result['migrated_sessions'] >= 1

    def test_returns_error_list(self, tmp_path):
        kv = make_kv(tmp_path)
        seed_session(kv)
        result = migrate_sessions_to_folders(kv, dry_run=False)
        assert 'errors' in result
        assert isinstance(result['errors'], list)
