#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import sys
from importlib import resources

# Helper functions for connect module


def get_home_dir():
    return os.path.expanduser("~")


def get_appdata_dir():
    return os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA") or get_home_dir()


def read_json_safe(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write_json_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    # Atomic rename
    shutil.move(temp_path, path)


def backup_file(path, prefix, ext="json"):
    if not os.path.exists(path):
        return None
    backup_path = f"{path}.{prefix}.backup-{int(os.path.getmtime(path))}.{ext}"
    shutil.copy2(path, backup_path)
    return backup_path


def get_plugin_root():
    # connect.py resides in src/agentcache/, plugin/ is in the parent of src/
    src_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(src_dir))
    plugin_path = os.path.join(project_root, "plugin")
    if os.path.exists(os.path.join(plugin_path, "scripts")):
        return plugin_path
    raise RuntimeError("Could not find plugin root directory.")


def get_mcp_stdio_path():
    src_dir = os.path.dirname(os.path.abspath(__file__))
    mcp_path = os.path.join(src_dir, "mcp_stdio.py")
    if os.path.exists(mcp_path):
        return mcp_path
    raise RuntimeError("Could not find mcp_stdio.py.")


# Env keys we consider required to exist on every agentcache MCP entry,
# independent of what the ambient shell currently exports. AGENTCACHE_SECRET
# is optional at the server level, so we treat it as required-if-user-has-it
# via `desired_env` rather than adding it here.
REQUIRED_MCP_ENV_KEYS = frozenset({"AGENTCACHE_URL"})


def _norm_path_for_compare(p):
    """Normalize a path for cross-platform equality checks (case + slash)."""
    return os.path.normcase(os.path.normpath(str(p))) if p else ""


def build_desired_mcp_env():
    """Build the env dict we would write into an MCP server entry."""
    env = {
        "AGENTCACHE_URL": os.environ.get("AGENTCACHE_URL")
        or os.environ.get("AGENTMEMORY_URL")
        or "http://localhost:3111"
    }
    secret = os.environ.get("AGENTCACHE_SECRET") or os.environ.get("AGENTMEMORY_SECRET")
    if secret:
        env["AGENTCACHE_SECRET"] = secret
    return env


def build_desired_json_entry(mcp_stdio_path):
    """Build the full JSON MCP server entry we would write for `agentcache`."""
    return {
        "command": sys.executable,
        "args": [mcp_stdio_path],
        "env": build_desired_mcp_env(),
    }


def json_entry_matches(existing, desired):
    """Return True if `existing` MCP entry is functionally equivalent to `desired`.

    Command and args are compared with OS path normalization. Env keys are
    validated in two passes: (1) every key in REQUIRED_MCP_ENV_KEYS must be
    present on `existing`, regardless of what the current shell exports; and
    (2) every key in `desired["env"]` (which reflects what would be written
    now, e.g. an AGENTCACHE_SECRET picked up from the current shell) must
    also exist on `existing`. Env *values* are not compared, so a user who
    customised AGENTCACHE_URL after install isn't considered stale on that
    basis alone.
    """
    if not isinstance(existing, dict):
        return False
    if _norm_path_for_compare(existing.get("command")) != _norm_path_for_compare(
        desired.get("command")
    ):
        return False
    existing_args = existing.get("args") or []
    desired_args = desired.get("args") or []
    if not isinstance(existing_args, list) or len(existing_args) != len(desired_args):
        return False
    for a, b in zip(existing_args, desired_args):
        if _norm_path_for_compare(a) != _norm_path_for_compare(b):
            return False
    existing_env = existing.get("env") or {}
    if not isinstance(existing_env, dict):
        return False
    for key in REQUIRED_MCP_ENV_KEYS:
        if key not in existing_env:
            return False
    for key in desired.get("env") or {}:
        if key not in existing_env:
            return False
    return True


