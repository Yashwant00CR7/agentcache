"""
Tests for the auto-repair behavior of `agentcache connect <target>`.

Covers issue #37: adapters should detect stale MCP entries and either repair
them or clearly report they need `--force`, instead of silently reporting
"already wired" on broken configs.
"""

import json
import os
import sys

import pytest

from agentcache import connect


class Args:
    """Minimal stand-in for the argparse.Namespace passed to adapters."""

    def __init__(self, force=False, dry_run=False, with_hooks=False):
        self.force = force
        self.dry_run = dry_run
        self.with_hooks = with_hooks


@pytest.fixture
def fake_mcp_stdio(tmp_path, monkeypatch):
    stdio = tmp_path / "mcp_stdio.py"
    stdio.write_text("# fake mcp entry\n", encoding="utf-8")
    monkeypatch.setattr(connect, "get_mcp_stdio_path", lambda: str(stdio))
    return str(stdio)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr(connect, "get_home_dir", lambda: str(tmp_path))
    monkeypatch.setattr(connect, "get_appdata_dir", lambda: str(tmp_path / "AppData"))
    return str(tmp_path)


# ------------------------------------------------------------------------------
# Helper unit tests
# ------------------------------------------------------------------------------


def test_json_entry_matches_true_for_identical_entry():
    desired = {
        "command": sys.executable,
        "args": ["/tmp/mcp_stdio.py"],
        "env": {"AGENTCACHE_URL": "http://localhost:3111"},
    }
    existing = json.loads(json.dumps(desired))
    assert connect.json_entry_matches(existing, desired) is True


def test_json_entry_matches_false_when_command_differs():
    desired = {
        "command": sys.executable,
        "args": ["/tmp/mcp_stdio.py"],
        "env": {"AGENTCACHE_URL": "http://localhost:3111"},
    }
    existing = {
        "command": "/some/broken/python",
        "args": ["/tmp/mcp_stdio.py"],
        "env": {"AGENTCACHE_URL": "http://localhost:3111"},
    }
    assert connect.json_entry_matches(existing, desired) is False


def test_json_entry_matches_false_when_args_differ():
    desired = {
        "command": sys.executable,
        "args": ["/new/mcp_stdio.py"],
        "env": {"AGENTCACHE_URL": "http://localhost:3111"},
    }
    existing = {
        "command": sys.executable,
        "args": ["/old/mcp_stdio.py"],
        "env": {"AGENTCACHE_URL": "http://localhost:3111"},
    }
    assert connect.json_entry_matches(existing, desired) is False


def test_json_entry_matches_false_when_required_env_key_missing():
    desired = {
        "command": sys.executable,
        "args": ["/tmp/mcp_stdio.py"],
        "env": {"AGENTCACHE_URL": "http://localhost:3111"},
    }
    existing = {
        "command": sys.executable,
        "args": ["/tmp/mcp_stdio.py"],
        "env": {},
    }
    assert connect.json_entry_matches(existing, desired) is False


def test_json_entry_matches_ignores_extra_env_keys():
    """Users may add extra env vars; that shouldn't count as stale."""
    desired = {
        "command": sys.executable,
        "args": ["/tmp/mcp_stdio.py"],
        "env": {"AGENTCACHE_URL": "http://localhost:3111"},
    }
    existing = {
        "command": sys.executable,
        "args": ["/tmp/mcp_stdio.py"],
        "env": {
            "AGENTCACHE_URL": "http://localhost:3111",
            "USER_CUSTOM": "1",
        },
    }
    assert connect.json_entry_matches(existing, desired) is True


def test_parse_codex_block_extracts_command_args_env():
    text = """
[mcp_servers.agentcache]
command = "/usr/bin/python"
args = ["/tmp/mcp_stdio.py"]
[mcp_servers.agentcache.env]
AGENTCACHE_URL = "http://localhost:3111"

[some_other_section]
foo = "bar"
"""
    parsed = connect.parse_codex_agentcache_block(text)
    assert parsed is not None
    assert parsed["command"] == "/usr/bin/python"
    assert parsed["args"] == ["/tmp/mcp_stdio.py"]
    assert parsed["env"].get("AGENTCACHE_URL") == "http://localhost:3111"


