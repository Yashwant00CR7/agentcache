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
    elif args.command == "migrate":
        cmd_migrate(args)
    elif args.command == "export":
        cmd_export(args)
    elif args.command == "connect":
        cmd_connect(args)


if __name__ == "__main__":
    main()
