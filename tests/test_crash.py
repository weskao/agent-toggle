"""A killed run is recoverable (G13) and a cross-filesystem park is crash-safe (G12).

A SIGKILL skips every `finally`, so each crash below raises Killed (a BaseException
no handler catches) at one step AND drops apply_plan's final save: only what was
saved before the step survives, exactly as after a real kill.
"""
from __future__ import annotations

import contextlib
import errno
import json
import os
import shlex
import shutil
import stat
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from test_conformance import FIXTURES
from test_project import ProjectCase

from agent_toggle import fs, store
from agent_toggle.backends import flag_json


class Killed(BaseException):
    """The process dies here."""


EXDEV = OSError(errno.EXDEV, "Invalid cross-device link")


def file_bytes(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


class CrashCase(ProjectCase):
    @contextlib.contextmanager
    def killed(self, target: str, **patch):
        """Run the block with `target` patched; it must die there, and its final save
        (apply_plan's `finally`) never happens."""
        with mock.patch("agent_toggle.ops.save_state"), mock.patch(target, **patch), \
                self.assertRaises(Killed):
            yield

    def raw_state(self) -> dict:
        return json.loads(fs.state_file().read_text(encoding="utf-8"))

    def pending(self) -> dict:
        return self.raw_state().get("pending", {}) if fs.state_file().exists() else {}

    def warnings(self, *argv: str) -> list[str]:
        rc, env = self.run_json(*argv)
        return env["warnings"]


class FlagCrashTest(CrashCase):
    """openclaw plugin = one `enabled` token in openclaw.json."""

    KEY = "openclaw:plugin:demo-plugin"
    ARGS = ("plugin", "demo-plugin", "--harness", "openclaw")

    def setUp(self) -> None:
        super().setUp()
        shutil.copytree(FIXTURES / "openclaw", self.tmp, dirs_exist_ok=True)
        self.file = self.tmp / ".openclaw" / "openclaw.json"
        self.before = self.file.read_bytes()
        self.real_set = flag_json.set_flag

    def write_then_die(self, *a):
        self.real_set(*a)
        raise Killed

    def flag(self) -> bool:
        return flag_json.read_flag(self.file, ("plugins", "entries", "demo-plugin", "enabled"))

    def test_kill_before_the_flag_write_rolls_back(self) -> None:
        with self.killed("agent_toggle.mechanisms.set_flag", side_effect=Killed):
            self.run_cli("disable", *self.ARGS)
        self.assertEqual(self.pending()[self.KEY]["entry"]["flag"]["was"], True)
        self.assertTrue(any("never reached disk" in w for w in self.warnings("status")))
        rc, env = self.run_json("disable", *self.ARGS)       # the next change settles it first
        self.assertEqual(rc, 0, env)
        self.assertFalse(self.flag())
        self.assertNotIn("pending", self.raw_state())
        self.assertIn(self.KEY, self.raw_state()["disabled"])

    def test_kill_after_the_flag_write_is_restorable_with_the_reported_command(self) -> None:
        with self.killed("agent_toggle.mechanisms.set_flag", side_effect=self.write_then_die):
            self.run_cli("disable", *self.ARGS)
        self.assertFalse(self.flag())                          # the old G13: false, no entry
        self.assertEqual(self.raw_state()["disabled"], {})
        warning = next(w for w in self.warnings("status") if "finished on disk" in w)
        cmd = warning.rsplit("`", 2)[-2].split()
        self.assertEqual(cmd, ["agent-toggle", "enable", *self.ARGS])
        rc, env = self.run_json(*cmd[1:])
        self.assertEqual(rc, 0, env)
        self.assertTrue(any("recovered interrupted disable" in w for w in env["warnings"]))
        self.assertEqual(self.file.read_bytes(), self.before)  # restored from the recorded `was`
        self.assertEqual(self.raw_state()["disabled"], {})
        self.assertNotIn("pending", self.raw_state())

    def test_kill_after_the_enable_write_drops_the_entry(self) -> None:
        self.assertEqual(self.run_cli("disable", *self.ARGS)[0], 0)
        with self.killed("agent_toggle.mechanisms.set_flag", side_effect=self.write_then_die):
            self.run_cli("enable", *self.ARGS)
        self.assertIn(self.KEY, self.raw_state()["disabled"])   # not saved: the kill
        rc, rows = self.run_json("doctor")
        self.assertTrue(any(r.get("pending") == "done" for r in rows["results"]), rows)
        self.assertEqual(self.run_cli("status")[0], 0)
        self.assertEqual(self.run_cli("enable", *self.ARGS)[0], 1)  # recovered, then nothing left
        self.assertEqual(self.raw_state()["disabled"], {})
        self.assertEqual(self.file.read_bytes(), self.before)

    def test_an_unreadable_flag_file_is_reported_never_guessed(self) -> None:
        with self.killed("agent_toggle.mechanisms.set_flag", side_effect=self.write_then_die):
            self.run_cli("disable", *self.ARGS)
        self.file.write_text("{broken", encoding="utf-8")
        rc, env = self.run_json("doctor")
        stuck = [r for r in env["results"] if r.get("pending") == "stuck"]
        self.assertEqual(len(stuck), 1, env)
        self.assertIn("is true in", stuck[0]["detail"])         # the recorded prior value
        self.run_cli("disable", "plugin", "other", "--harness", "openclaw")
        self.assertIn(self.KEY, self.pending())                 # kept until it can be settled
        self.assertEqual(self.file.read_text(encoding="utf-8"), "{broken")

    def test_enable_all_after_a_killed_flag_write_restores_it(self) -> None:
        with self.killed("agent_toggle.mechanisms.set_flag", side_effect=self.write_then_die):
            self.run_cli("disable", *self.ARGS)
        rc, env = self.run_json("enable", "--all")
        self.assertEqual(rc, 0, env)
        self.assertEqual(self.file.read_bytes(), self.before)
        self.assertEqual(self.raw_state(), {"version": 3, "disabled": {}})

    @unittest.skipIf(os.name == "nt", "lock liveness is POSIX-only (fs._holder_alive)")
    def test_status_does_not_call_a_live_runs_op_interrupted(self) -> None:
        with self.killed("agent_toggle.mechanisms.set_flag", side_effect=self.write_then_die):
            self.run_cli("disable", *self.ARGS)
        fs.lock_file().write_text(str(os.getpid()), encoding="utf-8")   # a live holder
        self.addCleanup(fs.lock_file().unlink, missing_ok=True)
        warnings = self.warnings("status")
        self.assertTrue(any("in progress" in w for w in warnings), warnings)
        self.assertFalse(any("interrupted" in w for w in warnings), warnings)


class MoveCrashTest(CrashCase):
    """Project skill park: <proj>/.claude/skills/demo-skill -> parked/<sha8>/skills-disabled."""

    def setUp(self) -> None:
        super().setUp()
        self.files = file_bytes(self.pskill)
        self.dest = self.parked("skills-disabled", "demo-skill")
        self.tmpcopy, self.trash = fs.move_leftovers(self.pskill, self.dest)

    def cross_fs(self):
        """Force the copy path: the plain rename reports another filesystem."""
        return mock.patch("agent_toggle.fs.os.rename", side_effect=EXDEV)

    def assert_live(self) -> None:
        self.assertEqual(file_bytes(self.pskill), self.files)
        self.assertFalse(self.dest.exists())
        self.assertFalse(self.tmpcopy.exists())

    def assert_parked(self) -> None:
        self.assertFalse(self.pskill.exists())
        self.assertEqual(file_bytes(self.dest), self.files)
        self.assertFalse(self.trash.exists())

    def test_cross_fs_round_trip_is_byte_identical_and_prunes_the_park(self) -> None:
        with self.cross_fs():
            self.assertEqual(self.p("disable", "skill", "demo-skill")[0], 0)
            self.assert_parked()
            self.assertNotIn("pending", self.raw_state())
            self.assertEqual(self.p("enable", "skill", "demo-skill")[0], 0)
        self.assert_live()
        self.assertFalse((fs.parked_dir() / self.digest).exists())   # G12: nothing left behind

    def test_prune_keeps_a_park_that_still_holds_an_item(self) -> None:
        self.assertEqual(self.p("disable", "skill", "demo-skill")[0], 0)
        self.assertEqual(self.p("disable", "agent", "demo-agent")[0], 0)
        self.assertEqual(self.p("enable", "skill", "demo-skill")[0], 0)
        self.assertFalse(self.parked("skills-disabled").exists())
        self.assertTrue(self.parked("agents-disabled", "demo-agent.md").is_file())
        self.assertTrue(fs.state_dir().is_dir())

    def test_a_copy_failure_cleans_up_and_keeps_the_source(self) -> None:
        def disk_full(src, dst, **kw):
            Path(dst).mkdir()
            raise OSError(errno.ENOSPC, "No space left on device")
        with self.cross_fs(), mock.patch("agent_toggle.fs.shutil.copytree", side_effect=disk_full):
            rc, env = self.p("disable", "skill", "demo-skill")
        self.assertEqual(rc, 1, env)
        self.assert_live()
        self.assertNotIn("pending", self.raw_state())
        self.assertEqual(self.raw_state()["disabled"], {})

    def test_a_source_that_cannot_be_moved_aside_rolls_the_copy_back(self) -> None:
        real = fs.replace
        def refuse_trash(src, dst):
            if Path(dst).name == self.trash.name:
                raise PermissionError(errno.EACCES, "Permission denied")
            real(src, dst)
        with self.cross_fs(), mock.patch("agent_toggle.fs.replace", side_effect=refuse_trash):
            rc, env = self.p("disable", "skill", "demo-skill")
        self.assertEqual(rc, 1, env)
        self.assert_live()                                       # one copy, the source
        self.assertNotIn("pending", self.raw_state())

    def test_a_filesystem_refusing_dir_fsync_still_parks(self) -> None:
        real = os.fsync
        def no_dir_fsync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError(errno.EINVAL, "Invalid argument")     # some FUSE / SMB mounts
            real(fd)
        with self.cross_fs(), mock.patch("agent_toggle.fs.os.fsync", side_effect=no_dir_fsync):
            self.assertEqual(self.p("disable", "skill", "demo-skill")[0], 0)
        self.assert_parked()

    def test_kill_before_the_move_rolls_back(self) -> None:
        with self.killed("agent_toggle.fs.os.rename", side_effect=Killed):
            self.p("disable", "skill", "demo-skill")
        self.assertIn(self.key, self.pending())
        self.assertEqual(self.p("disable", "skill", "demo-skill")[0], 0)
        self.assert_parked()
        self.assertNotIn("pending", self.raw_state())

    def test_kill_mid_copy_leaves_the_source_and_a_half_copy_the_next_run_removes(self) -> None:
        def half_copy(src, dst, **kw):
            Path(dst).mkdir()
            (Path(dst) / "SKILL.md").write_text("half", encoding="utf-8")
            raise Killed
        with self.cross_fs(), mock.patch("agent_toggle.fs.remove_leftover"), \
                self.killed("agent_toggle.fs.shutil.copytree", side_effect=half_copy):
            self.p("disable", "skill", "demo-skill")             # (no cleanup runs on a kill)
        self.assertTrue(self.tmpcopy.is_dir())
        self.assertEqual(file_bytes(self.pskill), self.files)
        self.assertTrue(any("never reached disk" in w for w in self.warnings("status")))
        self.assertEqual(self.p("enable", "skill", "demo-skill")[0], 1)   # settles, nothing parked
        self.assert_live()
        self.assertNotIn("pending", self.raw_state())

    def test_kill_after_the_rename_into_place_reports_both_copies(self) -> None:
        real, dead = fs.replace, []
        def die_on_trash(src, dst):
            if dead or Path(dst).name == self.trash.name:
                dead.append(dst)                                 # dead: no later call runs
                raise Killed
            real(src, dst)
        with self.cross_fs(), mock.patch("agent_toggle.fs.remove_leftover"), \
                self.killed("agent_toggle.fs.replace", side_effect=die_on_trash):
            self.p("disable", "skill", "demo-skill")
        self.assertEqual(file_bytes(self.dest), self.files)     # complete
        self.assertEqual(file_bytes(self.pskill), self.files)   # intact
        rc, env = self.run_json("doctor")
        stuck = [r for r in env["results"] if r.get("pending") == "stuck"]
        self.assertEqual(len(stuck), 1, env)
        self.assertIn(f"rm -rf {shlex.quote(str(self.pskill.resolve()))}", stuck[0]["detail"])
        self.p("disable", "agent", "demo-agent")                 # an unrelated change
        self.assertIn(self.key, self.pending())                  # never resolved by deleting
        self.assertEqual(file_bytes(self.pskill), self.files)
        shutil.rmtree(self.pskill)                               # the reported fix
        self.assertEqual(self.p("enable", "skill", "demo-skill")[0], 0)
        self.assert_live()
        self.assertNotIn("pending", self.raw_state())

    def test_kill_before_the_trash_is_deleted_completes_the_park(self) -> None:
        real = shutil.rmtree
        def die_on_trash(path, *a, **kw):
            if Path(path).name == self.trash.name:
                raise Killed
            real(path, *a, **kw)
        with self.cross_fs(), self.killed("agent_toggle.fs.shutil.rmtree",
                                          side_effect=die_on_trash):
            self.p("disable", "skill", "demo-skill")
        self.assertTrue(self.trash.is_dir())
        self.assertEqual(self.p("disable", "agent", "demo-agent")[0], 0)
        self.assert_parked()
        self.assertEqual(self.raw_state()["disabled"][self.key]["parked_at"], str(self.dest))
        self.assertEqual(self.p("enable", "skill", "demo-skill")[0], 0)
        self.assert_live()

    def test_kill_mid_batch_keeps_the_items_already_done(self) -> None:
        real = os.rename
        def die_on_agent(src, dst):
            if Path(src).name == "demo-agent.md":
                raise Killed
            real(src, dst)
        with self.killed("agent_toggle.fs.os.rename", side_effect=die_on_agent):
            self.run_json("enable", "--all")                     # nothing to enable yet
            self.p("disable", "skill", "demo-skill")
            self.p("disable", "agent", "demo-agent")
        self.assertIn(self.key, self.raw_state()["disabled"])    # saved by the next write-ahead
        self.assertEqual(list(self.pending()), [self.key.replace("skill:demo-skill",
                                                                 "agent:demo-agent")])

    def test_kill_mid_enable_restores_on_the_next_run(self) -> None:
        self.assertEqual(self.p("disable", "skill", "demo-skill")[0], 0)
        real = os.rename
        def move_then_die(src, dst):
            real(src, dst)
            raise Killed
        with self.killed("agent_toggle.fs.os.rename", side_effect=move_then_die):
            self.p("enable", "skill", "demo-skill")
        self.assertIn(self.key, self.raw_state()["disabled"])
        self.assertEqual(self.p("disable", "agent", "demo-agent")[0], 0)
        self.assert_live()
        self.assertNotIn(self.key, self.raw_state()["disabled"])
        self.assertFalse(self.parked("skills-disabled").exists())

    def test_a_tampered_pending_entry_is_refused_and_touches_nothing(self) -> None:
        victim = self.tmp / "victim"
        victim.mkdir()
        (victim / "keep.txt").write_text("keep", encoding="utf-8")
        fs.private_dir(fs.state_dir())
        entry = {"mechanism": "move", "harness": "claude", "type": "skill", "name": "x",
                 "parked_at": str(victim), "origin": str(self.home / "skills" / "x"),
                 "companions": []}
        store.save_state({"version": 3, "disabled": {},
                          "pending": {"claude:skill:x": {"action": "enable", "entry": entry}}})
        rc, env = self.run_json("disable", "skill", "demo-skill")
        self.assertTrue(any("refused" in w for w in env["warnings"]), env)
        self.assertTrue((victim / "keep.txt").is_file())
        self.assertIn("claude:skill:x", self.pending())


class CompanionCrashTest(CrashCase):
    def setUp(self) -> None:
        super().setUp()
        self.helper = self.write("scripts/only-mine.sh", "#!/bin/sh\n")
        self.write("skills/tool/SKILL.md", "run $HOME/.claude/scripts/only-mine.sh\n")
        self.ukey = "claude:skill:tool"

    def test_kill_while_a_companion_moves_keeps_its_record(self) -> None:
        from agent_toggle import companions
        real = companions.move
        def move_then_die(src, dest, dry_run=False):
            target = real(src, dest, dry_run)
            if not dry_run and Path(src).name == "only-mine.sh":
                raise Killed
            return target
        with self.killed("agent_toggle.companions.move", side_effect=move_then_die):
            self.run_cli("disable", "skill", "tool")
        self.assertFalse(self.helper.exists())
        self.assertEqual(self.run_cli("disable", "skill", "nothing-here")[0], 1)   # settles
        comps = self.raw_state()["disabled"][self.ukey]["companions"]
        self.assertEqual([Path(c["from"]).name for c in comps], ["only-mine.sh"])
        self.assertEqual(self.run_cli("enable", "skill", "tool")[0], 0)
        self.assertTrue(self.helper.is_file())                  # restored with the skill

    def test_a_pending_companion_from_another_key_is_refused(self) -> None:
        other = fs.companion_dir() / "claude_skill_other" / "scripts" / "x.sh"
        other.parent.mkdir(parents=True)
        other.write_text("x", encoding="utf-8")
        entry = {"mechanism": "move", "harness": "claude", "type": "skill", "name": "tool",
                 "parked_at": str(self.user_parked("skills", "tool")),
                 "origin": str(self.home / "skills" / "tool"),
                 "companions": [{"from": str(self.home / "scripts" / "x.sh"), "to": str(other)}]}
        store.save_state({"version": 3, "disabled": {self.ukey: entry},
                          "pending": {self.ukey: {"action": "enable", "entry": entry}}})
        rc, env = self.run_json("disable", "skill", "nothing-here")
        self.assertTrue(any("refused: a companion outside" in w for w in env["warnings"]), env)
        self.assertTrue(other.is_file())
        self.assertFalse((self.home / "scripts" / "x.sh").exists())


class JsonMcpCrashTest(CrashCase):
    """User-scope strict-JSON MCP file (backend `json`): copilot mcp-config.json."""

    HARNESS = "copilot"
    FILE = (".copilot", "mcp-config.json")

    def setUp(self) -> None:
        super().setUp()
        shutil.copytree(FIXTURES / self.HARNESS, self.tmp, dirs_exist_ok=True)
        self.cfg = self.tmp.joinpath(*self.FILE)
        self.before = self.cfg.read_bytes()
        self.args = ("mcp", "example-mcp", "--harness", self.HARNESS)
        self.ghost = ("mcp", "ghost-mcp", "--harness", self.HARNESS)    # fails; settles first
        self.mkey = f"{self.HARNESS}:mcp:example-mcp"
        self.bp = fs.backup_dir() / f"{self.HARNESS}__example-mcp.json"

    def kill_in_write(self, action: str, *, write: bool) -> None:
        """Die in the config write: before it (backup + pending saved) or right after it."""
        from agent_toggle.backends import mcp_json
        real = mcp_json.write_project_mcp

        def die(*a, **kw):
            if write:
                real(*a, **kw)
            raise Killed
        with self.killed("agent_toggle.mechanisms.write_project_mcp", side_effect=die):
            self.run_cli(action, *self.args)

    def servers(self) -> dict:
        return json.loads(self.cfg.read_text(encoding="utf-8"))["mcpServers"]

    def pending_rows(self, verdict: str) -> list[dict]:
        rc, env = self.run_json("doctor")
        return [r for r in env["results"] if r.get("pending") == verdict]

    def assert_settled(self, disabled: bool, body: bytes) -> None:
        state = self.raw_state()
        self.assertNotIn("pending", state)
        self.assertEqual(self.mkey in state["disabled"], disabled)
        self.assertEqual(self.cfg.read_bytes(), body)            # whole bytes, never a mix

    def forge(self, action: str, **fields) -> dict:
        """A pending entry as a killed disable would leave it, with `fields` tampered."""
        entry = {"mechanism": "remove_backup", "harness": self.HARNESS, "type": "mcp",
                 "name": "example-mcp", "backend": "json", "backup": str(self.bp),
                 "scope": "user", "project": None, "at": "2026-01-01T00:00:00+0000", **fields}
        fs.private_dir(fs.state_dir())
        store.save_state({"version": 3, "disabled": {},
                          "pending": {self.mkey: {"action": action, "entry": entry}}})
        return entry

    # -- disable ---------------------------------------------------------------------
    def test_kill_before_the_disable_write_rolls_back_and_drops_the_backup(self) -> None:
        self.kill_in_write("disable", write=False)
        self.assertEqual(self.pending()[self.mkey]["entry"]["backup"], str(self.bp))
        self.assertTrue(self.bp.exists())
        self.assertEqual(self.raw_state()["disabled"], {})
        self.assertTrue(any("never reached disk" in w for w in self.warnings("status")))
        rc, env = self.run_json("doctor")
        self.assertFalse([r for r in env["results"] if r.get("name") == self.bp.name], env)
        self.assertEqual(self.run_json("disable", *self.ghost)[0], 1)
        self.assert_settled(False, self.before)
        self.assertFalse(self.bp.exists())

    def test_an_unsaved_begin_drops_the_backup_it_just_wrote(self) -> None:
        enospc = OSError(errno.ENOSPC, "No space left on device")
        with mock.patch("agent_toggle.mechanisms.begin", side_effect=enospc):
            rc, env = self.run_json("disable", *self.args)
        self.assertEqual(rc, 1)
        self.assertIn("state not saved, nothing changed", env["results"][0]["detail"])
        self.assertFalse(self.bp.exists())
        self.assertEqual(self.cfg.read_bytes(), self.before)

    def test_an_unsaved_begin_keeps_a_backup_an_older_entry_still_uses(self) -> None:
        from agent_toggle import mechanisms as mech
        state = {"version": 3, "disabled": {self.mkey: {}}}      # a falsy entry skips the
        enospc = OSError(errno.ENOSPC, "No space left on device")   # "already disabled" refusal
        with mock.patch("agent_toggle.mechanisms.begin", side_effect=enospc):
            fails = mech.toggle_json_mcp("disable", ["example-mcp"], state, self.HARNESS,
                                         mech.Result(), False, mech.harnesses())
        self.assertEqual(fails, 1)
        self.assertTrue(self.bp.exists())                        # the older entry restores from it
        self.assertEqual(self.cfg.read_bytes(), self.before)

    def test_settle_keeps_the_backup_of_an_already_recorded_disable(self) -> None:
        """A pending disable that never reached the file, over a key that already has a
        recorded entry, must not delete the backup that entry restores from."""
        self.assertEqual(self.run_json("disable", *self.args)[0], 0)
        state = self.raw_state()
        entry = state["disabled"][self.mkey]
        self.cfg.write_bytes(self.before)                        # the server is back in the file
        store.save_state({**state, "pending": {self.mkey: {"action": "disable",
                                                           "entry": entry}}})
        self.assertTrue(self.bp.exists())
        self.run_json("disable", *self.ghost)                    # fails; settles first
        self.assertTrue(self.bp.exists())
        self.assertNotIn("pending", self.raw_state())
        self.assertEqual(self.raw_state()["disabled"][self.mkey], entry)

    def test_kill_after_the_disable_write_is_recorded_and_restorable(self) -> None:
        self.kill_in_write("disable", write=True)
        self.assertNotIn("example-mcp", self.servers())
        self.assertEqual(self.raw_state()["disabled"], {})      # the old gap: edited, unrecorded
        warning = next(w for w in self.warnings("status") if "finished on disk" in w)
        cmd = warning.rsplit("`", 2)[-2].split()
        self.assertEqual(cmd, ["agent-toggle", "enable", *self.args])
        rc, env = self.run_json(*cmd[1:])
        self.assertEqual(rc, 0, env)
        self.assertTrue(any("recovered interrupted disable" in w for w in env["warnings"]))
        self.assert_settled(False, self.before)

    def test_a_rolled_back_write_settles_inline(self) -> None:
        def boom(expected=None):
            def verify(before, after):
                raise ValueError("boom")
            return verify
        with mock.patch.object(fs, "json_verify", boom):
            rc, env = self.run_json("disable", *self.args)
        self.assertEqual(rc, 1)
        self.assertIn("rolled back", env["results"][0]["detail"])
        self.assert_settled(False, self.before)
        self.assertFalse(self.bp.exists())
        self.assertEqual(self.run_json("disable", *self.args)[0], 0)
        self.assertNotIn("pending", self.raw_state())            # a clean op ends its record
        after = self.cfg.read_bytes()
        with mock.patch.object(fs, "json_verify", boom):
            self.assertEqual(self.run_json("enable", *self.args)[0], 1)
        self.assert_settled(True, after)
        self.assertTrue(self.bp.exists())

    def test_enable_all_settles_a_killed_disable_then_restores_it(self) -> None:
        self.kill_in_write("disable", write=True)
        rc, env = self.run_json("enable", "--all")
        self.assertEqual(rc, 0, env)
        self.assert_settled(False, self.before)

    # -- enable ----------------------------------------------------------------------
    def test_kill_before_the_enable_write_keeps_it_disabled(self) -> None:
        self.assertEqual(self.run_json("disable", *self.args)[0], 0)
        after = self.cfg.read_bytes()
        self.kill_in_write("enable", write=False)
        self.assertEqual(len(self.pending_rows("undone")), 1)
        self.assertEqual(self.run_json("disable", *self.ghost)[0], 1)
        self.assert_settled(True, after)
        self.assertTrue(self.bp.exists())                        # still the way back
        self.assertEqual(self.run_json("enable", *self.args)[0], 0)
        self.assert_settled(False, self.before)

    def test_kill_after_the_enable_write_drops_the_entry(self) -> None:
        self.assertEqual(self.run_json("disable", *self.args)[0], 0)
        self.kill_in_write("enable", write=True)
        self.assertIn(self.mkey, self.raw_state()["disabled"])  # not saved: the kill
        self.assertEqual(len(self.pending_rows("done")), 1)
        self.assertEqual(self.run_json("enable", *self.args)[0], 1)   # recovered, nothing left
        self.assert_settled(False, self.before)

    def test_an_old_whole_file_backup_settles_and_restores(self) -> None:
        self.assertEqual(self.run_json("disable", *self.args)[0], 0)
        after = self.cfg.read_bytes()
        entry = json.loads(self.bp.read_text(encoding="utf-8"))["json"]["entry"]
        self.bp.write_text(json.dumps({"json": {"entry": entry, "before": self.before.decode(),
                                                "after": after.decode()}}), encoding="utf-8")
        self.kill_in_write("enable", write=False)
        self.assertEqual(self.run_json("disable", *self.ghost)[0], 1)
        self.assert_settled(True, after)
        self.kill_in_write("enable", write=True)
        self.assertEqual(self.run_json("disable", *self.ghost)[0], 1)
        self.assert_settled(False, self.before)

    # -- never guessed -----------------------------------------------------------------
    def test_an_unreadable_config_is_reported_never_guessed(self) -> None:
        self.kill_in_write("disable", write=True)
        self.cfg.write_text("{broken", encoding="utf-8")
        stuck = self.pending_rows("stuck")
        self.assertEqual(len(stuck), 1, stuck)
        self.assertIn("not strict JSON", stuck[0]["detail"])
        self.run_json("disable", *self.ghost)
        self.assertIn(self.mkey, self.pending())                 # kept until it can be settled
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), "{broken")
        self.assertTrue(self.bp.exists())

    def test_a_gone_server_without_its_backup_is_stuck(self) -> None:
        self.kill_in_write("disable", write=True)
        for label, body in (("missing", None), ("enable would refuse it", '{"json": {"entry": {}}}')):
            with self.subTest(label):
                self.bp.unlink(missing_ok=True)
                if body:
                    self.bp.write_text(body, encoding="utf-8")
                stuck = self.pending_rows("stuck")
                self.assertEqual(len(stuck), 1, stuck)
                self.assertIn(str(self.bp), stuck[0]["detail"])
                self.run_json("disable", *self.ghost)
                self.assertIn(self.mkey, self.pending())
                self.assertEqual(self.raw_state()["disabled"], {})

    # -- untrusted state ---------------------------------------------------------------
    def test_a_forged_backup_path_is_refused_and_touches_nothing(self) -> None:
        self.assertEqual(self.run_json("disable", "mcp", "other-mcp",
                                       "--harness", self.HARNESS)[0], 0)
        other = fs.backup_dir() / f"{self.HARNESS}__other-mcp.json"
        body = self.cfg.read_bytes()
        elsewhere = self.tmp / "elsewhere" / self.bp.name
        for backup, needle in ((other, "backup is not the one declared"),
                               (elsewhere, "backup outside")):
            with self.subTest(backup=backup):
                state = self.raw_state()
                state["pending"] = {self.mkey: {"action": "disable", "entry": {
                    **state["disabled"][f"{self.HARNESS}:mcp:other-mcp"],
                    "name": "example-mcp", "backup": str(backup)}}}
                fs.state_file().write_text(json.dumps(state), encoding="utf-8")
                rc, env = self.run_json("disable", *self.ghost)
                self.assertIn(f"refused: {needle}", " ".join(env["warnings"]))
                self.assertTrue(other.exists())
                self.assertEqual(self.cfg.read_bytes(), body)
                self.assertIn(self.mkey, self.pending())

    def test_a_tampered_entry_is_refused_untouched(self) -> None:
        for label, fields, needle in (
                ("harness", {"harness": "agy" if self.HARNESS == "copilot" else "copilot"},
                 "does not match its key"),
                ("backend", {"backend": "toml"}, "does not use the toml backend"),
                ("project", {"project": str(self.tmp / "work")}, "refused"),
                ("no backup", {"backup": None}, "missing backup")):
            with self.subTest(label):
                self.forge("disable", **fields)
                self.bp.parent.mkdir(parents=True, exist_ok=True)
                self.bp.write_text("{}", encoding="utf-8")
                rc, env = self.run_json("disable", *self.ghost)
                self.assertIn(needle, " ".join(env["warnings"]), env)
                self.assertTrue(self.bp.exists())
                self.assertEqual(self.cfg.read_bytes(), self.before)
                self.assertIn(self.mkey, self.pending())

    def test_a_json_entry_under_a_non_json_harness_key_is_refused(self) -> None:
        bp = fs.backup_dir() / "claude__x.json"
        fs.private_dir(fs.backup_dir())
        bp.write_text("{}", encoding="utf-8")
        entry = {"mechanism": "remove_backup", "harness": "claude", "type": "mcp", "name": "x",
                 "backend": "json", "backup": str(bp), "scope": "user", "project": None}
        store.save_state({"version": 3, "disabled": {},
                          "pending": {"claude:mcp:x": {"action": "disable", "entry": entry}}})
        rc, env = self.run_json("disable", *self.ghost)
        self.assertIn("refused", " ".join(env["warnings"]), env)
        self.assertTrue(bp.exists())
        self.assertIn("claude:mcp:x", self.pending())
        self.assertEqual(self.cli_calls, [])

    def test_the_config_path_comes_from_the_table_never_the_entry(self) -> None:
        victim = self.tmp / "victim.json"
        victim.write_text('{"mcpServers": {}}', encoding="utf-8")
        self.forge("disable", file=str(victim))
        self.assertEqual(self.run_json("disable", *self.ghost)[0], 1)
        self.assertEqual(victim.read_text(encoding="utf-8"), '{"mcpServers": {}}')
        self.assert_settled(False, self.before)    # the table's file still holds the server


