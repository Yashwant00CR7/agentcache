#!/usr/bin/env python3
import os
import sys
import json
import shutil
import argparse
from pathlib import Path

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
                resolved_cmd = cmd.replace("${CLAUDE_PLUGIN_ROOT}", plugin_root.replace("\\", "/"))
                # Also replace python with sys.executable to use the correct Python instance
                if resolved_cmd.startswith("python "):
                    resolved_cmd = f'"{sys.executable.replace("\\", "/")}" ' + resolved_cmd[7:]
                resolved_handlers.append({
                    "type": handler.get("type"),
                    "command": resolved_cmd
                })
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

        already_has = "agentmemory" in servers
        if already_has and not args.force:
            print(f"[OK] Claude Code already wired in {claude_json}")
        else:
            if args.dry_run:
                print(f"[dry-run] Would write mcpServers.agentmemory in {claude_json}")
            else:
                backup = backup_file(claude_json, "claude-code")
                if backup:
                    print(f"Backed up configuration to {backup}")
                
                env = {"AGENTMEMORY_URL": os.environ.get("AGENTMEMORY_URL", "http://localhost:3111")}
                secret = os.environ.get("AGENTMEMORY_SECRET")
                if secret:
                    env["AGENTMEMORY_SECRET"] = secret
                servers["agentmemory"] = {
                    "command": sys.executable,
                    "args": [mcp_stdio_path],
                    "env": env
                }
                next_cfg["mcpServers"] = servers
                write_json_atomic(claude_json, next_cfg)
                print(f"[OK] Wired Claude Code MCP to {claude_json}")

        if args.with_hooks:
            claude_settings = os.path.join(get_home_dir(), ".claude", "settings.json")
            try:
                plugin_root = get_plugin_root()
                existing_settings = read_json_safe(claude_settings)
                merged = build_merged_hooks(existing_settings, plugin_root, "hooks.json")
                
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

        url = os.environ.get("AGENTMEMORY_URL", "http://localhost:3111")
        secret = os.environ.get("AGENTMEMORY_SECRET")
        toml_block = f"""
[mcp_servers.agentmemory]
command = "{sys.executable.replace('\\', '/')}"
args = ["{mcp_stdio_path.replace('\\', '/')}"]
[mcp_servers.agentmemory.env]
AGENTMEMORY_URL = "{url}"
"""
        if secret:
            toml_block += f'AGENTMEMORY_SECRET = "{secret}"\n'

        exists = os.path.exists(codex_toml)
        current = ""
        if exists:
            with open(codex_toml, "r", encoding="utf-8") as f:
                current = f.read()

        wired = "[mcp_servers.agentmemory]" in current
        if wired and not args.force:
            print(f"[OK] Codex CLI already wired in {codex_toml}")
        else:
            if args.dry_run:
                print(f"[dry-run] Would write [mcp_servers.agentmemory] block to {codex_toml}")
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
                        if trimmed == "[mcp_servers.agentmemory]" or trimmed == "[mcp_servers.agentmemory.env]":
                            skipping = True
                            continue
                        if skipping and trimmed.startswith("[") and trimmed != "[mcp_servers.agentmemory.env]":
                            skipping = False
                        if not skipping:
                            out.append(line)
                    cleaned = "\n".join(out).strip()
                
                next_toml = cleaned + ("\n\n" if cleaned else "") + toml_block.strip() + "\n"
                os.makedirs(os.path.dirname(codex_toml), exist_ok=True)
                with open(codex_toml, "w", encoding="utf-8") as f:
                    f.write(next_toml)
                print(f"[OK] Wired Codex CLI TOML configuration to {codex_toml}")

        if args.with_hooks:
            codex_hooks = os.path.join(get_home_dir(), ".codex", "hooks.json")
            try:
                plugin_root = get_plugin_root()
                existing_hooks = read_json_safe(codex_hooks)
                merged = build_merged_hooks(existing_hooks, plugin_root, "hooks.codex.json")
                
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
        dest_dir = os.path.join(get_home_dir(), ".hermes", "plugins", "agentmemory")
        src_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(src_dir)
        hermes_src = os.path.join(project_root, "integrations", "hermes")

        if not os.path.exists(hermes_src):
            print(f"[FAIL] Failed: Source integrations/hermes not found at {hermes_src}")
            return

        if args.dry_run:
            print(f"[dry-run] Would copy {hermes_src} to {dest_dir}")
        else:
            if os.path.exists(dest_dir):
                if not args.force:
                    print(f"[OK] Hermes plugin directory already exists at {dest_dir}. Use --force to overwrite.")
                    return
                shutil.rmtree(dest_dir)
            
            shutil.copytree(hermes_src, dest_dir)
            print(f"[OK] Copied Hermes memory provider plugin to {dest_dir}")
            print("To finish configuration, add to ~/.hermes/config.yaml:")
            print("  mcp_servers:")
            print("    agentmemory:")
            print("      command: python")
            print(f'      args: ["{get_mcp_stdio_path()}"]')
            print("  memory:")
            print("    provider: agentmemory")