def _split_toml_list(raw):
    """Parse a TOML string-array literal like `["a", "b"]` into a Python list.

    Falls back to a single-element list if the value isn't array-shaped, so
    callers can still reason about it. Not a full TOML parser — just enough
    for the shapes agentcache writes.
    """
    if raw is None:
        return None
    s = raw.strip()
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        parts = [p.strip() for p in inner.split(",")]
        return [p.strip().strip('"').strip("'") for p in parts if p]
    return [s.strip().strip('"').strip("'")]


def parse_codex_agentcache_block(text):
    """Extract the `[mcp_servers.agentcache]` and `.env` sub-block from a TOML string.

    Returns a dict with keys `command` (str|None), `args` (list[str]|None),
    and `env` (dict[str, str]), or None if the block is not present.
    """
    if "[mcp_servers.agentcache]" not in text:
        return None
    result = {"command": None, "args": None, "env": {}}
    in_block = False
    in_env = False
    for line in text.splitlines():
        trimmed = line.strip()
        if trimmed == "[mcp_servers.agentcache]":
            in_block, in_env = True, False
            continue
        if trimmed == "[mcp_servers.agentcache.env]":
            in_block, in_env = False, True
            continue
        if trimmed.startswith("["):
            in_block, in_env = False, False
            continue
        if not trimmed or trimmed.startswith("#"):
            continue
        if in_block and "=" in trimmed:
            key, _, val = trimmed.partition("=")
            key, val = key.strip(), val.strip()
            if key == "command":
                result["command"] = val.strip().strip('"')
            elif key == "args":
                result["args"] = _split_toml_list(val)
        elif in_env and "=" in trimmed:
            key, _, val = trimmed.partition("=")
            result["env"][key.strip()] = val.strip().strip('"')
    return result


def codex_entry_matches(text, python_exe_posix, mcp_stdio_posix):
    """Check whether the Codex TOML already contains an equivalent agentcache block."""
    parsed = parse_codex_agentcache_block(text)
    if not parsed:
        return False
    if _norm_path_for_compare(parsed.get("command")) != _norm_path_for_compare(
        python_exe_posix
    ):
        return False
    args = parsed.get("args") or []
    if len(args) != 1 or _norm_path_for_compare(args[0]) != _norm_path_for_compare(
        mcp_stdio_posix
    ):
        return False
    existing_env = parsed.get("env") or {}
    for key in REQUIRED_MCP_ENV_KEYS:
        if key not in existing_env:
            return False
    for key in build_desired_mcp_env():
        if key not in existing_env:
            return False
    return True


def install_json_mcp_entry(config_path, servers_key, display_name, args, backup_prefix):
    """Shared install path for JSON-based MCP clients.

    Reads `config_path` as JSON, computes the desired agentcache entry, and
    takes one of three actions based on the diff:
      - matches -> print already-wired message with --force hint
      - present but stale -> back up and rewrite in place
      - absent -> write fresh entry
    Honors `args.dry_run` and `args.force`.
    """
    mcp_stdio_path = get_mcp_stdio_path()
    existing_cfg = read_json_safe(config_path)
    next_cfg = existing_cfg.copy()
    servers = next_cfg.get(servers_key, {})

    desired_entry = build_desired_json_entry(mcp_stdio_path)
    existing_entry = servers.get("agentcache")
    up_to_date = existing_entry is not None and json_entry_matches(
        existing_entry, desired_entry
    )

    if up_to_date and not args.force:
        print(
            f"[OK] {display_name} already wired in {config_path} "
            "(re-run with --force to overwrite)"
        )
        return

    action = "update" if existing_entry else "write"
    if args.dry_run:
        print(f"[dry-run] Would {action} {servers_key}.agentcache in {config_path}")
        return

    backup = backup_file(config_path, backup_prefix)
    if backup:
        print(f"Backed up configuration to {backup}")

    servers["agentcache"] = desired_entry
    next_cfg[servers_key] = servers
    write_json_atomic(config_path, next_cfg)
    if existing_entry:
        print(f"[OK] Updated existing agentcache MCP entry in {config_path}")
    else:
        print(f"[OK] Wired {display_name} MCP config in {config_path}")