class AgyJsonMcpCrashTest(JsonMcpCrashTest):
    """Same, for agy's ~/.gemini/config/mcp_config.json."""

    HARNESS = "agy"
    FILE = (".gemini", "config", "mcp_config.json")
    test_a_json_entry_under_a_non_json_harness_key_is_refused = None    # harness-independent


NEXT = ("disable", "skill", "nope", "--harness", "claude")     # any change: settles first


class CliCrashCase(CrashCase):
    """A stub claude (never the real one): `plugin enable|disable|list --json` act on
    `self.plugins`; `mcp remove|add-json` edit the temp HOME's ~/.claude.json, at the
    scope given (local: under projects/<cwd>). `self.listing` breaks the list read."""

    def setUp(self) -> None:
        super().setUp()
        self.plugins = {"demo@mkt": True}
        self.listing = "ok"                       # ok | timeout | garbage | fail
        self.broken = False                       # every mutating call fails, changing nothing
        self.addCleanup(self._restore_env, "AGENT_TOGGLE_CLI_TIMEOUT",
                        os.environ.pop("AGENT_TOGGLE_CLI_TIMEOUT", None))
        self.timeouts: list[float] = []
        self.cj = self.tmp / ".claude.json"
        self.cj.write_text(json.dumps({"mcpServers": {"demo-mcp": {"command": "demo"}},
                                       "projects": {}}), encoding="utf-8")

    def _fake_run(self, cmd: list[str], **kw) -> subprocess.CompletedProcess:
        args, rc, text = cmd[1:], 1, ""
        self.cli_calls.append((args, kw.get("cwd")))
        if self.broken:
            text = "boom"
        elif args[:2] == ["plugin", "list"]:
            self.timeouts.append(kw["timeout"])
            if self.listing == "timeout":
                raise subprocess.TimeoutExpired(cmd, kw["timeout"])
            rc = int(self.listing == "fail")
            text = "<html>oops" if self.listing == "garbage" else json.dumps(
                [{"id": i, "enabled": on} for i, on in self.plugins.items()])
        elif args[0] == "plugin" and args[2] in self.plugins:
            self.plugins[args[2]], rc = args[1] == "enable", 0
        elif args[0] == "mcp":
            cfg = json.loads(self.cj.read_text(encoding="utf-8"))
            remove = args[1] == "remove"
            scope, name = (args[4], args[2]) if remove else (args[3], args[4])
            box = cfg if scope == "user" else cfg["projects"].setdefault(kw["cwd"], {})
            servers = box.setdefault("mcpServers", {})
            if remove and name in servers:
                del servers[name]
                rc = 0
            elif not remove:
                servers[name], rc = json.loads(args[5]), 0
            self.cj.write_text(json.dumps(cfg), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, rc, stdout=text, stderr="")

    def kill_in_cli(self, *argv: str, landed: bool) -> None:
        """Die in the mutating CLI call: before it runs, or right after it landed."""
        from agent_toggle.backends import plugin_cli
        real = plugin_cli.run_cli

        def die(binary, args, **kw):
            if landed:
                real(binary, args, **kw)
            raise Killed
        with self.killed("agent_toggle.mechanisms.run_cli", side_effect=die):
            self.run_cli(*argv)

    def doctor_rows(self, verdict: str) -> list[dict]:
        rc, env = self.run_json("doctor")
        return [r for r in env["results"] if r.get("pending") == verdict]

    def forge(self, key: str, action: str, entry: dict) -> None:
        fs.private_dir(fs.state_dir())
        store.save_state({"version": 3, "disabled": {} if action == "disable" else {key: entry},
                          "pending": {key: {"action": action, "entry": entry}}})


