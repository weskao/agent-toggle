"""DESIGN s6.1 row 3: output shows names and paths, never backed-up values.

Every MCP config below carries a synthetic secret holding SENTINEL. Each command
path that reads, backs up or restores one runs in plain, --json and -v form; the
sentinel must never reach stdout, stderr, a JSON envelope, log.jsonl, state.json
or a profile. Only the backups under mcp-backups/ may hold it (that is their job),
and they must be 0600.
"""
from __future__ import annotations

import json
import os
import stat
import unittest
from unittest import mock

from test_cli_surface import CliCase

from agent_toggle import fs, store
from agent_toggle.backends import plugin_cli
from agent_toggle.ui import model

SENTINEL = "SENTINEL"
VARIANTS = ((), ("--json",), ("-v",), ("--json", "-v"))
POSIX = os.name != "nt"


class SecretAuditTest(CliCase):
    def setUp(self) -> None:
        super().setUp()
        self.cli_rc = 0
        self.seen: list[tuple[tuple[str, ...], str]] = []
        self.proj = self.tmp / "work" / "app"
        (self.proj / ".claude" / "skills").mkdir(parents=True)
        self.claude_json({
            "mcpServers": {"secret-mcp": {
                "headers": {"Authorization": "Bearer test-token-SENTINEL-000"},
                "type": "http", "url": "https://example.invalid/mcp?key=SENTINEL-001",
                "env": {"API_KEY": "SENTINEL-002"}}},
            "projects": {str(self.proj): {"mcpServers": {"local-mcp": {
                "env": {"TOKEN": "SENTINEL-003"}, "command": "demo"}}}}})
        codex = self.tmp / ".codex"
        codex.mkdir()
        (codex / "config.toml").write_text(
            '[mcp_servers.other-mcp]\ncommand = "other"\n\n'
            '[mcp_servers.secret-mcp]\ncommand = "demo"\n\n'
            '[mcp_servers.secret-mcp.env]\nTOKEN = "Bearer test-token-SENTINEL-004"\n',
            encoding="utf-8")
        copilot = self.tmp / ".copilot"
        copilot.mkdir()
        (copilot / "mcp-config.json").write_text(json.dumps({"mcpServers": {"secret-mcp": {
            "headers": {"Authorization": "Bearer test-token-SENTINEL-005"},
            "type": "http", "url": "https://example.invalid/mcp", "tools": ["*"]}}}, indent=2),
            encoding="utf-8")
        oc = self.tmp / ".config" / "opencode"
        oc.mkdir(parents=True)
        (oc / "opencode.json").write_text(json.dumps({"mcp": {"secret-mcp": {
            "type": "remote", "url": "https://example.invalid/mcp",
            "headers": {"Authorization": "Bearer test-token-SENTINEL-006"},
            "enabled": True}}}, indent=2), encoding="utf-8")
        ocl = self.tmp / ".openclaw"
        (ocl / "skills").mkdir(parents=True)
        (ocl / "openclaw.json").write_text(json.dumps({"plugins": {"entries": {"demo-plugin": {
            "enabled": True, "config": {"apiKey": "SENTINEL-007"}}}}}, indent=2),
            encoding="utf-8")
        (self.proj / ".mcp.json").write_text(json.dumps({"mcpServers": {"secret-mcp": {
            "headers": {"Authorization": "Bearer test-token-SENTINEL-008"},
            "url": "https://example.invalid/mcp"}}}, indent=2), encoding="utf-8")
        # a worst-case claude CLI: echoes its last argument (the config for add-json)
        plugin_cli.runner = self._echo_run

    def _echo_run(self, cmd: list[str], **kw):
        res = self._fake_run(cmd, **kw)
        res.stderr = f"Error: invalid MCP config: {cmd[-1]}"
        return res

    # ------------------------------------------------------------------ helpers
    TARGETS = (("mcp", "secret-mcp"), ("mcp", "local-mcp"),
               ("mcp", "secret-mcp", "--harness", "codex"),
               ("mcp", "secret-mcp", "--harness", "copilot"),
               ("mcp", "secret-mcp", "--harness", "opencode"),
               ("plugin", "demo-plugin", "--harness", "openclaw"))

    @property
    def project_target(self) -> tuple[str, ...]:
        return ("mcp", "secret-mcp", "--project", str(self.proj))

    def go(self, *argv: str) -> int:
        rc, out, err = self.run_cli(*argv)
        self.seen.append((argv, out + err))
        if "--json" in argv:
            json.loads(out)                          # still exactly one document
        return rc

    def each(self, verb: str, *extra: str) -> None:
        for t in (*self.TARGETS, self.project_target):
            self.go(verb, *t, *extra)

    def assert_clean(self) -> None:
        for argv, text in self.seen:
            self.assertNotIn(SENTINEL, text, f"leaked by: {' '.join(argv)}\n{text}")
        files = [fs.log_file(), fs.state_file(),
                 *(fs.profiles_dir().glob("*.json") if fs.profiles_dir().is_dir() else ())]
        for f in files:
            if f.is_file():
                self.assertNotIn(SENTINEL, f.read_text(encoding="utf-8"), f)

    def assert_backups_private(self) -> None:
        backups = list(fs.backup_dir().glob("*.json"))
        self.assertTrue(any(SENTINEL in b.read_text(encoding="utf-8") for b in backups),
                        "fixture never reached a backup")
        if POSIX:
            for b in backups:
                self.assertEqual(stat.S_IMODE(b.stat().st_mode), 0o600, b)
            self.assertEqual(stat.S_IMODE(fs.backup_dir().stat().st_mode) & 0o077, 0)

    def batch(self, name: str):
        return mock.patch.object(store, "BATCH", name)

    def read_only(self, *extra: str) -> None:
        for argv in (("status",), ("list",), ("list", "mcp"), ("list", "--project", str(self.proj)),
                     ("cost",), ("cost", "--type", "mcp"), ("doctor",), ("profile", "list")):
            self.go(*argv, *extra)

    # -------------------------------------------------------------------- tests
    def test_every_command_path_shows_no_backed_up_value(self) -> None:
        legacy = self.tmp / ".claude-toggle"
        (legacy / "backups").mkdir(parents=True)
        (legacy / "backups" / "old.json").write_text(
            json.dumps({"env": {"TOKEN": "SENTINEL-009"}}), encoding="utf-8")
        (legacy / "state.json").write_text(json.dumps({"disabled": {"mcp:old-mcp": {
            "type": "mcp", "name": "old-mcp", "backup": str(legacy / "backups" / "old.json")}}}),
            encoding="utf-8")
        p = ("--project", str(self.proj))
        for i, extra in enumerate(VARIANTS):
            self.read_only(*extra)
            self.each("disable", "--dry-run", *extra)
            with self.batch(f"c{i}-disable"):
                self.each("disable", *extra)
            self.assertEqual(len(store.load_state(write_back=False)["disabled"]), 7, extra)
            self.assert_backups_private()
            self.read_only(*extra)
            self.go("profile", "save", f"u{i}", *extra)
            self.go("profile", "save", f"p{i}", *p, *extra)
            for argv in (("profile", "diff", f"u{i}"), ("profile", "diff", f"p{i}", *p),
                         ("profile", "apply", f"u{i}", "--dry-run"),
                         ("undo", "--dry-run"), ("enable", "--all", "--dry-run"),
                         ("enable", "--all", "--dry-run", *p)):
                self.go(*argv, *extra)
            self.each("enable", "--dry-run", *extra)
            with self.batch(f"c{i}-undo"):
                self.go("undo", *extra)                       # restores the disable batch
            with self.batch(f"c{i}-apply"):
                self.go("profile", "apply", f"u{i}", *extra)  # parks it all again
                self.go("profile", "apply", f"p{i}", *p, *extra)
            with self.batch(f"c{i}-enable"):
                self.each("enable", *extra)
            with self.batch(f"c{i}-disable2"):
                self.each("disable", *extra)
            with self.batch(f"c{i}-all"):
                self.go("enable", "--all", *extra)
            self.assertEqual(store.load_state(write_back=False)["disabled"], {}, extra)
        for extra in VARIANTS:                       # imports a legacy mcp backup once
            self.go("migrate", *extra)
        self.assertIn("claude:mcp:old-mcp", store.load_state(write_back=False)["disabled"])
        self.assert_backups_private()
        self.read_only("-v")
        self.go("enable", "--all", "--dry-run", "-v")
        self.go("enable", "--all", "--json", "-v")
        self.ui_dry_run()
        self.assert_clean()

    def ui_dry_run(self) -> None:
        try:
            from agent_toggle.ui import picker as ui
        except ImportError:
            from agent_toggle.ui import menu as ui

        def stage_all(state, table, plugins=True, color=False):
            rows = [r for r in model.collect(state, table, plugins=plugins)
                    if r.type in ("mcp", "plugin")]
            for r in rows:
                r.staged = not r.staged
            return rows

        with mock.patch.object(ui, "pick", stage_all):
            for extra in ((), ("-v",)):
                self.go("ui", "--dry-run", *extra)

    def test_cli_error_echoing_the_config_is_redacted(self) -> None:
        self.each("disable")
        self.cli_rc = 1                              # add-json fails and echoes its config
        for extra in VARIANTS:
            for t in self.TARGETS[:2]:
                self.go("enable", *t, *extra)
        self.assertIn("invalid MCP config", self.seen[-1][1])      # still says why
        rows = [json.loads(ln) for ln in fs.log_file().read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(r["action"] == "enable" and r["result"] == "error" for r in rows))
        self.cli_rc = 0
        self.go("enable", "--all")
        self.cli_rc = 1                              # remove fails and echoes too
        for extra in VARIANTS:
            self.go("disable", "mcp", "secret-mcp", *extra)
        self.assert_clean()

    def test_unexpected_exception_traceback_shows_no_value(self) -> None:
        self.each("disable")

        def boom(cmd, **kw):
            raise RuntimeError("cli crashed")

        plugin_cli.runner = boom
        for extra in (("-v",), ("--json", "-v")):
            for t in self.TARGETS[:2]:
                rc, out, err = self.run_cli("enable", *t, *extra)
                self.seen.append((("enable", *t, *extra), out + err))
                self.assertEqual(rc, 1)
                self.assertIn("Traceback", err)
                self.assertIn("RuntimeError: cli crashed", out + err)
        self.assert_clean()

    def test_project_backup_holds_no_other_servers_secret(self) -> None:
        """G15: disabling server A must not copy server B's header anywhere."""
        token = "Bearer test-token-000"
        (self.proj / ".mcp.json").write_text(json.dumps({"mcpServers": {
            "a-mcp": {"command": "demo"},
            "b-mcp": {"url": "https://example.invalid/mcp",
                      "headers": {"Authorization": token}}}}, indent=2), encoding="utf-8")
        p = ("mcp", "a-mcp", "--project", str(self.proj))
        for i, extra in enumerate(VARIANTS):
            self.go("disable", *p, "--dry-run", *extra)
            with self.batch(f"g{i}"):
                self.go("disable", *p, *extra)
            self.go("enable", *p, "--dry-run", *extra)
            self.read_only(*extra)
            self.go("enable", *p, *extra)
        self.go("disable", *p)                        # leave the backup on disk
        self.assertTrue(list(fs.backup_dir().glob("*__a-mcp.json")))
        for f in fs.state_dir().rglob("*"):
            if f.is_file():
                self.assertNotIn("test-token-000", f.read_text(encoding="utf-8"), f)
        for argv, text in self.seen:
            self.assertNotIn("test-token-000", text, argv)

    def test_corrupt_backups_and_configs_show_no_value(self) -> None:
        self.each("disable")
        for b in fs.backup_dir().glob("*.json"):     # truncate mid-value: parsers must not echo it
            text = b.read_text(encoding="utf-8")
            b.write_text(text[:text.index(SENTINEL) + len(SENTINEL)], encoding="utf-8")
        for extra in VARIANTS:
            self.each("enable", "--dry-run", *extra)
            self.each("enable", *extra)
            self.go("cost", *extra)
            self.go("doctor", *extra)
        (self.tmp / ".codex" / "config.toml").write_text(
            '[mcp_servers.x]\nTOKEN = "SENTINEL-010\n', encoding="utf-8")
        (self.tmp / ".copilot" / "mcp-config.json").write_text(
            '{"mcpServers": {"x": {"t": "SENTINEL-011"', encoding="utf-8")
        (self.tmp / ".config" / "opencode" / "opencode.json").write_text(
            '{"mcp": {"secret-mcp": {"h": "SENTINEL-012\\q", "enabled": true}}}', encoding="utf-8")
        (self.tmp / ".openclaw" / "openclaw.json").write_text(
            '{"plugins": {"entries": {"demo-plugin": {"k": "SENTINEL-013", "enabled": true,}}}}',
            encoding="utf-8")
        (self.proj / ".mcp.json").write_text('{"mcpServers": {"secret-mcp": "SENTINEL-014\x01"}}',
                                             encoding="utf-8")
        (self.tmp / ".claude.json").write_text('{"mcpServers": {"secret-mcp": "SENTINEL-015"',
                                               encoding="utf-8")
        for extra in VARIANTS:
            self.read_only(*extra)
            self.each("disable", "--dry-run", *extra)
            self.each("disable", *extra)
        self.assert_clean()


if __name__ == "__main__":
    unittest.main()