def verify_json_mcp_entry(config_path, servers_key, display_name):
    """Diff on-disk agentcache entry vs desired, without touching disk.

    Returns True when the entry is up to date. Prints one line per client.
    """
    existing_cfg = read_json_safe(config_path)
    servers = existing_cfg.get(servers_key, {})
    existing_entry = servers.get("agentcache")
    if existing_entry is None:
        print(f"[--] {display_name}: no agentcache entry in {config_path}")
        return False
    desired_entry = build_desired_json_entry(get_mcp_stdio_path())
    if json_entry_matches(existing_entry, desired_entry):
        print(f"[OK] {display_name}: up to date ({config_path})")
        return True
    reasons = []
    if _norm_path_for_compare(existing_entry.get("command")) != _norm_path_for_compare(
        desired_entry["command"]
    ):
        reasons.append(
            f"command={existing_entry.get('command')!r} "
            f"(expected {desired_entry['command']!r})"
        )
    existing_args = existing_entry.get("args") or []
    if [_norm_path_for_compare(a) for a in existing_args] != [
        _norm_path_for_compare(a) for a in desired_entry["args"]
    ]:
        reasons.append(f"args={existing_args!r} (expected {desired_entry['args']!r})")
    existing_env = existing_entry.get("env") or {}
    missing_keys = sorted(set(REQUIRED_MCP_ENV_KEYS) - set(existing_env)) + sorted(
        set(desired_entry["env"]) - set(existing_env)
    )
    if missing_keys:
        reasons.append(f"missing env keys={missing_keys}")
    print(
        f"[!!] {display_name}: STALE ({config_path}) — "
        + "; ".join(reasons or ["differs from desired entry"])
    )
    return False


def build_merged_hooks(existing_hooks, plugin_root, manifest_filename="hooks.json"):
    manifest_path = os.path.join(plugin_root, "hooks", manifest_filename)
    with open(manifest_path, "r", encoding="utf-8") as f:
        ours = json.load(f)

    # Normalize paths for comparison
    normalized_scripts_dir = os.path.join(plugin_root, "scripts").replace("\\", "/")

    # Clean existing agentmemory hooks
    cleaned_hooks = {}
    if existing_hooks and "hooks" in existing_hooks:
        for event, entries in existing_hooks["hooks"].items():
            kept = []
            for entry in entries:
                is_ours = False
                for handler in entry.get("hooks", []):
                    cmd = handler.get("command", "").replace("\\", "/")
                    if normalized_scripts_dir in cmd:
                        is_ours = True
                        break
                if not is_ours:
                    kept.append(entry)
            if kept:
                cleaned_hooks[event] = kept

    # Add ours
    for event, entries in ours.get("hooks", {}).items():
        resolved_entries = []
        for entry in entries:
            next_entry = {}
            if "matcher" in entry:
                next_entry["matcher"] = entry["matcher"]

            resolved_handlers = []
            for handler in entry.get("hooks", []):
                cmd = handler.get("command", "")
                resolved_cmd = cmd.replace(
                    "${CLAUDE_PLUGIN_ROOT}", plugin_root.replace("\\", "/")
                )
                # Also replace python with sys.executable to use the correct Python instance
                if resolved_cmd.startswith("python "):
                    python_exe_posix = sys.executable.replace("\\", "/")
                    resolved_cmd = f'"{python_exe_posix}" ' + resolved_cmd[7:]
                resolved_handlers.append(
                    {"type": handler.get("type"), "command": resolved_cmd}
                )
            next_entry["hooks"] = resolved_handlers
            resolved_entries.append(next_entry)

        cleaned_hooks[event] = cleaned_hooks.get(event, []) + resolved_entries

    return {"hooks": cleaned_hooks}