class PluginCrashTest(CliCrashCase):
    """claude plugins: the CLI is the op, so settle asks `claude plugin list --json`."""

    KEY = "claude:plugin:demo@mkt"
    ARGS = ("plugin", "demo@mkt", "--harness", "claude")

    def entry(self, **fields) -> dict:
        return {"mechanism": "native_cli", "harness": "claude", "type": "plugin",
                "name": "demo@mkt", "native": True, "at": "2026-01-01T00:00:00+0000", **fields}

    def assert_settled(self, disabled: bool) -> None:
        self.assertNotIn("pending", self.raw_state())
        self.assertEqual(self.KEY in self.raw_state()["disabled"], disabled)
        self.assertEqual(self.plugins["demo@mkt"], not disabled)

    def test_kill_after_the_cli_disable_is_recorded_and_restorable(self) -> None:
        self.kill_in_cli("disable", *self.ARGS, landed=True)
        self.assertEqual(self.pending()[self.KEY]["entry"]["mechanism"], "native_cli")
        self.assertEqual(self.raw_state()["disabled"], {})      # the old gap: done, unrecorded
        warning = next(w for w in self.warnings("status") if "finished on disk" in w)
        self.assertIn(30, self.timeouts)                          # the read timeout, not 120 s
        cmd = warning.rsplit("`", 2)[-2].split()
        self.assertEqual(cmd, ["agent-toggle", "enable", *self.ARGS])
        rc, env = self.run_json(*cmd[1:])
        self.assertEqual(rc, 0, env)
        self.assertTrue(any("recovered interrupted disable" in w for w in env["warnings"]))
        self.assert_settled(False)

    def test_a_project_scope_plugin_is_settled_from_its_project(self) -> None:
        """The listing's `enabled` is the state seen from its cwd: ask from the project."""
        proj = self.tmp / "work" / "repo"
        proj.mkdir(parents=True)
        reg = self.home / "plugins" / "installed_plugins.json"
        reg.parent.mkdir(parents=True)
        reg.write_text(json.dumps({"version": 2, "plugins": {"demo@mkt": [
            {"scope": "project", "projectPath": str(proj)}]}}), encoding="utf-8")
        self.kill_in_cli("disable", *self.ARGS, landed=True)
        self.run_json(*NEXT)
        self.assert_settled(True)
        lists = [cwd for c, cwd in self.cli_calls if c[:2] == ["plugin", "list"]]
        self.assertEqual(lists, [str(proj)])

    def test_kill_before_the_cli_disable_rolls_back(self) -> None:
        self.kill_in_cli("disable", *self.ARGS, landed=False)
        self.assertIn(self.KEY, self.pending())
        self.assertTrue(any("never reached disk" in w for w in self.warnings("status")))
        self.run_json(*NEXT)
        self.assert_settled(False)

    def test_kill_after_the_cli_enable_drops_the_entry(self) -> None:
        self.assertEqual(self.run_json("disable", *self.ARGS)[0], 0)
        self.assertNotIn("pending", self.raw_state())             # a clean op ends its record
        self.kill_in_cli("enable", *self.ARGS, landed=True)
        self.assertIn(self.KEY, self.raw_state()["disabled"])    # not saved: the kill
        self.assertEqual(len(self.doctor_rows("done")), 1)
        self.run_json(*NEXT)
        self.assert_settled(False)

    def test_kill_before_the_cli_enable_keeps_it_disabled(self) -> None:
        self.assertEqual(self.run_json("disable", *self.ARGS)[0], 0)
        self.kill_in_cli("enable", *self.ARGS, landed=False)
        self.assertEqual(len(self.doctor_rows("undone")), 1)
        self.run_json(*NEXT)
        self.assert_settled(True)
        self.assertEqual(self.run_json("enable", *self.ARGS)[0], 0)
        self.assert_settled(False)

    def test_a_failed_cli_call_settles_inline(self) -> None:
        rc, env = self.run_json("disable", "plugin", "ghost@mkt", "--harness", "claude")
        self.assertEqual(rc, 1)
        self.assertNotIn("pending", self.raw_state())
        self.assertEqual(self.raw_state()["disabled"], {})

    def test_a_refused_disable_of_an_already_disabled_plugin_is_not_recorded(self) -> None:
        self.plugins["demo@mkt"] = False                 # disabled by hand, outside the tool
        self.broken = True                               # the CLI refuses: nothing changes
        self.assertEqual(self.run_json("disable", *self.ARGS)[0], 1)
        self.assertNotIn("pending", self.raw_state())
        self.assertEqual(self.raw_state()["disabled"], {})
        self.assertEqual([c for c, _ in self.cli_calls if c[:2] == ["plugin", "list"]], [])

    def test_a_timed_out_cli_call_asks_the_listing(self) -> None:
        from agent_toggle.backends import plugin_cli
        real = plugin_cli.run_cli

        def slow(binary, args, **kw):
            real(binary, args, **kw)                     # landed, then the wait ran out
            return False, "CLI timed out after 120s (raise it with AGENT_TOGGLE_CLI_TIMEOUT=<s>)"
        with mock.patch("agent_toggle.mechanisms.run_cli", side_effect=slow):
            self.assertEqual(self.run_json("disable", *self.ARGS)[0], 1)
        self.assert_settled(True)

    def test_an_unanswerable_listing_is_stuck_never_guessed(self) -> None:
        from agent_toggle.backends import plugin_cli
        for label, needle in (("timeout", "AGENT_TOGGLE_CLI_TIMEOUT"),
                              ("garbage", "could not read"), ("fail", "could not read"),
                              ("no cli", "claude CLI not found")):
            with self.subTest(label):
                self.setUp()
                self.kill_in_cli("disable", *self.ARGS, landed=True)
                self.listing = label
                if label == "no cli":
                    plugin_cli.which = lambda name: None
                stuck = self.doctor_rows("stuck")
                self.assertEqual(len(stuck), 1, stuck)
                self.assertIn(needle, stuck[0]["detail"])
                self.cli_calls.clear()
                self.run_json(*NEXT)
                self.assertIn(self.KEY, self.pending())             # kept until it can be settled
                self.assertEqual(self.raw_state()["disabled"], {})
                self.assertFalse(self.plugins["demo@mkt"])
                self.assertEqual([c for c, _ in self.cli_calls if c[:2] != ["plugin", "list"]], [])

    def test_a_plugin_gone_from_the_listing(self) -> None:
        self.kill_in_cli("disable", *self.ARGS, landed=True)
        del self.plugins["demo@mkt"]                     # uninstalled: a disable is moot
        self.run_json(*NEXT)
        self.assertNotIn("pending", self.raw_state())
        self.assertEqual(self.raw_state()["disabled"], {})
        self.forge(self.KEY, "enable", self.entry())     # nor an enable: its record stays
        self.run_json(*NEXT)
        self.assertNotIn("pending", self.raw_state())
        self.assertIn(self.KEY, self.raw_state()["disabled"])
        rc, env = self.run_json("enable", *self.ARGS)     # a plain failed enable: no leftover
        self.assertEqual(rc, 1)
        self.assertNotIn("pending", self.raw_state())
        self.assertIn(self.KEY, self.raw_state()["disabled"])

    def test_a_tampered_entry_is_refused_untouched(self) -> None:
        for label, fields, needle in (
                ("harness", {"harness": "codex"}, "does not match its key"),
                ("type", {"type": "mcp"}, "does not match its key"),
                ("mechanism", {"mechanism": "flag"}, "flag"),
                ("backup", {"backup": str(self.tmp / "x.json")}, "refused")):
            with self.subTest(label):
                self.forge(self.KEY, "disable", self.entry(**fields))
                self.cli_calls.clear()
                rc, env = self.run_json(*NEXT)
                self.assertIn(needle, " ".join(env["warnings"]), env)
                self.assertIn(self.KEY, self.pending())
                self.assertEqual(self.cli_calls, [])
                self.assertTrue(self.plugins["demo@mkt"])
        bad = "claude:plugin:demo&calc"                  # would reach cmd.exe on Windows
        self.forge(bad, "disable", self.entry(name="demo&calc"))
        rc, env = self.run_json(*NEXT)
        self.assertIn("refused", " ".join(env["warnings"]), env)
        self.assertIn(bad, self.pending())
        self.assertEqual(self.cli_calls, [])