class AntigravityAdapter:
    name = "antigravity"
    display_name = "Antigravity"

    def get_user_dir(self):
        if sys.platform == "darwin":
            return os.path.join(get_home_dir(), "Library", "Application Support", "Antigravity", "User")
        elif sys.platform == "win32":
            appdata = get_appdata_dir()
            return os.path.join(appdata, "Antigravity", "User")
        else:
            return os.path.join(get_home_dir(), ".config", "Antigravity", "User")

    def detect(self):
        return os.path.exists(self.get_user_dir())

    def install(self, args):
        mcp_config_path = os.path.join(self.get_user_dir(), "mcp_config.json")
        mcp_stdio_path = get_mcp_stdio_path()

        existing = read_json_safe(mcp_config_path)
        next_cfg = existing.copy()
        servers = next_cfg.get("mcpServers", {})

        already_has = "agentmemory" in servers
        if already_has and not args.force:
            print(f"[OK] Antigravity already wired in {mcp_config_path}")
        else:
            if args.dry_run:
                print(f"[dry-run] Would write mcpServers.agentmemory in {mcp_config_path}")
            else:
                backup = backup_file(mcp_config_path, "antigravity")
                if backup:
                    print(f"Backed up config to {backup}")
                
                env = {"AGENTMEMORY_URL": os.environ.get("AGENTMEMORY_URL", "http://localhost:3111")}
                secret = os.environ.get("AGENTMEMORY_SECRET")
                if secret:
                    env["AGENTMEMORY_SECRET"] = secret
                servers["agentmemory"] = {
                    "command": sys.executable,
                    "args": [mcp_stdio_path],
                    "env": env
                }
                next_cfg["mcpServers"] = servers
                write_json_atomic(mcp_config_path, next_cfg)
                print(f"[OK] Wired Antigravity MCP config in {mcp_config_path}")

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

        already_has = "agentmemory" in servers
        if already_has and not args.force:
            print(f"[OK] Kiro already wired in {mcp_config_path}")
        else:
            if args.dry_run:
                print(f"[dry-run] Would write mcpServers.agentmemory in {mcp_config_path}")
            else:
                backup = backup_file(mcp_config_path, "kiro")
                if backup:
                    print(f"Backed up config to {backup}")
                
                env = {"AGENTMEMORY_URL": os.environ.get("AGENTMEMORY_URL", "http://localhost:3111")}
                secret = os.environ.get("AGENTMEMORY_SECRET")
                if secret:
                    env["AGENTMEMORY_SECRET"] = secret
                servers["agentmemory"] = {
                    "command": sys.executable,
                    "args": [mcp_stdio_path],
                    "env": env
                }
                next_cfg["mcpServers"] = servers
                write_json_atomic(mcp_config_path, next_cfg)
                print(f"[OK] Wired Kiro MCP config in {mcp_config_path}")

class RulesGeneratorAdapter:
    name = "cursor"
    display_name = "Workspace Rules (Cursor/Cline/Windsurf)"

    def detect(self):
        # Always available for rules generation in current directory
        return True

    def install(self, args):
        rule_content = """# Agent Memory Rules

This workspace is integrated with long-term semantic memory via `agentmemory-python`.
You must act as your own memory manager by calling the memory MCP tools at critical boundaries.

## Rules & Workflow

1. **Initial Search (Prefetch Context)**:
   At the start of every session or new task, immediately call `memory_smart_search` with terms related to the current objective. This retrieves past architecture patterns, preferences, bug fixes, or lessons.
   - Example: `memory_smart_search(query="jwt token rotation logic")`

2. **Lessons & Insights Capture**:
   When you successfully debug a complex error, discover an undocumented requirement, or establish a convention, persist it:
   - Call `memory_lesson_save` to record lessons that improve your coding capabilities. Duplicate saves strengthen confidence scores.
   - Call `memory_save` to save long-term structural facts. Always extract 2-5 specific lowercased tags (e.g. `auth-flow`, `refresh-token`) as concepts.

3. **Checklist Before Ending**:
   Before stating a task is complete:
   - Reflect on whether any lessons learned should be saved.
   - Call `memory_reflect` to automatically distribute observations into slots if needed.
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
            print(f"[OK] Generated rule templates in current workspace:")
            print(f"  - {cursorrules}")
            print(f"  - {clineskills}")
            print(f"  - {windsurfrules}")

ADAPTERS = [
    ClaudeCodeAdapter(),
    CodexAdapter(),
    HermesAdapter(),
    AntigravityAdapter(),
    KiroAdapter(),
    RulesGeneratorAdapter()
]

def main():
    parser = argparse.ArgumentParser(description="Wired agentmemory MCP and Hooks into client agents.")
    parser.add_argument("agent", nargs="?", choices=[a.name for a in ADAPTERS], help="Specify target agent.")
    parser.add_argument("--with-hooks", action="store_true", help="Install global workspace hook execution blocks (Claude/Codex).")
    parser.add_argument("--dry-run", action="store_true", help="Log proposed configuration modifications without writing.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing configuration settings.")
    parser.add_argument("--all", action="store_true", help="Attempt connection to all detected agents.")
    
    args = parser.parse_args()

    if not args.agent and not args.all:
        parser.print_help()
        print("\nAvailable agents:")
        for a in ADAPTERS:
            print(f"  - {a.name:15} ({a.display_name})")
        sys.exit(0)

    targets = []
    if args.all:
        targets = [a for a in ADAPTERS if a.detect() and a.name != "cursor"]
    else:
        matched = [a for a in ADAPTERS if a.name == args.agent]
        if matched:
            targets = matched

    if not targets:
        print("No agents detected or matched target.")
        sys.exit(1)

    for target in targets:
        if not target.detect() and not args.force:
            print(f"[FAIL] {target.display_name} not detected on this system. (Use --force to install anyway)")
            continue
        
        print(f"Wiring {target.display_name}...")
        try:
            target.install(args)
        except Exception as e:
            print(f"[FAIL] Failed to install {target.display_name}: {e}")

if __name__ == "__main__":
    main()