# ----------------- Adapters -----------------


class ClaudeCodeAdapter:
    name = "claude-code"
    display_name = "Claude Code"

    def detect(self):
        claude_dir = os.path.join(get_home_dir(), ".claude")
        return os.path.exists(claude_dir)

    def get_config_path(self):
        return os.path.join(get_home_dir(), ".claude.json")

    def verify(self, args):
        return verify_json_mcp_entry(
            self.get_config_path(), "mcpServers", self.display_name
        )

    def install(self, args):
        install_json_mcp_entry(
            self.get_config_path(),
            servers_key="mcpServers",
            display_name=self.display_name,
            args=args,
            backup_prefix="claude-code",
        )

        if args.with_hooks:
            claude_settings = os.path.join(get_home_dir(), ".claude", "settings.json")
            try:
                plugin_root = get_plugin_root()
                existing_settings = read_json_safe(claude_settings)
                merged = build_merged_hooks(
                    existing_settings, plugin_root, "hooks.json"
                )

                if args.dry_run:
                    print(f"[dry-run] Would merge hooks into {claude_settings}")
                else:
                    backup = backup_file(claude_settings, "claude-settings")
                    if backup:
                        print(f"Backed up settings to {backup}")
                    existing_settings["hooks"] = merged["hooks"]
                    write_json_atomic(claude_settings, existing_settings)
                    print(f"[OK] Wired Claude Code hooks to {claude_settings}")
            except Exception as e:
                print(f"[FAIL] Failed to configure Claude Code hooks: {e}")