def test_parse_codex_block_returns_none_when_absent():
    assert connect.parse_codex_agentcache_block("[other]\nfoo = 1\n") is None


def test_split_toml_list_handles_multi_element_arrays():
    assert connect._split_toml_list('["a", "b"]') == ["a", "b"]
    assert connect._split_toml_list('["only"]') == ["only"]
    assert connect._split_toml_list("[]") == []
    assert connect._split_toml_list('"bare"') == ["bare"]
    assert connect._split_toml_list(None) is None


def test_codex_entry_matches_rejects_extra_args():
    """The old substring-in-raw check would accept multi-arg lists containing
    the desired path. Real list-based comparison rejects them."""
    text = """[mcp_servers.agentcache]
command = "/usr/bin/python"
args = ["/tmp/mcp_stdio.py", "/tmp/extra.py"]
[mcp_servers.agentcache.env]
AGENTCACHE_URL = "http://localhost:3111"
"""
    assert (
        connect.codex_entry_matches(text, "/usr/bin/python", "/tmp/mcp_stdio.py")
        is False
    )


def test_required_env_keys_constant_is_a_frozenset():
    assert "AGENTCACHE_URL" in connect.REQUIRED_MCP_ENV_KEYS
    assert isinstance(connect.REQUIRED_MCP_ENV_KEYS, frozenset)


def test_json_entry_matches_flags_missing_required_url_even_when_shell_env_lacks_it(
    monkeypatch,
):
    """The env-key check must NOT depend on the current shell — an entry
    missing AGENTCACHE_URL is stale regardless of what the caller exports."""
    monkeypatch.delenv("AGENTCACHE_URL", raising=False)
    monkeypatch.delenv("AGENTMEMORY_URL", raising=False)
    monkeypatch.delenv("AGENTCACHE_SECRET", raising=False)
    monkeypatch.delenv("AGENTMEMORY_SECRET", raising=False)

    desired = connect.build_desired_json_entry("/tmp/mcp_stdio.py")
    existing_missing_url = {
        "command": sys.executable,
        "args": ["/tmp/mcp_stdio.py"],
        "env": {},
    }
    assert connect.json_entry_matches(existing_missing_url, desired) is False


def test_json_entry_matches_flags_missing_secret_when_shell_has_one(monkeypatch):
    """If the user sets AGENTCACHE_SECRET in the shell now, an existing entry
    without that key should be treated as stale so the re-run refreshes it."""
    monkeypatch.setenv("AGENTCACHE_SECRET", "s3cret")
    desired = connect.build_desired_json_entry("/tmp/mcp_stdio.py")
    existing_no_secret = {
        "command": sys.executable,
        "args": ["/tmp/mcp_stdio.py"],
        "env": {"AGENTCACHE_URL": "http://localhost:3111"},
    }
    assert connect.json_entry_matches(existing_no_secret, desired) is False


# ------------------------------------------------------------------------------
# ClaudeCodeAdapter behavior
# ------------------------------------------------------------------------------


def test_claude_code_repairs_stale_entry(fake_home, fake_mcp_stdio, capsys):
    claude_json = os.path.join(fake_home, ".claude.json")
    stale = {
        "mcpServers": {
            "agentcache": {
                "command": "/old/broken/python",
                "args": ["/old/mcp_stdio.py"],
                "env": {},
            }
        }
    }
    with open(claude_json, "w", encoding="utf-8") as f:
        json.dump(stale, f)

    connect.ClaudeCodeAdapter().install(Args())

    with open(claude_json, "r", encoding="utf-8") as f:
        result = json.load(f)
    entry = result["mcpServers"]["agentcache"]
    assert entry["command"] == sys.executable
    assert entry["args"] == [fake_mcp_stdio]
    assert "AGENTCACHE_URL" in entry["env"]
    out = capsys.readouterr().out
    assert "Updated existing agentcache MCP entry" in out


