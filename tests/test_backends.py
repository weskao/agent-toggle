"""MCP backends: TOML text slices and ~/.claude.json scope lookup."""
from __future__ import annotations

import json

from base import SandboxCase

from agent_toggle import fs, mechanisms
from agent_toggle.backends import mcp_json, mcp_toml


class TomlTest(SandboxCase):
    def test_toml_block_spans_subtables(self) -> None:
        text = (
            "[mcp_servers.alpha]\n"
            'command = "a"\n'
            "\n"
            "[mcp_servers.beta]\n"
            'command = "b"\n'
            "[mcp_servers.beta.tools.thing]\n"
            "enabled = true\n"
            "\n"
            "[other]\n"
            "k = 1\n"
        )
        span = mcp_toml.toml_block(text, "beta")
        block = "".join(text.splitlines(keepends=True)[span[0]:span[1]])
        # toml block includes its sub-tables
        self.assertTrue("beta.tools.thing" in block and "enabled = true" in block)
        # toml block stops before the next top-level table
        self.assertNotIn("[other]", block)
        # toml block excludes the previous server
        self.assertNotIn("alpha", block)
        # unknown server returns None
        self.assertIsNone(mcp_toml.toml_block(text, "nope"))

    def test_toml_mcp_roundtrip(self) -> None:
        cfg = self.home / "config.toml"
        cfg.write_text('[general]\nx = 1\n\n[mcp_servers.tg]\ncommand = "tg"\n'
                       '[mcp_servers.tg.tools.send]\nenabled = true\n')
        block = mcp_toml.codex_mcp_remove(cfg, "tg")
        # removal drops the server from config
        self.assertNotIn("mcp_servers.tg", cfg.read_text())
        # removal keeps unrelated tables
        self.assertIn("[general]", cfg.read_text())
        mcp_toml.codex_mcp_add(cfg, block)
        # re-add restores the server
        self.assertIn("[mcp_servers.tg]", cfg.read_text())
        # re-add restores sub-tables
        self.assertIn("tg.tools.send", cfg.read_text())


class ClaudeJsonScopeTest(SandboxCase):
    def claude_json(self, payload: dict) -> None:
        fs.claude_json().write_text(json.dumps(payload))

    def test_mcp_lookup_reports_scope_and_project(self) -> None:
        self.claude_json({
            "mcpServers": {"dart": {"command": "dart"}},
            "projects": {"/work/app": {"mcpServers": {"mobile": {"command": "npx"}}}},
        })
        # user-scope server is found
        self.assertEqual(mcp_json.claude_mcp_config("dart"), ({"command": "dart"}, "user", None))
        # local-scope server is found with its project
        self.assertEqual(mcp_json.claude_mcp_config("mobile"),
                         ({"command": "npx"}, "local", "/work/app"))
        # absent server is still None
        self.assertIsNone(mcp_json.claude_mcp_config("nope"))

    def test_mcp_lookup_refuses_ambiguous_local_scope(self) -> None:
        """Same name in two projects: guessing would restore it to the wrong one."""
        self.claude_json({"projects": {
            "/work/a": {"mcpServers": {"dup": {"command": "a"}}},
            "/work/b": {"mcpServers": {"dup": {"command": "b"}}},
        }})
        # ambiguous local scope is refused
        with self.assertRaisesRegex(LookupError, "2 projects"):
            mcp_json.claude_mcp_config("dup")
        state = {"version": 2, "disabled": {}}
        fails = mechanisms.toggle_mcp("disable", ["dup"], state, "claude", self.home,
                                      "claude-json")
        # ambiguity counts as a failure, not a crash
        self.assertEqual(fails, 1)
        # nothing recorded for the ambiguous server
        self.assertFalse(state["disabled"])

    def test_mcp_enable_defaults_to_user_scope_for_old_entries(self) -> None:
        """Entries parked before scope tracking have no scope field."""
        fs.backup_dir().mkdir(parents=True)
        bp = fs.backup_dir() / "claude__legacy.json"
        bp.write_text('{"command": "x"}')
        self.cli_rc = 0
        state = {"version": 2, "disabled": {"claude:mcp:legacy": {
            "harness": "claude", "type": "mcp", "name": "legacy",
            "backend": "claude-json", "backup": str(bp), "at": "2026-01-01"}}}
        mechanisms.toggle_mcp("enable", ["legacy"], state, "claude", self.home, "claude-json")
        args, cwd = self.cli_calls[-1]
        # scopeless entry restores to user scope
        self.assertIn("user", args)
        # scopeless entry restores with no cwd
        self.assertIsNone(cwd)