class CodexAdapter:
    name = "codex"
    display_name = "Codex CLI"

    def get_config_path(self):
        return os.path.join(get_home_dir(), ".codex", "config.toml")

    def detect(self):
        codex_dir = os.path.join(get_home_dir(), ".codex")
        return os.path.exists(codex_dir)

    def verify(self, args):
        codex_toml = self.get_config_path()
        if not os.path.exists(codex_toml):
            print(f"[--] {self.display_name}: no config file at {codex_toml}")
            return False
        with open(codex_toml, "r", encoding="utf-8") as f:
            current = f.read()
        if "[mcp_servers.agentcache]" not in current:
            print(
                f"[--] {self.display_name}: no [mcp_servers.agentcache] block in "
                f"{codex_toml}"
            )
            return False
        python_exe_posix = sys.executable.replace("\\", "/")
        mcp_stdio_posix = get_mcp_stdio_path().replace("\\", "/")
        if codex_entry_matches(current, python_exe_posix, mcp_stdio_posix):
            print(f"[OK] {self.display_name}: up to date ({codex_toml})")
            return True
        print(
            f"[!!] {self.display_name}: STALE ({codex_toml}) — "
            "block does not match desired command/args/env"
        )
        return False

    def install(self, args):
        codex_toml = os.path.join(get_home_dir(), ".codex", "config.toml")
        mcp_stdio_path = get_mcp_stdio_path()

        url = (
            os.environ.get("AGENTCACHE_URL")
            or os.environ.get("AGENTMEMORY_URL")
            or "http://localhost:3111"
        )
        secret = os.environ.get("AGENTCACHE_SECRET") or os.environ.get(
            "AGENTMEMORY_SECRET"
        )
        python_exe_posix = sys.executable.replace("\\", "/")
        mcp_stdio_posix = mcp_stdio_path.replace("\\", "/")
        toml_block = f"""
[mcp_servers.agentcache]
command = "{python_exe_posix}"
args = ["{mcp_stdio_posix}"]
[mcp_servers.agentcache.env]
AGENTCACHE_URL = "{url}"
"""
        if secret:
            toml_block += f'AGENTCACHE_SECRET = "{secret}"\n'

        exists = os.path.exists(codex_toml)
        current = ""
        if exists:
            with open(codex_toml, "r", encoding="utf-8") as f:
                current = f.read()

        wired = "[mcp_servers.agentcache]" in current
        up_to_date = wired and codex_entry_matches(
            current, python_exe_posix, mcp_stdio_posix
        )
        if up_to_date and not args.force:
            print(
                f"[OK] Codex CLI already wired in {codex_toml} "
                "(re-run with --force to overwrite)"
            )
        else:
            action = "update" if wired else "write"
            if args.dry_run:
                print(
                    f"[dry-run] Would {action} [mcp_servers.agentcache] block in {codex_toml}"
                )
            else:
                backup = backup_file(codex_toml, "codex", "toml")
                if backup:
                    print(f"Backed up config to {backup}")

                # Strip existing block if forcing
                cleaned = current
                if wired:
                    lines = current.splitlines()
                    out = []
                    skipping = False
                    for line in lines:
                        trimmed = line.strip()
                        if (
                            trimmed == "[mcp_servers.agentcache]"
                            or trimmed == "[mcp_servers.agentcache.env]"
                        ):
                            skipping = True
                            continue
                        if (
                            skipping
                            and trimmed.startswith("[")
                            and trimmed != "[mcp_servers.agentcache.env]"
                        ):
                            skipping = False
                        if not skipping:
                            out.append(line)
                    cleaned = "\n".join(out).strip()

                next_toml = (
                    cleaned + ("\n\n" if cleaned else "") + toml_block.strip() + "\n"
                )
                os.makedirs(os.path.dirname(codex_toml), exist_ok=True)
                with open(codex_toml, "w", encoding="utf-8") as f:
                    f.write(next_toml)
                if wired:
                    print(f"[OK] Updated existing agentcache MCP entry in {codex_toml}")
                else:
                    print(f"[OK] Wired Codex CLI TOML configuration to {codex_toml}")

        if args.with_hooks:
            codex_hooks = os.path.join(get_home_dir(), ".codex", "hooks.json")
            try:
                plugin_root = get_plugin_root()
                existing_hooks = read_json_safe(codex_hooks)
                merged = build_merged_hooks(
                    existing_hooks, plugin_root, "hooks.codex.json"
                )

                if args.dry_run:
                    print(f"[dry-run] Would merge hooks into {codex_hooks}")
                else:
                    backup = backup_file(codex_hooks, "codex-hooks")
                    if backup:
                        print(f"Backed up hooks to {backup}")
                    write_json_atomic(codex_hooks, merged)
                    print(f"[OK] Wired Codex hooks workaround to {codex_hooks}")
            except Exception as e:
                print(f"[FAIL] Failed to configure Codex hooks: {e}")


class HermesAdapter:
    name = "hermes"
    display_name = "Hermes Agent"

    def detect(self):
        hermes_dir = os.path.join(get_home_dir(), ".hermes")
        return os.path.exists(hermes_dir)

    def get_plugin_source_dir(self):
        """Return the packaged Hermes plugin source directory.

        Uses importlib.resources so the lookup survives editable installs,
        zipapps, and any environment where __file__-relative walks lie.
        """
        return str(resources.files("agentcache.integrations.hermes"))

    def verify(self, args):
        dest = os.path.join(get_home_dir(), ".hermes", "plugins", "agentcache")
        if not os.path.isdir(dest):
            print(f"[--] {self.display_name}: plugin not installed at {dest}")
            return False
        expected = ("__init__.py", "plugin.yaml")
        missing = [f for f in expected if not os.path.isfile(os.path.join(dest, f))]
        if missing:
            print(
                f"[!!] {self.display_name}: STALE ({dest}) — missing files: {missing}"
            )
            return False
        print(f"[OK] {self.display_name}: plugin present at {dest}")
        return True

    def install(self, args):
        dest_dir = os.path.join(get_home_dir(), ".hermes", "plugins", "agentcache")
        hermes_src = self.get_plugin_source_dir()

        if not os.path.exists(hermes_src):
            print(
                f"[FAIL] Failed: Packaged Hermes plugin not found at {hermes_src}. "
                "Reinstall agentcache-core."
            )
            return

        if args.dry_run:
            action = "overwrite" if os.path.exists(dest_dir) else "copy"
            print(f"[dry-run] Would {action} {hermes_src} to {dest_dir}")
        else:
            if os.path.exists(dest_dir):
                if not args.force:
                    print(
                        f"[OK] Hermes plugin already installed at {dest_dir} "
                        "(re-run with --force to overwrite)"
                    )
                    return
                shutil.rmtree(dest_dir)

            shutil.copytree(hermes_src, dest_dir)
            print(f"[OK] Copied Hermes cache provider plugin to {dest_dir}")
            print("To finish configuration, add to ~/.hermes/config.yaml:")
            print("  memory:")
            print("    provider: agentcache")
            print("  mcp_servers:")
            print("    agentcache:")
            print("      command: python")
            print(f'      args: ["{get_mcp_stdio_path()}"]')


