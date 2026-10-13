"""Park / restore of dir-type resources, and the harness/backend gates."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import unittest
from pathlib import Path

from base import CAN_SYMLINK, SandboxCase
from test_cli_surface import CliCase

from agent_toggle import fs
from agent_toggle import mechanisms as mech


class DirTypeTest(SandboxCase):
    def test_nested_command_roundtrip(self) -> None:
        """`orch:batch` lives at commands/orch/batch.md and must come back there."""
        self.write("commands/orch/batch.md", "batch")
        self.write("commands/other/batch.md", "other")
        state = {"version": 2, "disabled": {}}

        mech.toggle_dir_type("disable", "command", ["orch:batch"], state, "claude", self.home)
        # nested command parked with its parent dir
        self.assertTrue(self.user_parked("commands", "orch", "batch.md").is_file())
        # sibling with same basename untouched
        self.assertEqual((self.home / "commands/other/batch.md").read_text(encoding="utf-8"), "other")
        # origin recorded
        origin = state["disabled"]["claude:command:orch:batch"]["origin"]
        self.assertEqual(Path(origin).parts[-3:], ("commands", "orch", "batch.md"))

        mech.toggle_dir_type("enable", "command", ["orch:batch"], state, "claude", self.home)
        # nested command restored to its original path
        self.assertEqual((self.home / "commands/orch/batch.md").read_text(encoding="utf-8"), "batch")
        # state entry cleared
        self.assertFalse(state["disabled"])
        # empty park subdir pruned
        self.assertFalse(self.user_parked("commands", "orch").exists())
        # park root kept; nothing was ever parked inside the harness home
        self.assertTrue(self.user_parked("commands").is_dir())
        self.assertEqual(sorted(p.name for p in self.home.iterdir()),
                         ["agents", "commands", "scripts", "skills"])

    def test_resolve_probes_shapes(self) -> None:
        self.write("skills/dir-skill/SKILL.md")
        self.write("agents/file-agent.md")
        # resolves a directory skill
        self.assertTrue(mech.resolve_item(self.home / "skills", "dir-skill").is_dir())
        # resolves an .md agent without naming the suffix
        self.assertEqual(mech.resolve_item(self.home / "agents", "file-agent").suffix, ".md")
        # missing item resolves to None
        self.assertIsNone(mech.resolve_item(self.home / "agents", "nope"))

    @unittest.skipUnless(CAN_SYMLINK, "cannot create symlinks here")
    def test_symlink_disable_roundtrip(self) -> None:
        live = self.home / "skills"
        link = live / "broken-symlink"
        link.symlink_to(self.home / "nonexistent-target")
        state = {"version": 2, "disabled": {}}
        fails = mech.toggle_dir_type("disable", "skill", ["broken-symlink"], state,
                                     "claude", self.home)
        # broken symlink disabled without error
        self.assertEqual(fails, 0)
        # broken symlink moved to parked
        self.assertTrue(self.user_parked("skills", "broken-symlink").is_symlink())
        # broken symlink gone from live
        self.assertFalse(link.exists() or link.is_symlink())
        fails_enable = mech.toggle_dir_type("enable", "skill", ["broken-symlink"], state,
                                            "claude", self.home)
        # broken symlink restored without error
        self.assertEqual(fails_enable, 0)
        # broken symlink back in live
        self.assertTrue(link.is_symlink())


class OpencodeDuplicateSkillTest(CliCase):
    """OpenCode scans ~/.claude/skills and ~/.agents/skills too (DESIGN s11 q2): disabling one
    copy of a name must warn that another scanned copy is still loaded, never refuse."""

    def setUp(self) -> None:
        super().setUp()
        self.oc = self.tmp / ".config" / "opencode"
        (self.oc / "skills").mkdir(parents=True)       # opencode installed: its compat dirs count
        self.agents = self.tmp / ".agents" / "skills"

    def put(self, base: Path, name: str = "dup-skill") -> Path:
        p = base / name / "SKILL.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
        return p

    def test_duplicate_in_second_scanned_dir_warns_and_still_disables(self) -> None:
        mine = self.put(self.oc / "skills")
        other = self.put(self.agents)
        rc, env = self.run_json("disable", "skill", "dup-skill", "--harness", "opencode")
        self.assertEqual((rc, env["ok"]), (0, True))
        self.assertFalse(mine.exists())                       # the targeted copy is parked
        self.assertTrue(other.exists())                       # the other one is left alone
        self.assertEqual(len(env["warnings"]), 1)
        self.assertIn("still loads another copy", env["warnings"][0])
        self.assertIn(str(self.agents / "dup-skill"), env["warnings"][0])
        self.assertTrue(env["warnings"][0].endswith(
            f"{self.agents / 'dup-skill'} -- it stays active until that copy is moved away"))
        self.assertNotIn("disable again", env["warnings"][0])
        self.assertNotIn(str(self.oc / "skills" / "dup-skill"), env["warnings"][0])

    def raw_state(self) -> dict:
        return json.loads(fs.state_file().read_text(encoding="utf-8"))

    def test_name_in_one_dir_only_does_not_warn(self) -> None:
        self.put(self.oc / "skills")
        self.put(self.agents, "other-skill")
        rc, env = self.run_json("disable", "skill", "dup-skill", "--harness", "opencode")
        self.assertEqual((rc, env["warnings"]), (0, []))

    def test_same_dir_reached_twice_does_not_warn(self) -> None:
        self.put(self.tmp / ".claude" / "skills")
        (self.oc / "opencode.json").write_text(              # also a compat dir: reached twice
            '{"skills": {"paths": ["~/.claude/skills"]}}', encoding="utf-8")
        rc, env = self.run_json("disable", "skill", "dup-skill", "--harness", "opencode")
        self.assertEqual((rc, env["warnings"]), (0, []))

    @unittest.skipUnless(CAN_SYMLINK, "cannot create symlinks here")
    def test_dir_reached_twice_through_a_symlink_warns_once(self) -> None:
        mine = self.put(self.oc / "skills")
        other = self.put(self.oc / "skill")
        self.agents.parent.mkdir(parents=True)
        self.agents.symlink_to(self.oc / "skill", target_is_directory=True)  # same real dir
        rc, env = self.run_json("disable", "skill", "dup-skill", "--harness", "opencode")
        self.assertEqual(rc, 0)
        self.assertFalse(mine.exists())
        self.assertEqual(len(env["warnings"]), 1)
        self.assertEqual(env["warnings"][0].count(str(other.parent)), 1)

    def test_second_disable_of_a_same_owner_copy_is_refused(self) -> None:
        """skills/ and skill/ share the owner, hence one state key: a second disable would
        overwrite the first record and strand the first parked copy."""
        first = self.put(self.oc / "skills")
        second = self.put(self.oc / "skill")
        self.assertEqual(self.run_json("disable", "skill", "dup-skill", "--harness",
                                       "opencode")[0], 0)
        key = "opencode:skill:dup-skill"
        recorded = self.raw_state()["disabled"][key]
        rc, env = self.run_json("disable", "skill", "dup-skill", "--harness", "opencode")
        self.assertEqual(rc, 1)                               # same exit as any refusal
        row = env["results"][0]
        self.assertEqual(row["status"], "error")
        self.assertIn(recorded["parked_at"], row["detail"])
        self.assertIn("enable it first", row["detail"])
        self.assertTrue(second.exists())                      # nothing moved
        self.assertEqual(self.raw_state()["disabled"][key], recorded)
        self.assertEqual(self.run_json("enable", "skill", "dup-skill", "--harness",
                                       "opencode")[0], 0)
        self.assertTrue(first.exists())                       # the recorded copy comes back
        self.assertTrue(second.exists())
        self.assertNotIn(key, self.raw_state()["disabled"])

    def test_a_stale_record_does_not_block_a_new_disable(self) -> None:
        """The recorded park was deleted by hand: enable cannot clear it, so disable replaces it."""
        self.put(self.oc / "skills")
        second = self.put(self.oc / "skill")
        self.run_json("disable", "skill", "dup-skill", "--harness", "opencode")
        parked = Path(self.raw_state()["disabled"]["opencode:skill:dup-skill"]["parked_at"])
        shutil.rmtree(parked)
        rc, env = self.run_json("disable", "skill", "dup-skill", "--harness", "opencode")
        self.assertEqual(rc, 0, env)
        self.assertFalse(second.exists())

    def test_dry_run_refuses_a_second_disable_too(self) -> None:
        self.put(self.oc / "skills")
        self.put(self.oc / "skill")
        self.run_json("disable", "skill", "dup-skill", "--harness", "opencode")
        rc, env = self.run_json("disable", "skill", "dup-skill", "--harness", "opencode",
                                "--dry-run")
        self.assertEqual((rc, env["results"][0]["status"]), (1, "error"))

    def test_other_harness_is_unaffected(self) -> None:
        self.put(self.tmp / ".claude" / "skills")
        self.put(self.agents)
        rc, env = self.run_json("disable", "skill", "dup-skill", "--harness", "claude")
        self.assertEqual((rc, env["warnings"]), (0, []))

    def test_human_output_goes_to_stderr(self) -> None:
        self.put(self.oc / "skills")
        self.put(self.agents)
        rc, out, err = self.run_cli("disable", "skill", "dup-skill", "--harness", "opencode")
        self.assertEqual(rc, 0)
        self.assertIn("still loads another copy", err)
        self.assertNotIn("still loads another copy", out)


class PluginScopeTest(SandboxCase):
    """`claude plugin disable|enable` without `-s` picks the scope from the cwd: a
    project-scope plugin enabled from elsewhere got a new user-scope `true` while its
    project stayed `false`. The install registry names the scope and project."""

    ID = "demo-plugin@demo-mkt"

    def setUp(self) -> None:
        super().setUp()
        self.cli_rc = 0
        (self.tmp / "work" / "repo").mkdir(parents=True)
        (self.tmp / "work" / "other").mkdir(parents=True)
        # resolved: os.getcwd() is, and macOS /var is a symlink
        self.proj = (self.tmp / "work" / "repo").resolve()
        self.elsewhere = (self.tmp / "work" / "other").resolve()
        self.state = {"version": 3, "disabled": {}}

    def installs(self, *rows: dict) -> None:
        reg = self.home / "plugins" / "installed_plugins.json"
        reg.parent.mkdir(parents=True, exist_ok=True)
        reg.write_text(json.dumps({"version": 2, "plugins": {self.ID: list(rows)}}),
                       encoding="utf-8")

    def toggle(self, action: str, cwd: Path) -> int:
        old = os.getcwd()
        os.chdir(cwd)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                return mech.toggle_plugin(action, [self.ID], self.state, "claude")
        finally:
            os.chdir(old)

    def calls(self) -> list[tuple[list[str], str | None]]:
        return [(a, c) for a, c in self.cli_calls if a[:2] != ["plugin", "list"]]

    def test_project_scope_round_trips_from_any_cwd(self) -> None:
        self.installs({"scope": "project", "projectPath": str(self.proj)})
        self.assertEqual(self.toggle("disable", self.elsewhere), 0)
        self.assertEqual(self.toggle("enable", self.elsewhere), 0)
        want = (str(self.proj),)
        self.assertEqual(self.calls(), [
            (["plugin", "disable", self.ID, "-s", "project"], *want),
            (["plugin", "enable", self.ID, "-s", "project"], *want)])

    def test_enable_replays_the_recorded_scope_not_todays_registry(self) -> None:
        self.installs({"scope": "local", "projectPath": str(self.proj)})
        self.toggle("disable", self.proj)
        self.installs({"scope": "user"})                 # registry changed meanwhile
        self.toggle("enable", self.elsewhere)
        self.assertEqual(self.calls()[-1],
                         (["plugin", "enable", self.ID, "-s", "local"], str(self.proj)))

    def test_user_scope_passes_the_scope_and_no_cwd(self) -> None:
        self.installs({"scope": "user"})
        self.toggle("disable", self.proj)
        self.assertEqual(self.calls(), [(["plugin", "disable", self.ID, "-s", "user"], None)])

    def test_ambiguous_install_keeps_auto_detect_and_replays_its_cwd(self) -> None:
        self.installs({"scope": "user"}, {"scope": "project", "projectPath": str(self.proj)})
        self.toggle("disable", self.proj)
        self.toggle("enable", self.elsewhere)
        self.assertEqual(self.calls(), [
            (["plugin", "disable", self.ID], str(self.proj)),
            (["plugin", "enable", self.ID], str(self.proj))])

    def test_entry_from_before_scope_tracking_resolves_from_the_registry(self) -> None:
        self.installs({"scope": "project", "projectPath": str(self.proj)})
        self.state["disabled"][f"claude:plugin:{self.ID}"] = {
            "mechanism": "native_cli", "harness": "claude", "type": "plugin",
            "name": self.ID, "native": True, "at": "2026-01-01T00:00:00+0000"}
        self.toggle("enable", self.elsewhere)
        self.assertEqual(self.calls(), [
            (["plugin", "enable", self.ID, "-s", "project"], str(self.proj))])

    def test_no_registry_and_a_gone_project_fall_back_to_auto_detect(self) -> None:
        self.toggle("disable", self.elsewhere)           # no installed_plugins.json
        self.installs({"scope": "project", "projectPath": str(self.tmp / "gone")})
        self.toggle("enable", self.elsewhere)
        self.assertEqual(self.calls(), [
            (["plugin", "disable", self.ID], str(self.elsewhere)),
            (["plugin", "enable", self.ID], str(self.elsewhere))])

    def test_tampered_scope_or_cwd_is_refused(self) -> None:
        for bad in ({"scope": "global"}, {"cwd": "relative/dir"}, {"cwd": 7}):
            with self.subTest(bad=bad):
                entry = {"mechanism": "native_cli", "harness": "claude", "type": "plugin",
                         "name": self.ID, "native": True, "at": "2026", **bad}
                self.assertTrue(mech._entry_reason(entry, mech.harnesses(),
                                                   f"claude:plugin:{self.ID}", ()))


class McpGateTest(SandboxCase):
    def test_mcp_without_backend_fails_loudly(self) -> None:
        state = {"version": 2, "disabled": {}}
        fails = mech.toggle_mcp("disable", ["x"], state, "openclaw", self.home, None)
        # sqlite-backed harness reports failure instead of no-op
        self.assertEqual(fails, 1)
        # no state written for the refused server
        self.assertFalse(state["disabled"])
