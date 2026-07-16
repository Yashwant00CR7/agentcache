"""Unit tests for the agentcache context CLI command."""

import argparse
import os
from unittest.mock import patch

from agentcache.cli import cmd_context
from agentcache.db import StateKV
from agentcache.functions import folder_observe, remember


def make_kv(tmp_path):
    db_path = os.path.join(str(tmp_path), "test.db")
    return StateKV(db_path=db_path)


def test_cli_context_generation(tmp_path):
    kv = make_kv(tmp_path)

    # 1. Add observations and memories
    folder = "/home/user/myproject"
    agent = "test-agent"

    folder_observe(
        kv,
        {
            "folderPath": folder,
            "agentId": agent,
            "text": "First observation",
            "timestamp": "2026-07-15T10:00:00Z",
        },
    )

    remember(
        kv, {"content": "A crucial project rule", "type": "fact", "agentId": agent}
    )

    remember(
        kv,
        {
            "content": "A project-wide memory",
            "type": "architecture",
            "project": "myproject",
            "agentId": "some-other-agent",
        },
    )

    output_file = os.path.join(str(tmp_path), "context.md")
    args = argparse.Namespace(agent=agent, output=output_file, watch=False)

    # Mock os.getcwd to match the project path and init_services to return our test db
    with (
        patch("os.getcwd", return_value=folder),
        patch("agentcache.app.init_services", return_value=(kv, None, None)),
    ):
        cmd_context(args)

    # 2. Verify file output
    assert os.path.exists(output_file)
    with open(output_file, "r", encoding="utf-8") as f:
        content = f.read()

    # Check that metadata and values are written
    assert "Agent Cache Context" in content
    assert "Project Metadata" in content
    assert "myproject" in content
    assert "test-agent" in content
    assert "First observation" in content
    assert "A crucial project rule" in content
    assert "A project-wide memory" in content