class AntigravityAdapter:
    name = "antigravity"
    display_name = "Antigravity"

    def get_user_dir(self):
        if sys.platform == "darwin":
            return os.path.join(
                get_home_dir(), "Library", "Application Support", "Antigravity", "User"
            )
        elif sys.platform == "win32":
            appdata = get_appdata_dir()
            return os.path.join(appdata, "Antigravity", "User")
        else:
            return os.path.join(get_home_dir(), ".config", "Antigravity", "User")

    def get_gemini_mcp_dir(self):
        return os.path.join(
            get_home_dir(), ".gemini", "antigravity", "mcp", "agentcache"
        )

    def detect(self):
        gemini_parent = os.path.dirname(os.path.dirname(self.get_gemini_mcp_dir()))
        return os.path.exists(gemini_parent) or os.path.exists(self.get_user_dir())

    def install_gemini_schemas(self, args):
        gemini_mcp_dir = self.get_gemini_mcp_dir()
        if args.dry_run:
            print(
                f"[dry-run] Would create directory {gemini_mcp_dir} and write tool schema JSON files."
            )
            return

        os.makedirs(gemini_mcp_dir, exist_ok=True)
        try:
            # Dynamic import to avoid circular dependency
            from .routes.mcp import get_mcp_tools_schemas

            tools = get_mcp_tools_schemas()
        except Exception as e:
            print(f"[FAIL] Could not load tool schemas: {e}")
            return

        for tool in tools:
            tool_name = tool["name"]
            schema = {
                "name": tool_name,
                "description": tool.get("description", ""),
                "parameters": tool.get("inputSchema", {}),
            }
            tool_file_path = os.path.join(gemini_mcp_dir, f"{tool_name}.json")

            if os.path.exists(tool_file_path) and not args.force:
                continue

            with open(tool_file_path, "w", encoding="utf-8") as f:
                json.dump(schema, f, indent=2)
                f.write("\n")
        print(f"[OK] Installed {len(tools)} tool schemas to {gemini_mcp_dir}")

    def verify(self, args):
        user_dir = self.get_user_dir()
        return verify_json_mcp_entry(
            os.path.join(user_dir, "mcp_config.json"),
            "mcpServers",
            "Antigravity VS Code client",
        )

    def install(self, args):
        # 1. Install tool schemas under ~/.gemini/antigravity/mcp/agentcache
        gemini_parent = os.path.dirname(os.path.dirname(self.get_gemini_mcp_dir()))
        if os.path.exists(gemini_parent) or args.force:
            self.install_gemini_schemas(args)

        # 2. Wire the VS Code/User AppData client config if present
        user_dir = self.get_user_dir()
        if os.path.exists(user_dir) or args.force:
            install_json_mcp_entry(
                os.path.join(user_dir, "mcp_config.json"),
                servers_key="mcpServers",
                display_name="Antigravity VS Code client",
                args=args,
                backup_prefix="antigravity",
            )


