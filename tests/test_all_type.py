"""`disable|enable all <name>...`: every type on the harness that has an item of that name."""
from __future__ import annotations

import json

from test_cli_surface import CliCase

from agent_toggle import fs, mechanisms


class AllTypeTest(CliCase):
    def setUp(self) -> None:
        super().setUp()
        self.cli_rc = 0
        self.write("skills/dup/SKILL.md")
        self.write("commands/dup.md")
        self.write("agents/other.md")           # a different name: never touched

    def state(self) -> dict:
        return json.loads(fs.state_file().read_text(encoding="utf-8"))["disabled"]

    def test_disable_all_parks_every_type_with_that_name(self) -> None:
        rc, env = self.run_json("disable", "all", "dup")
        self.assertEqual(rc, 0)
        self.assertEqual(sorted((r["type"], r["status"]) for r in env["results"]),
                         [("command", "ok"), ("skill", "ok")])
        self.assertEqual(sorted(self.state()), ["claude:command:dup", "claude:skill:dup"])
        self.assertTrue((self.home / "agents" / "other.md").exists())

    def test_enable_all_restores_every_parked_type(self) -> None:
        self.assertEqual(self.run_cli("disable", "all", "dup")[0], 0)
        rc, _ = self.run_json("enable", "all", "dup")
        self.assertEqual(rc, 0)
        self.assertEqual(self.state(), {})
        self.assertTrue((self.home / "skills" / "dup" / "SKILL.md").exists())
        self.assertTrue((self.home / "commands" / "dup.md").exists())

    def test_undo_reverses_the_whole_all_batch(self) -> None:
        self.assertEqual(self.run_cli("disable", "all", "dup")[0], 0)
        self.assertEqual(self.run_cli("undo")[0], 0)
        self.assertEqual(self.state(), {})

    def test_mcp_with_the_same_name_is_included(self) -> None:
        self.claude_json({"mcpServers": {"dup": {"command": "dup-server"}}})
        saved = mechanisms.claude_mcp_config
        self.addCleanup(setattr, mechanisms, "claude_mcp_config", saved)
        mechanisms.claude_mcp_config = lambda name: ({"command": "dup-server"}, "user", None)
        rc, env = self.run_json("disable", "all", "dup")
        self.assertEqual(rc, 0)
        self.assertIn(("mcp", "ok"), [(r["type"], r["status"]) for r in env["results"]])

    def test_a_name_with_no_match_is_an_error_row(self) -> None:
        rc, env = self.run_json("disable", "all", "ghost")
        self.assertEqual(rc, 1)
        self.assertEqual([(r["type"], r["name"], r["status"]) for r in env["results"]],
                         [("all", "ghost", "error")])

    def test_dry_run_changes_nothing(self) -> None:
        rc, env = self.run_json("disable", "all", "dup", "--dry-run")
        self.assertEqual(rc, 0)
        self.assertEqual(len(env["results"]), 2)
        self.assertTrue((self.home / "skills" / "dup").exists())
        self.assertFalse(fs.state_file().exists())

    def test_project_scope(self) -> None:
        proj = self.tmp / "repo"
        for rel in (".claude/skills/dup/SKILL.md", ".claude/commands/dup.md"):
            (proj / rel).parent.mkdir(parents=True, exist_ok=True)
            (proj / rel).write_text("x", encoding="utf-8")
        rc, env = self.run_json("disable", "all", "dup", "--project", str(proj))
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(r["type"] for r in env["results"]), ["command", "skill"])
        self.assertFalse((proj / ".claude" / "skills" / "dup").exists())
        self.assertTrue((self.home / "skills" / "dup").exists())     # user scope untouched