class ClaudeMcpCrashTest(CliCrashCase):
    """claude MCP servers: `claude mcp remove|add-json` write ~/.claude.json, so settle
    reads that file strictly (no CLI call); unreadable -> stuck."""

    KEY = "claude:mcp:demo-mcp"
    ARGS = ("mcp", "demo-mcp", "--harness", "claude")

    def setUp(self) -> None:
        super().setUp()
        self.bp = fs.backup_dir() / "claude__demo-mcp.json"

    def servers(self) -> dict:
        return json.loads(self.cj.read_text(encoding="utf-8"))["mcpServers"]

    def assert_settled(self, disabled: bool) -> None:
        self.assertNotIn("pending", self.raw_state())
        self.assertEqual(self.KEY in self.raw_state()["disabled"], disabled)
        self.assertEqual("demo-mcp" in self.servers(), not disabled)

    def entry(self, **fields) -> dict:
        return {"mechanism": "remove_backup", "harness": "claude", "type": "mcp",
                "name": "demo-mcp", "backend": "claude-json", "backup": str(self.bp),
                "scope": "user", "project": None, "at": "2026-01-01T00:00:00+0000", **fields}

    def test_kill_after_the_cli_remove_is_recorded_and_restorable(self) -> None:
        self.kill_in_cli("disable", *self.ARGS, landed=True)
        self.assertEqual(self.pending()[self.KEY]["entry"]["backend"], "claude-json")
        self.assertNotIn("demo-mcp", self.servers())
        self.assertEqual(self.raw_state()["disabled"], {})
        self.cli_calls.clear()
        warning = next(w for w in self.warnings("status") if "finished on disk" in w)
        self.assertEqual(self.cli_calls, [])                      # the file says, not the CLI
        cmd = warning.rsplit("`", 2)[-2].split()
        self.assertEqual(cmd, ["agent-toggle", "enable", *self.ARGS])
        rc, env = self.run_json(*cmd[1:])
        self.assertEqual(rc, 0, env)
        self.assertTrue(any("recovered interrupted disable" in w for w in env["warnings"]))
        self.assert_settled(False)
        self.assertEqual(self.servers()["demo-mcp"], {"command": "demo"})

    def test_kill_before_the_cli_remove_rolls_back_and_drops_the_backup(self) -> None:
        self.kill_in_cli("disable", *self.ARGS, landed=False)
        self.assertTrue(self.bp.exists())
        self.assertTrue(any("never reached disk" in w for w in self.warnings("status")))
        self.run_json(*NEXT)
        self.assert_settled(False)
        self.assertFalse(self.bp.exists())

    def test_kill_after_the_cli_add_drops_the_entry(self) -> None:
        self.assertEqual(self.run_json("disable", *self.ARGS)[0], 0)
        self.assertNotIn("pending", self.raw_state())
        self.kill_in_cli("enable", *self.ARGS, landed=True)
        self.assertEqual(len(self.doctor_rows("done")), 1)
        self.run_json(*NEXT)
        self.assert_settled(False)

    def test_kill_before_the_cli_add_keeps_it_disabled(self) -> None:
        self.assertEqual(self.run_json("disable", *self.ARGS)[0], 0)
        self.kill_in_cli("enable", *self.ARGS, landed=False)
        self.assertEqual(len(self.doctor_rows("undone")), 1)
        self.run_json(*NEXT)
        self.assert_settled(True)
        self.assertTrue(self.bp.exists())

    def test_settle_needs_no_cli(self) -> None:
        from agent_toggle.backends import plugin_cli
        self.kill_in_cli("disable", *self.ARGS, landed=True)
        plugin_cli.which = lambda name: None
        self.run_json(*NEXT)
        self.assert_settled(True)

    def test_a_failed_cli_remove_settles_inline(self) -> None:
        self.broken = True
        rc, env = self.run_json("disable", *self.ARGS)
        self.assertEqual(rc, 1)
        self.assert_settled(False)
        self.assertFalse(self.bp.exists())

    def test_an_unreadable_claude_json_is_stuck_never_guessed(self) -> None:
        self.kill_in_cli("disable", *self.ARGS, landed=True)
        for body in ("{broken", "[]", '{"mcpServers": []}'):
            with self.subTest(body):
                self.cj.write_text(body, encoding="utf-8")
                stuck = self.doctor_rows("stuck")
                self.assertEqual(len(stuck), 1, stuck)
                self.assertIn(str(self.cj), stuck[0]["detail"])
                self.run_json(*NEXT)
                self.assertIn(self.KEY, self.pending())
                self.assertEqual(self.cj.read_text(encoding="utf-8"), body)
                self.assertTrue(self.bp.exists())

    def test_a_gone_server_without_its_backup_is_stuck(self) -> None:
        self.kill_in_cli("disable", *self.ARGS, landed=True)
        self.bp.unlink()
        stuck = self.doctor_rows("stuck")
        self.assertEqual(len(stuck), 1, stuck)
        self.assertIn(str(self.bp), stuck[0]["detail"])
        self.run_json(*NEXT)
        self.assertIn(self.KEY, self.pending())
        self.assertEqual(self.raw_state()["disabled"], {})

    def test_a_local_scope_server_settles_in_its_project(self) -> None:
        proj = self.tmp / "loc"
        proj.mkdir()
        self.cj.write_text(json.dumps({"mcpServers": {}, "projects": {
            str(proj): {"mcpServers": {"loc-mcp": {"command": "loc"}}}}}), encoding="utf-8")
        self.kill_in_cli("disable", "mcp", "loc-mcp", "--harness", "claude", landed=True)
        entry = self.pending()["claude:mcp:loc-mcp"]["entry"]
        self.assertEqual((entry["scope"], entry["project"]), ("local", str(proj)))
        self.run_json(*NEXT)
        self.assertNotIn("pending", self.raw_state())
        self.assertEqual(self.raw_state()["disabled"]["claude:mcp:loc-mcp"]["scope"], "local")

    def test_a_tampered_entry_is_refused_untouched(self) -> None:
        fs.private_dir(fs.backup_dir())
        self.bp.write_text('{"command": "demo"}', encoding="utf-8")
        for label, fields, needle in (
                ("harness", {"harness": "codex"}, "does not match its key"),
                ("backend", {"backend": "toml"}, "does not use the toml backend"),
                ("scope", {"scope": "project"}, "unknown mcp scope"),
                ("local root", {"scope": "local", "project": "/"}, "not a usable dir"),
                ("backup", {"backup": str(fs.backup_dir() / "claude__other.json")},
                 "backup is not the one declared"),
                ("outside", {"backup": str(self.tmp / "claude__demo-mcp.json")}, "backup outside")):
            with self.subTest(label):
                self.forge(self.KEY, "disable", self.entry(**fields))
                self.cli_calls.clear()
                rc, env = self.run_json(*NEXT)
                self.assertIn(needle, " ".join(env["warnings"]), env)
                self.assertIn(self.KEY, self.pending())
                self.assertTrue(self.bp.exists())
                self.assertIn("demo-mcp", self.servers())
                self.assertEqual(self.cli_calls, [])