class KiroAdapter:
    name = "kiro"
    display_name = "Kiro"

    def detect(self):
        kiro_dir = os.path.join(get_home_dir(), ".kiro")
        return os.path.exists(kiro_dir)

    def get_config_path(self):
        return os.path.join(get_home_dir(), ".kiro", "settings", "mcp.json")

    def verify(self, args):
        return verify_json_mcp_entry(
            self.get_config_path(), "mcpServers", self.display_name
        )

    def install(self, args):
        install_json_mcp_entry(
            self.get_config_path(),
            servers_key="mcpServers",
            display_name=self.display_name,
            args=args,
            backup_prefix="kiro",
        )


class VSCodeAdapter:
    name = "vscode"
    display_name = "VS Code"

    def get_workspace_config_path(self):
        return os.path.join(os.getcwd(), ".vscode", "mcp.json")

    def get_user_config_path(self):
        if sys.platform == "darwin":
            return os.path.join(
                get_home_dir(),
                "Library",
                "Application Support",
                "Code",
                "User",
                "mcp.json",
            )
        elif sys.platform == "win32":
            appdata = get_appdata_dir()
            return os.path.join(appdata, "Code", "User", "mcp.json")
        else:
            return os.path.join(get_home_dir(), ".config", "Code", "User", "mcp.json")

    def get_config_path(self):
        workspace_config = self.get_workspace_config_path()
        if os.path.isdir(os.path.dirname(workspace_config)):
            return workspace_config
        return self.get_user_config_path()

    def detect(self):
        return (
            os.path.exists(os.path.dirname(self.get_workspace_config_path()))
            or os.path.exists(os.path.dirname(self.get_user_config_path()))
            or shutil.which("code") is not None
        )

    def verify(self, args):
        return verify_json_mcp_entry(
            self.get_config_path(), "servers", self.display_name
        )

    def install(self, args):
        if getattr(args, "with_hooks", False):
            print(
                "[INFO] VS Code has no native hook installer; skipping hooks.",
                file=sys.stderr,
            )
        install_json_mcp_entry(
            self.get_config_path(),
            servers_key="servers",
            display_name=self.display_name,
            args=args,
            backup_prefix="vscode",
        )


class RulesGeneratorAdapter:
    name = "cursor"
    display_name = "Workspace Rules (Cursor/Cline/Windsurf)"

    def detect(self):
        # Always available for rules generation in current directory
        return True

    def install(self, args):
        rule_content = """# Agent Cache Rules

This workspace is integrated with long-term semantic memory via `agentcache-python`.
You must act as your own cache manager by calling the cache MCP tools at critical boundaries.

## Rules & Workflow

1. **Initial Search (Prefetch Context)**:
   At the start of every session or new task, immediately call `cache_smart_search` with terms related to the current objective. This retrieves past architecture patterns, preferences, bug fixes, or lessons.
   - Example: `cache_smart_search(query="jwt token rotation logic")`

2. **Lessons & Insights Capture**:
   When you successfully debug a complex error, discover an undocumented requirement, or establish a convention, persist it:
   - Call `cache_lesson_save` to record lessons that improve your coding capabilities. Duplicate saves strengthen confidence scores.
   - Call `cache_save` to save long-term structural facts. Always extract 2-5 specific lowercased tags (e.g. `auth-flow`, `refresh-token`) as concepts.

3. **Checklist Before Ending**:
   Before stating a task is complete:
   - Reflect on whether any lessons learned should be saved.
   - Call `cache_reflect` to automatically distribute observations into slots if needed.
"""
        cwd = os.getcwd()

        # Write to .cursorrules
        cursorrules = os.path.join(cwd, ".cursorrules")
        clineskills = os.path.join(cwd, ".clineskills")
        windsurfrules = os.path.join(cwd, ".windsurfrules")

        if args.dry_run:
            print(f"[dry-run] Would write rules templates to {cwd}")
        else:
            with open(cursorrules, "w", encoding="utf-8") as f:
                f.write(rule_content)
            with open(clineskills, "w", encoding="utf-8") as f:
                f.write(rule_content)
            with open(windsurfrules, "w", encoding="utf-8") as f:
                f.write(rule_content)
            print("[OK] Generated rule templates in current workspace:")
            print(f"  - {cursorrules}")
            print(f"  - {clineskills}")
            print(f"  - {windsurfrules}")


