#!/usr/bin/env python3
"""
agentcache CLI entrypoint.

Commands:
  agentcache serve [--port PORT] [--host HOST]
  agentcache migrate [--dry-run]
  agentcache export [--output FILE]
"""

import argparse
import json
import os


def cmd_serve(args) -> None:
    """Start the Flask server."""
    os.environ.setdefault("III_REST_PORT", str(args.port))
    if getattr(args, "no_workers", False):
        os.environ["AGENTCACHE_DISABLE_WORKERS"] = "true"
    from .app import create_app

    flask_app = create_app()
    print(f"[cli] Starting agentcache on {args.host}:{args.port}")
    flask_app.run(host=args.host, port=args.port, debug=False)


def cmd_migrate(args) -> None:
    """Run session → folder migration."""
    from .db import StateKV
    from .functions import migrate_sessions_to_folders

    kv = StateKV()
    result = migrate_sessions_to_folders(kv, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    if args.dry_run:
        print(
            f"\n[cli] Dry run — no changes made. "
            f"{result['migrated_sessions']} sessions, "
            f"{result['migrated_observations']} observations would be migrated."
        )
    else:
        print(
            f"\n[cli] Migration complete: "
            f"{result['migrated_sessions']} sessions, "
            f"{result['migrated_observations']} observations migrated."
        )
    if result.get("errors"):
        print(f"[cli] {len(result['errors'])} errors:")
        for e in result["errors"]:
            print(f"  - {e}")


def cmd_export(args) -> None:
    """Export all data as JSON."""
    from .db import StateKV
    from .functions import export_data

    kv = StateKV()
    data = export_data(kv, {})
    output = json.dumps(data, indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"[cli] Exported to {args.output}")
        total_obs = sum(
            len(folder.get("observations", [])) for folder in data.get("folders", [])
        )
        print(
            f"[cli] {len(data.get('folders', []))} folders, {total_obs} observations, "
            f"{len(data.get('memories', []))} memories"
        )
    else:
        print(output)


def cmd_connect(args) -> None:
    """Connect/wire MCP and hooks to client agents."""
    from .connect import run_connect

    run_connect(args)


def cmd_worker(args) -> None:
    """Run background worker tasks in a dedicated process."""
    import time

    tasks = [t.strip() for t in args.tasks.split(",")]
    print(f"[worker] Starting worker process for tasks: {tasks}")

    from .app import init_services

    kv, _, _ = init_services()

    from .workers import _shutting_down, start_background_workers

    start_background_workers(kv, tasks=tasks)

    try:
        while not _shutting_down.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        print("[worker] KeyboardInterrupt received. Initiating shutdown...")
        import signal

        from .workers import _shutdown_handler

        _shutdown_handler(signal.SIGINT, None)


def cmd_context(args) -> None:
    """Generate or watch .agentcache_context.md in the current folder."""
    import time

    from .app import init_services
    from .functions import KV, normalize_folder_path

    # 1. Resolve folder and agent
    cwd = os.getcwd()
    try:
        folder_path = normalize_folder_path(cwd)
    except Exception as e:
        print(f"[cli] Failed to normalize current path: {e}")
        return

    agent_id = args.agent or os.getenv("AGENT_ID") or "agent"
    output_file = args.output or ".agentcache_context.md"

    print(f"[context] Target: {folder_path} (agent: {agent_id}) -> {output_file}")

    # 2. Init DB services
    kv, _, _ = init_services()

    def generate_context() -> str:
        # Fetch metadata
        meta = kv.get(KV.folder_meta(folder_path, agent_id), "meta") or {}
        obs_count = meta.get("obsCount", 0)
        last_updated = meta.get("lastUpdated", "Never")

        # Fetch recent observations (sorted desc by timestamp)
        obs_list = kv.list(KV.folder_obs(folder_path, agent_id))
        obs_list = sorted(obs_list, key=lambda x: x.get("timestamp", ""), reverse=True)
        recent_obs = obs_list[:5]

        # Fetch memories relevant to this agent or project (case-insensitive match)
        folder_name = os.path.basename(cwd) or "Workspace"
        project_name = folder_name.strip().lower()

        memories = kv.list(KV.memories)
        relevant_memories = []
        for m in memories:
            if m.get("isLatest") is False:
                continue

            match_agent = m.get("agentId") == agent_id
            m_proj = m.get("project")
            match_project = m_proj and m_proj.strip().lower() == project_name

            if match_agent or match_project:
                relevant_memories.append(m)

        relevant_memories = sorted(
            relevant_memories, key=lambda x: x.get("updatedAt", ""), reverse=True
        )
        recent_memories = relevant_memories[:5]

        lines = [
            "<!-- Generated by agentcache. DO NOT EDIT. -->",
            f"# Agent Cache Context - {folder_name}",
            "",
            "## Project Metadata",
            f"- **Path:** `{folder_path}`",
            f"- **Agent ID:** `{agent_id}`",
            f"- **Total Observations:** {obs_count}",
            f"- **Last Updated:** {last_updated}",
            "",
            "## Pinned Memories & Lessons",
        ]

        if not recent_memories:
            lines.append("No active long-term memories registered for this agent.")
        else:
            for mem in recent_memories:
                m_type = mem.get("type", "fact").upper()
                lines.append(f"### [{m_type}] {mem.get('title', 'Untitled')}")
                lines.append(f"{mem.get('content', '')}")
                lines.append("")

        lines.extend(["", "## Recent Activity Timeline (Last 5 Observations)", ""])

        if not recent_obs:
            lines.append("No observations logged yet in this workspace.")
        else:
            for obs in recent_obs:
                ts = obs.get("timestamp", "")
                o_type = obs.get("type", "observation")
                text = obs.get("text", "")
                lines.append(f"- **[{ts}] ({o_type}):** {text.strip()}")

        lines.append("")
        return "\n".join(lines)

    def write_context():
        content = generate_context()
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[context] Updated {output_file}")

    # Initial write
    write_context()

    print(
        "[context] Tip: Add the following instruction to your IDE's system prompt or .cursorrules to automatically load this cache context:"
    )
    print(
        "--------------------------------------------------------------------------------"
    )
    print(
        "Always load and inspect the local `.agentcache_context.md` file at the start of"
    )
    print(
        "your session to retrieve the latest project state, lessons, and pinned memories."
    )
    print(
        "--------------------------------------------------------------------------------"
    )

    if args.watch:
        print("[context] Watching for database changes. Press Ctrl+C to stop...")
        last_meta_state = None
        try:
            while True:
                # Check meta state change
                meta = kv.get(KV.folder_meta(folder_path, agent_id), "meta") or {}
                meta_state = (meta.get("obsCount", 0), meta.get("lastUpdated", ""))
                if meta_state != last_meta_state:
                    if last_meta_state is not None:
                        write_context()
                    last_meta_state = meta_state
                time.sleep(2)
        except KeyboardInterrupt:
            print("[context] Watcher stopped.")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="agentcache",
        description="agentcache — AI agent cache server",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # serve
    serve_parser = subparsers.add_parser("serve", help="Start the cache server")
    serve_parser.add_argument(
        "--port", type=int, default=int(os.getenv("III_REST_PORT", "3111"))
    )
    serve_parser.add_argument("--host", default="0.0.0.0")
    serve_parser.add_argument(
        "--no-workers", action="store_true", help="Disable background worker threads"
    )

    # worker
    worker_parser = subparsers.add_parser(
        "worker", help="Run background worker processes"
    )
    worker_parser.add_argument(
        "--tasks",
        default="index,forget",
        help="Comma-separated tasks to run (index, forget)",
    )

    # migrate
    migrate_parser = subparsers.add_parser(
        "migrate", help="Migrate legacy session data to folder-based storage"
    )
    migrate_parser.add_argument(
        "--dry-run", action="store_true", help="Preview without writing"
    )

    # export
    export_parser = subparsers.add_parser("export", help="Export all data as JSON")
    export_parser.add_argument(
        "--output", "-o", help="Output file path (default: stdout)"
    )

    # context
    context_parser = subparsers.add_parser(
        "context", help="Generate or watch .agentcache_context.md local file"
    )
    context_parser.add_argument(
        "--agent", help="Specify agent ID (defaults to AGENT_ID or 'agent')"
    )
    context_parser.add_argument(
        "--output", "-o", help="Output file path (default: .agentcache_context.md)"
    )
    context_parser.add_argument(
        "--watch", action="store_true", help="Watch for updates in real-time"
    )

    # connect
    connect_parser = subparsers.add_parser(
        "connect", help="Connect/wire MCP and hooks to client agents"
    )
    connect_parser.add_argument(
        "agent",
        nargs="?",
        help="Specify target agent (antigravity, claude-code, kiro, etc.)",
    )
    connect_parser.add_argument(
        "--with-hooks",
        action="store_true",
        help="Install global workspace hook execution blocks (Claude/Codex).",
    )
    connect_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log proposed configuration modifications without writing.",
    )
    connect_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing configuration settings.",
    )
    connect_parser.add_argument(
        "--all", action="store_true", help="Attempt connection to all detected agents."
    )

    args = parser.parse_args()

    if args.command == "serve":
        cmd_serve(args)
    elif args.command == "worker":
        cmd_worker(args)
    elif args.command == "migrate":
        cmd_migrate(args)
    elif args.command == "export":
        cmd_export(args)
    elif args.command == "context":
        cmd_context(args)
    elif args.command == "connect":
        cmd_connect(args)


if __name__ == "__main__":
    main()