class TomlMcpCrashTest(CrashCase):
    """codex config.toml: a direct block edit, settled from the file."""

    KEY = "codex:mcp:example-mcp"
    ARGS = ("mcp", "example-mcp", "--harness", "codex")

    def setUp(self) -> None:
        super().setUp()
        shutil.copytree(FIXTURES / "codex", self.tmp, dirs_exist_ok=True)
        self.cfg = self.tmp / ".codex" / "config.toml"
        self.before = self.cfg.read_bytes()
        self.bp = fs.backup_dir() / "codex__example-mcp.json"

    def kill_in_edit(self, action: str, *, write: bool) -> None:
        from agent_toggle.backends import mcp_toml
        fn = "codex_mcp_remove" if action == "disable" else "codex_mcp_add"
        real = getattr(mcp_toml, fn)

        def die(*a, **kw):
            if action == "disable" and (len(a) > 2 or kw.get("dry_run")):
                return real(*a, **kw)                    # the plan, not the edit
            if write:
                real(*a, **kw)
            raise Killed
        with self.killed(f"agent_toggle.mechanisms.{fn}", side_effect=die):
            self.run_cli(action, *self.ARGS)

    def assert_settled(self, disabled: bool, body: bytes) -> None:
        self.assertNotIn("pending", self.raw_state())
        self.assertEqual(self.KEY in self.raw_state()["disabled"], disabled)
        self.assertEqual(self.cfg.read_bytes(), body)

    def test_kill_after_the_remove_is_recorded_and_restorable(self) -> None:
        self.kill_in_edit("disable", write=True)
        self.assertEqual(self.pending()[self.KEY]["entry"]["backend"], "toml")
        warning = next(w for w in self.warnings("status") if "finished on disk" in w)
        rc, env = self.run_json(*warning.rsplit("`", 2)[-2].split()[1:])
        self.assertEqual(rc, 0, env)
        self.assert_settled(False, self.before)

    def test_kill_before_the_remove_rolls_back_and_drops_the_backup(self) -> None:
        self.kill_in_edit("disable", write=False)
        self.assertTrue(self.bp.exists())
        self.run_json(*NEXT)
        self.assert_settled(False, self.before)
        self.assertFalse(self.bp.exists())

    def test_kill_around_the_enable_edit(self) -> None:
        for write, disabled in ((False, True), (True, False)):
            with self.subTest(write=write):
                self.assertEqual(self.run_json("disable", *self.ARGS)[0], 0)
                after = self.cfg.read_bytes()
                self.kill_in_edit("enable", write=write)
                self.assertEqual(len([r for r in self.run_json("doctor")[1]["results"]
                                      if r.get("pending") == ("done" if write else "undone")]), 1)
                self.run_json(*NEXT)
                self.assert_settled(disabled, after if disabled else self.before)
                if disabled:
                    self.assertEqual(self.run_json("enable", *self.ARGS)[0], 0)

    def test_an_unreadable_config_is_stuck_never_guessed(self) -> None:
        self.kill_in_edit("disable", write=True)
        self.cfg.write_bytes(b"\xff\xfe not utf-8")
        rc, env = self.run_json(*NEXT)
        self.assertTrue(any(str(self.cfg) in w for w in env["warnings"]), env)
        self.assertIn(self.KEY, self.pending())
        self.assertEqual(self.cfg.read_bytes(), b"\xff\xfe not utf-8")
        self.assertTrue(self.bp.exists())


class StateCompatTest(CrashCase):
    def test_an_old_state_file_loads_and_stays_without_pending(self) -> None:
        self.assertEqual(self.p("disable", "skill", "demo-skill")[0], 0)
        old = self.raw_state()
        self.assertEqual(set(old), {"version", "disabled"})      # nothing new when idle
        fs.state_file().write_text(json.dumps(old), encoding="utf-8")
        self.assertEqual(store.load_state()["disabled"], old["disabled"])
        self.assertEqual(self.p("enable", "skill", "demo-skill")[0], 0)
        self.assertEqual(set(self.raw_state()), {"version", "disabled"})

    def test_a_malformed_pending_is_refused_on_load(self) -> None:
        fs.private_dir(fs.state_dir())
        fs.state_file().write_text('{"version": 3, "disabled": {}, "pending": []}',
                                   encoding="utf-8")
        with self.assertRaises(SystemExit):
            store.load_state()