def test_claude_code_reports_up_to_date_with_force_hint(
    fake_home, fake_mcp_stdio, capsys
):
    claude_json = os.path.join(fake_home, ".claude.json")
    good = {
        "mcpServers": {
            "agentcache": {
                "command": sys.executable,
                "args": [fake_mcp_stdio],
                "env": {"AGENTCACHE_URL": "http://localhost:3111"},
            }
        }
    }
    with open(claude_json, "w", encoding="utf-8") as f:
        json.dump(good, f)

    connect.ClaudeCodeAdapter().install(Args())

    out = capsys.readouterr().out
    assert "already wired" in out
    assert "--force" in out


def test_claude_code_writes_fresh_when_no_entry(fake_home, fake_mcp_stdio, capsys):
    claude_json = os.path.join(fake_home, ".claude.json")
    assert not os.path.exists(claude_json)

    connect.ClaudeCodeAdapter().install(Args())

    with open(claude_json, "r", encoding="utf-8") as f:
        result = json.load(f)
    assert result["mcpServers"]["agentcache"]["command"] == sys.executable
    out = capsys.readouterr().out
    assert "Wired Claude Code MCP" in out


def test_claude_code_dry_run_does_not_write(fake_home, fake_mcp_stdio, capsys):
    claude_json = os.path.join(fake_home, ".claude.json")
    stale = {
        "mcpServers": {"agentcache": {"command": "/old/python", "args": [], "env": {}}}
    }
    with open(claude_json, "w", encoding="utf-8") as f:
        json.dump(stale, f)

    connect.ClaudeCodeAdapter().install(Args(dry_run=True))

    with open(claude_json, "r", encoding="utf-8") as f:
        after = json.load(f)
    # File must be unchanged
    assert after == stale
    out = capsys.readouterr().out
    assert "[dry-run]" in out
    assert "update" in out


# ------------------------------------------------------------------------------
# KiroAdapter behavior
# ------------------------------------------------------------------------------


def test_kiro_repairs_stale_entry(fake_home, fake_mcp_stdio, capsys):
    kiro_json = os.path.join(fake_home, ".kiro", "settings", "mcp.json")
    os.makedirs(os.path.dirname(kiro_json), exist_ok=True)
    with open(kiro_json, "w", encoding="utf-8") as f:
        json.dump(
            {
                "mcpServers": {
                    "agentcache": {
                        "command": "/old/python",
                        "args": ["/old/mcp.py"],
                        "env": {},
                    }
                }
            },
            f,
        )

    connect.KiroAdapter().install(Args())

    with open(kiro_json, "r", encoding="utf-8") as f:
        result = json.load(f)
    entry = result["mcpServers"]["agentcache"]
    assert entry["command"] == sys.executable
    assert entry["args"] == [fake_mcp_stdio]
    out = capsys.readouterr().out
    assert "Updated existing agentcache MCP entry" in out


# ------------------------------------------------------------------------------
# VSCodeAdapter behavior
# ------------------------------------------------------------------------------


