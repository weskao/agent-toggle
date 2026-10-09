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
