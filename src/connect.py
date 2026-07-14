#!/usr/bin/env python3
import os
import sys
import json
import shutil
import argparse

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
    # connect.py resides in src/, plugin/ is in the parent directory
    src_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(src_dir)
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

    def install(self, args):
        claude_json = os.path.join(get_home_dir(), ".claude.json")
        mcp_stdio_path = get_mcp_stdio_path()

        existing = read_json_safe(claude_json)
        next_cfg = existing.copy()
        servers = next_cfg.get("mcpServers", {})

        already_has = "agentcache" in servers
        if already_has and not args.force:
            print(f"[OK] Claude Code already wired in {claude_json}")
        else:
            if args.dry_run:
                print(f"[dry-run] Would write mcpServers.agentcache in {claude_json}")
            else:
                backup = backup_file(claude_json, "claude-code")
                if backup:
                    print(f"Backed up configuration to {backup}")

                env = {
                    "AGENTCACHE_URL": os.environ.get("AGENTCACHE_URL")
                    or os.environ.get("AGENTMEMORY_URL")
                    or "http://localhost:3111"
                }
                secret = os.environ.get("AGENTCACHE_SECRET") or os.environ.get(
                    "AGENTMEMORY_SECRET"
                )
                if secret:
                    env["AGENTCACHE_SECRET"] = secret
                servers["agentcache"] = {
                    "command": sys.executable,
                    "args": [mcp_stdio_path],
                    "env": env,
                }
                next_cfg["mcpServers"] = servers
                write_json_atomic(claude_json, next_cfg)
                print(f"[OK] Wired Claude Code MCP to {claude_json}")

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

    def detect(self):
        codex_dir = os.path.join(get_home_dir(), ".codex")
        return os.path.exists(codex_dir)

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
        if wired and not args.force:
            print(f"[OK] Codex CLI already wired in {codex_toml}")
        else:
            if args.dry_run:
                print(
                    f"[dry-run] Would write [mcp_servers.agentcache] block to {codex_toml}"
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

    def install(self, args):
        dest_dir = os.path.join(get_home_dir(), ".hermes", "plugins", "agentcache")
        src_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(src_dir)
        hermes_src = os.path.join(project_root, "integrations", "hermes")

        if not os.path.exists(hermes_src):
            print(
                f"[FAIL] Failed: Source integrations/hermes not found at {hermes_src}"
            )
            return

        if args.dry_run:
            print(f"[dry-run] Would copy {hermes_src} to {dest_dir}")
        else:
            if os.path.exists(dest_dir):
                if not args.force:
                    print(
                        f"[OK] Hermes plugin directory already exists at {dest_dir}. Use --force to overwrite."
                    )
                    return
                shutil.rmtree(dest_dir)

            shutil.copytree(hermes_src, dest_dir)
            print(f"[OK] Copied Hermes cache provider plugin to {dest_dir}")
            print("To finish configuration, add to ~/.hermes/config.yaml:")
            print("  mcp_servers:")
            print("    agentcache:")
            print("      command: python")
            print(f'      args: ["{get_mcp_stdio_path()}"]')
            print("  cache:")
            print("    provider: agentcache")


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
            try:
                from routes.mcp import get_mcp_tools_schemas
            except ImportError:
                try:
                    from src.routes.mcp import get_mcp_tools_schemas
                except ImportError:
                    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                    from routes.mcp import get_mcp_tools_schemas

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

    def install(self, args):
        # 1. Install tool schemas under ~/.gemini/antigravity/mcp/agentcache
        gemini_parent = os.path.dirname(os.path.dirname(self.get_gemini_mcp_dir()))
        if os.path.exists(gemini_parent) or args.force:
            self.install_gemini_schemas(args)

        # 2. Wire the VS Code/User AppData client config if present
        user_dir = self.get_user_dir()
        if os.path.exists(user_dir) or args.force:
            mcp_config_path = os.path.join(user_dir, "mcp_config.json")
            mcp_stdio_path = get_mcp_stdio_path()

            existing = read_json_safe(mcp_config_path)
            next_cfg = existing.copy()
            servers = next_cfg.get("mcpServers", {})

            already_has = "agentcache" in servers
            if already_has and not args.force:
                print(
                    f"[OK] Antigravity VS Code client already wired in {mcp_config_path}"
                )
            else:
                if args.dry_run:
                    print(
                        f"[dry-run] Would write mcpServers.agentcache in {mcp_config_path}"
                    )
                else:
                    backup = backup_file(mcp_config_path, "antigravity")
                    if backup:
                        print(f"Backed up config to {backup}")

                    env = {
                        "AGENTCACHE_URL": os.environ.get("AGENTCACHE_URL")
                        or os.environ.get("AGENTMEMORY_URL")
                        or "http://localhost:3111"
                    }
                    secret = os.environ.get("AGENTCACHE_SECRET") or os.environ.get(
                        "AGENTMEMORY_SECRET"
                    )
                    if secret:
                        env["AGENTCACHE_SECRET"] = secret
                    servers["agentcache"] = {
                        "command": sys.executable,
                        "args": [mcp_stdio_path],
                        "env": env,
                    }
                    next_cfg["mcpServers"] = servers
                    write_json_atomic(mcp_config_path, next_cfg)
                    print(
                        f"[OK] Wired Antigravity VS Code client MCP config in {mcp_config_path}"
                    )


class KiroAdapter:
    name = "kiro"
    display_name = "Kiro"

    def detect(self):
        kiro_dir = os.path.join(get_home_dir(), ".kiro")
        return os.path.exists(kiro_dir)

    def install(self, args):
        mcp_config_path = os.path.join(get_home_dir(), ".kiro", "settings", "mcp.json")
        mcp_stdio_path = get_mcp_stdio_path()

        existing = read_json_safe(mcp_config_path)
        next_cfg = existing.copy()
        servers = next_cfg.get("mcpServers", {})

        already_has = "agentcache" in servers
        if already_has and not args.force:
            print(f"[OK] Kiro already wired in {mcp_config_path}")
        else:
            if args.dry_run:
                print(
                    f"[dry-run] Would write mcpServers.agentcache in {mcp_config_path}"
                )
            else:
                backup = backup_file(mcp_config_path, "kiro")
                if backup:
                    print(f"Backed up config to {backup}")

                env = {
                    "AGENTCACHE_URL": os.environ.get("AGENTCACHE_URL")
                    or os.environ.get("AGENTMEMORY_URL")
                    or "http://localhost:3111"
                }
                secret = os.environ.get("AGENTCACHE_SECRET") or os.environ.get(
                    "AGENTMEMORY_SECRET"
                )
                if secret:
                    env["AGENTCACHE_SECRET"] = secret
                servers["agentcache"] = {
                    "command": sys.executable,
                    "args": [mcp_stdio_path],
                    "env": env,
                }
                next_cfg["mcpServers"] = servers
                write_json_atomic(mcp_config_path, next_cfg)
                print(f"[OK] Wired Kiro MCP config in {mcp_config_path}")


class VSCodeAdapter:
    name = "vscode"
    display_name = "VS Code"

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

    def detect(self):
        return os.path.exists(os.path.dirname(self.get_user_config_path()))

    def install(self, args):
        mcp_config_path = self.get_user_config_path()
        mcp_stdio_path = get_mcp_stdio_path()

        existing = read_json_safe(mcp_config_path)
        next_cfg = existing.copy()
        servers = next_cfg.get("servers", {})

        already_has = "agentcache" in servers
        if already_has and not args.force:
            print(f"[OK] VS Code already wired in {mcp_config_path}")
        else:
            if args.dry_run:
                print(f"[dry-run] Would write servers.agentcache in {mcp_config_path}")
            else:
                backup = backup_file(mcp_config_path, "vscode")
                if backup:
                    print(f"Backed up config to {backup}")

                env = {
                    "AGENTCACHE_URL": os.environ.get("AGENTCACHE_URL")
                    or os.environ.get("AGENTMEMORY_URL")
                    or "http://localhost:3111"
                }
                secret = os.environ.get("AGENTCACHE_SECRET") or os.environ.get(
                    "AGENTMEMORY_SECRET"
                )
                if secret:
                    env["AGENTCACHE_SECRET"] = secret
                servers["agentcache"] = {
                    "command": sys.executable,
                    "args": [mcp_stdio_path],
                    "env": env,
                }
                next_cfg["servers"] = servers
                write_json_atomic(mcp_config_path, next_cfg)
                print(f"[OK] Wired VS Code MCP config in {mcp_config_path}")


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

    targets = []
    if getattr(args, "all", False):
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

    args = parser.parse_args()

    if not args.agent and not args.all:
        parser.print_help()
        print("\nAvailable agents:")
        for a in ADAPTERS:
            print(f"  - {a.name:15} ({a.display_name})")
        sys.exit(0)

    run_connect(args)


if __name__ == "__main__":
    main()