ADAPTERS = [
    ClaudeCodeAdapter(),
    CodexAdapter(),
    HermesAdapter(),
    AntigravityAdapter(),
    KiroAdapter(),
    VSCodeAdapter(),
    RulesGeneratorAdapter(),
]


def map_agent_alias(name: str) -> str:
    if not name:
        return name
    name = name.lower().strip()
    if name in (
        "antigravity",
        "/anti-gravity",
        "anti-gravity",
        "/antigravity",
        "antigravity",
    ):
        return "antigravity"
    if name in ("claude", "claude-code", "claudecode", "claude code"):
        return "claude-code"
    if name in ("kiro", "keyro"):
        return "kiro"
    if name in ("vscode", "vs-code", "visual-studio-code", "vs code"):
        return "vscode"
    return name


def run_connect(args):
    # Normalize/Map target agent alias if provided
    agent_name = getattr(args, "agent", None)
    if agent_name:
        agent_name = map_agent_alias(agent_name)
        setattr(args, "agent", agent_name)

    valid_names = [a.name for a in ADAPTERS]
    if agent_name and agent_name not in valid_names:
        print(
            f"[FAIL] Unknown agent: {agent_name}. Supported agents: {', '.join(valid_names)}"
        )
        sys.exit(1)

    verify_mode = getattr(args, "verify", False)

    targets = []
    if getattr(args, "all", False) or (verify_mode and not agent_name):
        # In verify mode with no explicit target, inspect every detected
        # client so the user gets a single diff report.
        targets = [a for a in ADAPTERS if a.detect() and a.name != "cursor"]
    else:
        matched = [a for a in ADAPTERS if a.name == agent_name]
        if matched:
            targets = matched

    if not targets:
        print("No agents detected or matched target.")
        sys.exit(1)

    for target in targets:
        if not target.detect() and not getattr(args, "force", False):
            print(
                f"[FAIL] {target.display_name} not detected on this system. (Use --force to install anyway)"
            )
            continue

        if verify_mode:
            verify_fn = getattr(target, "verify", None)
            if verify_fn is None:
                print(
                    f"[--] {target.display_name}: --verify not supported for this adapter"
                )
                continue
            try:
                verify_fn(args)
            except Exception as e:
                print(f"[FAIL] Failed to verify {target.display_name}: {e}")
            continue

        print(f"Wiring {target.display_name}...")
        try:
            target.install(args)
        except Exception as e:
            print(f"[FAIL] Failed to install {target.display_name}: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Wired agentcache MCP and Hooks into client agents."
    )
    parser.add_argument(
        "agent",
        nargs="?",
        help="Specify target agent (antigravity, claude-code, kiro, etc.).",
    )
    parser.add_argument(
        "--with-hooks",
        action="store_true",
        help="Install global workspace hook execution blocks (Claude/Codex).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log proposed configuration modifications without writing.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing configuration settings.",
    )
    parser.add_argument(
        "--all", action="store_true", help="Attempt connection to all detected agents."
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help=(
            "Diff each detected agentcache MCP entry against what would be "
            "written. Read-only — no writes, backups, or hook installs."
        ),
    )

    args = parser.parse_args()

    if not args.agent and not args.all and not args.verify:
        parser.print_help()
        print("\nAvailable agents:")
        for a in ADAPTERS:
            print(f"  - {a.name:15} ({a.display_name})")
        sys.exit(0)

    run_connect(args)


if __name__ == "__main__":
    main()
