"""
C1.1 — Unit tests for observe(), strip_private_data(), and folder_observe().
"""

import datetime

import pytest

from agentcache.db import StateKV
from agentcache.functions import KV, folder_observe, observe, strip_private_data

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_kv(tmp_path):
    return StateKV(db_path=str(tmp_path / "test.db"))


def _now() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    )


def valid_observe_payload(**overrides):
    p = {
        "sessionId": "sess_test_001",
        "hookType": "post_tool_use",
        "timestamp": _now(),
        "data": {"tool_name": "read_file", "tool_input": {"path": "/src/app.py"}},
    }
    p.update(overrides)
    return p


def valid_folder_payload(**overrides):
    p = {
        "folderPath": "/home/user/projects/myapp",
        "agentId": "test-agent",
        "text": "Edited the authentication module",
        "timestamp": _now(),
    }
    p.update(overrides)
    return p


# ---------------------------------------------------------------------------
# observe() — valid payload
# ---------------------------------------------------------------------------


class TestObserveValid:
    def test_returns_observation_id(self, tmp_path):
        kv = make_kv(tmp_path)
        result = observe(kv, valid_observe_payload())
        assert "observationId" in result
        assert isinstance(result["observationId"], str)
        assert len(result["observationId"]) > 0

    def test_observation_id_has_obs_prefix(self, tmp_path):
        kv = make_kv(tmp_path)
        result = observe(kv, valid_observe_payload())
        assert result["observationId"].startswith("obs_")

    def test_observation_stored_in_kv(self, tmp_path):
        kv = make_kv(tmp_path)
        result = observe(kv, valid_observe_payload(sessionId="sess_store_check"))
        obs_id = result["observationId"]
        stored = kv.get(KV.observations("sess_store_check"), obs_id)
        assert stored is not None
        assert stored["id"] == obs_id


# ---------------------------------------------------------------------------
# observe() — missing required fields
# ---------------------------------------------------------------------------


class TestObserveMissingFields:
    def test_missing_session_id_raises(self, tmp_path):
        kv = make_kv(tmp_path)
        with pytest.raises(ValueError):
            observe(kv, valid_observe_payload(sessionId=""))

    def test_missing_session_id_none_raises(self, tmp_path):
        kv = make_kv(tmp_path)
        payload = valid_observe_payload()
        del payload["sessionId"]
        with pytest.raises(ValueError):
            observe(kv, payload)

    def test_missing_hook_type_raises(self, tmp_path):
        kv = make_kv(tmp_path)
        with pytest.raises(ValueError):
            observe(kv, valid_observe_payload(hookType=""))

    def test_missing_hook_type_none_raises(self, tmp_path):
        kv = make_kv(tmp_path)
        payload = valid_observe_payload()
        del payload["hookType"]
        with pytest.raises(ValueError):
            observe(kv, payload)

    def test_missing_timestamp_raises(self, tmp_path):
        kv = make_kv(tmp_path)
        with pytest.raises(ValueError):
            observe(kv, valid_observe_payload(timestamp=""))

    def test_missing_timestamp_none_raises(self, tmp_path):
        kv = make_kv(tmp_path)
        payload = valid_observe_payload()
        del payload["timestamp"]
        with pytest.raises(ValueError):
            observe(kv, payload)


# ---------------------------------------------------------------------------
# strip_private_data() — redaction
# ---------------------------------------------------------------------------


class TestStripPrivateData:
    def test_redacts_api_key_assignment(self):
        text = "api_key = sk-proj-abc123LONGKEY456789012345678901234567890"
        result = strip_private_data(text)
        assert "[REDACTED_SECRET]" in result
        # Raw key value must not appear
        assert "sk-proj-abc123" not in result

    def test_redacts_bearer_token(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyMTIzIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        result = strip_private_data(text)
        assert "[REDACTED_SECRET]" in result
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result

    def test_redacts_secret_key_pair(self):
        text = "secret=mysupersecretvalue12345678901234567890"
        result = strip_private_data(text)
        assert "[REDACTED_SECRET]" in result

    def test_non_sensitive_text_unchanged(self):
        text = "Edited authentication module in src/app.py"
        result = strip_private_data(text)
        assert result == text

    def test_redacts_private_xml_tags(self):
        text = "prefix <private>confidential info</private> suffix"
        result = strip_private_data(text)
        assert "[REDACTED]" in result
        assert "confidential info" not in result

    def test_redacts_google_api_key(self):
        # AIza prefix (Google API key pattern)
        text = "key = AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        result = strip_private_data(text)
        assert "[REDACTED_SECRET]" in result
        assert "AIzaSy" not in result


# ---------------------------------------------------------------------------
# MAX_OBS_PER_SESSION cap
# ---------------------------------------------------------------------------


class TestObserveSessionCap:
    def test_cap_raises_on_fourth_observation(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MAX_OBS_PER_SESSION", "3")
        kv = make_kv(tmp_path)
        sess = "sess_cap_test"
        for _ in range(3):
            observe(kv, valid_observe_payload(sessionId=sess))
        with pytest.raises(ValueError, match="limit"):
            observe(kv, valid_observe_payload(sessionId=sess))

    def test_cap_not_triggered_at_limit(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MAX_OBS_PER_SESSION", "3")
        kv = make_kv(tmp_path)
        sess = "sess_cap_ok"
        # Exactly 3 should succeed
        for _ in range(3):
            result = observe(kv, valid_observe_payload(sessionId=sess))
        assert "observationId" in result


# ---------------------------------------------------------------------------
# folder_observe() — valid payload / fobs_ prefix
# ---------------------------------------------------------------------------


class TestFolderObserveCore:
    def test_returns_observation_id(self, tmp_path):
        kv = make_kv(tmp_path)
        result = folder_observe(kv, valid_folder_payload())
        assert "observationId" in result

    def test_observation_id_has_fobs_prefix(self, tmp_path):
        kv = make_kv(tmp_path)
        result = folder_observe(kv, valid_folder_payload())
        assert result["observationId"].startswith("fobs_")

    def test_observation_stored_in_kv(self, tmp_path):
        kv = make_kv(tmp_path)
        result = folder_observe(kv, valid_folder_payload())
        obs_id = result["observationId"]
        # normalized: "home/user/projects/myapp"
        normalized = "home/user/projects/myapp"
        stored = kv.get(KV.folder_obs(normalized, "test-agent"), obs_id)
        assert stored is not None
        assert stored["id"] == obs_id