def test_vscode_prefers_workspace_config(
    fake_home, fake_mcp_stdio, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    workspace_config_dir = tmp_path / ".vscode"
    workspace_config_dir.mkdir()
    user_config_path = connect.VSCodeAdapter().get_user_config_path()
    os.makedirs(os.path.dirname(user_config_path), exist_ok=True)

    adapter = connect.VSCodeAdapter()

    assert adapter.get_config_path() == str(workspace_config_dir / "mcp.json")

    adapter.install(Args())

    with open(workspace_config_dir / "mcp.json", "r", encoding="utf-8") as f:
        result = json.load(f)
    assert "agentcache" in result["servers"]
    assert not os.path.exists(user_config_path)


def test_vscode_workspace_config_merges_existing_servers(
    fake_home, fake_mcp_stdio, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    workspace_config = tmp_path / ".vscode" / "mcp.json"
    workspace_config.parent.mkdir()
    with open(workspace_config, "w", encoding="utf-8") as f:
        json.dump(
            {
                "servers": {
                    "other": {
                        "type": "stdio",
                        "command": "other",
                        "args": ["server.py"],
                    }
                }
            },
            f,
        )

    connect.VSCodeAdapter().install(Args())

    with open(workspace_config, "r", encoding="utf-8") as f:
        result = json.load(f)
    assert result["servers"]["other"]["command"] == "other"
    assert result["servers"]["agentcache"]["command"] == sys.executable
    assert result["servers"]["agentcache"]["args"] == [fake_mcp_stdio]


def test_vscode_with_hooks_is_a_noop_note(
    fake_home, fake_mcp_stdio, tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".vscode").mkdir()

    connect.VSCodeAdapter().install(Args(with_hooks=True))

    captured = capsys.readouterr()
    assert "VS Code has no native hook installer" in captured.err
    assert (tmp_path / ".vscode" / "mcp.json").exists()


def test_vscode_repairs_stale_entry(
    fake_home, fake_mcp_stdio, tmp_path, monkeypatch, capsys
):
    workspace = tmp_path / "outside-workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    adapter = connect.VSCodeAdapter()
    mcp_config_path = adapter.get_user_config_path()
    os.makedirs(os.path.dirname(mcp_config_path), exist_ok=True)
    with open(mcp_config_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "servers": {
                    "agentcache": {
                        "command": "/old/python",
                        "args": ["/old/mcp.py"],
                        "env": {},
                    }
                }
            },
            f,
        )

    adapter.install(Args())

    with open(mcp_config_path, "r", encoding="utf-8") as f:
        result = json.load(f)
    entry = result["servers"]["agentcache"]
    assert entry["command"] == sys.executable
    assert entry["args"] == [fake_mcp_stdio]
    out = capsys.readouterr().out
    assert "Updated existing agentcache MCP entry" in out


# ------------------------------------------------------------------------------
# CodexAdapter behavior
# ------------------------------------------------------------------------------


def test_codex_repairs_stale_toml_entry(fake_home, fake_mcp_stdio, capsys):
    codex_toml = os.path.join(fake_home, ".codex", "config.toml")
    os.makedirs(os.path.dirname(codex_toml), exist_ok=True)
    stale_toml = """
[mcp_servers.agentcache]
command = "/old/broken/python"
args = ["/old/mcp_stdio.py"]
[mcp_servers.agentcache.env]
AGENTCACHE_URL = "http://localhost:3111"
"""
    with open(codex_toml, "w", encoding="utf-8") as f:
        f.write(stale_toml.lstrip())

    connect.CodexAdapter().install(Args())

    with open(codex_toml, "r", encoding="utf-8") as f:
        after = f.read()

    expected_command = sys.executable.replace("\\", "/")
    expected_args = fake_mcp_stdio.replace("\\", "/")
    assert f'command = "{expected_command}"' in after
    assert expected_args in after
    # old command should be gone (block was stripped and rewritten)
    assert "/old/broken/python" not in after
    out = capsys.readouterr().out
    assert "Updated existing agentcache MCP entry" in out


# ------------------------------------------------------------------------------
# HermesAdapter behavior
# ------------------------------------------------------------------------------


def test_hermes_plugin_source_ships_inside_package():
    """The plugin must be discoverable from the installed package, not the repo tree."""
    adapter = connect.HermesAdapter()
    src = adapter.get_plugin_source_dir()
    assert os.path.isdir(src), f"packaged hermes plugin missing at {src}"
    assert os.path.isfile(os.path.join(src, "__init__.py"))
    assert os.path.isfile(os.path.join(src, "plugin.yaml"))
    assert os.path.isfile(os.path.join(src, "README.md"))


def test_hermes_install_copies_packaged_plugin(fake_home, fake_mcp_stdio, capsys):
    connect.HermesAdapter().install(Args())
    dest = os.path.join(fake_home, ".hermes", "plugins", "agentcache")
    assert os.path.isfile(os.path.join(dest, "__init__.py"))
    assert os.path.isfile(os.path.join(dest, "plugin.yaml"))
    out = capsys.readouterr().out
    assert "Copied Hermes cache provider plugin" in out


def test_hermes_install_second_run_hints_force(fake_home, fake_mcp_stdio, capsys):
    connect.HermesAdapter().install(Args())
    capsys.readouterr()  # discard first output
    connect.HermesAdapter().install(Args())
    out = capsys.readouterr().out
    assert "already installed" in out
    assert "--force" in out


# ------------------------------------------------------------------------------
# --verify mode
# ------------------------------------------------------------------------------


class VerifyArgs:
    def __init__(self):
        self.force = False
        self.dry_run = False
        self.with_hooks = False
        self.verify = True


def test_verify_reports_up_to_date_without_writing(fake_home, fake_mcp_stdio, capsys):
    claude_json = os.path.join(fake_home, ".claude.json")
    good = {
        "mcpServers": {
            "agentcache": {
                "command": sys.executable,
                "args": [fake_mcp_stdio],
                "env": {"AGENTCACHE_URL": "http://localhost:3111"},
            }
        }
    }
    with open(claude_json, "w", encoding="utf-8") as f:
        json.dump(good, f)
    mtime_before = os.path.getmtime(claude_json)

    connect.ClaudeCodeAdapter().verify(VerifyArgs())

    assert os.path.getmtime(claude_json) == mtime_before, "verify must not touch disk"
    out = capsys.readouterr().out
    assert "up to date" in out


def test_verify_reports_stale_with_reasons(fake_home, fake_mcp_stdio, capsys):
    claude_json = os.path.join(fake_home, ".claude.json")
    with open(claude_json, "w", encoding="utf-8") as f:
        json.dump(
            {
                "mcpServers": {
                    "agentcache": {
                        "command": "/wrong/python",
                        "args": ["/wrong/mcp.py"],
                        "env": {},
                    }
                }
            },
            f,
        )

    connect.ClaudeCodeAdapter().verify(VerifyArgs())
    out = capsys.readouterr().out
    assert "STALE" in out
    assert "/wrong/python" in out
    assert "missing env keys" in out


def test_verify_reports_missing_when_no_entry(fake_home, fake_mcp_stdio, capsys):
    connect.ClaudeCodeAdapter().verify(VerifyArgs())
    out = capsys.readouterr().out
    assert "no agentcache entry" in out


def test_verify_hermes_absent_and_present(fake_home, fake_mcp_stdio, capsys):
    adapter = connect.HermesAdapter()
    adapter.verify(VerifyArgs())
    out = capsys.readouterr().out
    assert "not installed" in out

    adapter.install(Args())  # populate
    capsys.readouterr()
    adapter.verify(VerifyArgs())
    out = capsys.readouterr().out
    assert "plugin present" in out


def test_verify_codex_stale_toml(fake_home, fake_mcp_stdio, capsys):
    codex_toml = os.path.join(fake_home, ".codex", "config.toml")
    os.makedirs(os.path.dirname(codex_toml), exist_ok=True)
    with open(codex_toml, "w", encoding="utf-8") as f:
        f.write(
            "[mcp_servers.agentcache]\n"
            'command = "/old/python"\n'
            'args = ["/old/mcp.py"]\n'
            "[mcp_servers.agentcache.env]\n"
            'AGENTCACHE_URL = "http://localhost:3111"\n'
        )
    mtime_before = os.path.getmtime(codex_toml)

    connect.CodexAdapter().verify(VerifyArgs())

    assert os.path.getmtime(codex_toml) == mtime_before
    out = capsys.readouterr().out
    assert "STALE" in out


def test_codex_reports_up_to_date_when_matching(fake_home, fake_mcp_stdio, capsys):
    codex_toml = os.path.join(fake_home, ".codex", "config.toml")
    os.makedirs(os.path.dirname(codex_toml), exist_ok=True)
    python_exe_posix = sys.executable.replace("\\", "/")
    mcp_stdio_posix = fake_mcp_stdio.replace("\\", "/")
    current = f"""[mcp_servers.agentcache]
command = "{python_exe_posix}"
args = ["{mcp_stdio_posix}"]
[mcp_servers.agentcache.env]
AGENTCACHE_URL = "http://localhost:3111"
"""
    with open(codex_toml, "w", encoding="utf-8") as f:
        f.write(current)

    connect.CodexAdapter().install(Args())

    out = capsys.readouterr().out
    assert "already wired" in out
    assert "--force" in out
