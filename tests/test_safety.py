"""Storage safety: lock, 0600 writes, state schema v3 migration."""
from __future__ import annotations

import json
import os
import time
import unittest
from pathlib import Path
from unittest import mock

from base import SandboxCase

from agent_toggle import cli, fs, mechanisms, store

FIXTURE = Path(__file__).parent / "fixtures" / "state-v2" / "state.json"
POSIX = os.name != "nt"


def mode(p: Path) -> int:
    return p.stat().st_mode & 0o777


class LockTest(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        saved = fs.LOCK_WAIT
        self.addCleanup(setattr, fs, "LOCK_WAIT", saved)
        fs.LOCK_WAIT = 0.1

    def test_acquire_release(self) -> None:
        with fs.lock():
            self.assertEqual(fs.lock_file().read_text(), str(os.getpid()))
        self.assertFalse(fs.lock_file().exists())

    def test_second_acquire_raises_locked(self) -> None:
        with fs.lock():
            with self.assertRaises(fs.Locked):
                with fs.lock():
                    pass
            # the failed attempt must not have removed the holder's lock
            self.assertTrue(fs.lock_file().exists())

    def test_release_leaves_a_lock_we_no_longer_own(self) -> None:
        with fs.lock():
            fs.lock_file().write_text("999999")      # a rival took it over
        self.assertEqual(fs.lock_file().read_text(), "999999")

    def test_stale_lock_is_taken_over(self) -> None:
        fs.state_dir().mkdir(parents=True)
        fs.lock_file().write_text("999999")
        old = time.time() - 11 * 60
        os.utime(fs.lock_file(), (old, old))
        with fs.lock():
            self.assertEqual(fs.lock_file().read_text(), str(os.getpid()))

    def test_takeover_never_steals_a_lock_a_rival_just_refreshed(self) -> None:
        fs.state_dir().mkdir(parents=True)
        fs.lock_file().write_text("999999")
        old = time.time() - 11 * 60
        os.utime(fs.lock_file(), (old, old))
        real_rename = os.rename

        def rival_wins_the_race(src, dst):
            fs.lock_file().write_text("424242")          # rival replaces the stale lock
            real_rename(src, dst)                        # ...and we move THEIR fresh one

        with mock.patch.object(fs.os, "rename", rival_wins_the_race):
            with self.assertRaises(fs.Locked):
                with fs.lock():
                    self.fail("proceeded despite a live rival lock")
        self.assertEqual(fs.lock_file().read_text(), "424242")     # put back
        self.assertEqual(list(fs.state_dir().glob("lock.stale.*")), [])

    @unittest.skipIf(not POSIX, "PID liveness is POSIX only")
    def test_stale_lock_of_a_live_pid_is_not_taken_over(self) -> None:
        fs.state_dir().mkdir(parents=True)
        fs.lock_file().write_text(str(os.getpid()))      # a running process
        old = time.time() - 11 * 60
        os.utime(fs.lock_file(), (old, old))
        with self.assertRaises(fs.Locked):
            with fs.lock():
                pass
        self.assertEqual(fs.lock_file().read_text(), str(os.getpid()))

    def test_batch_refreshes_the_lock_mtime(self) -> None:
        self.write("skills/demo-skill/SKILL.md")
        with fs.lock():
            old = time.time() - 9 * 60
            os.utime(fs.lock_file(), (old, old))
            mechanisms.toggle_dir_type("disable", "skill", ["demo-skill"],
                                       {"version": 3, "disabled": {}}, "claude", self.home)
            self.assertGreater(fs.lock_file().stat().st_mtime, time.time() - 60)

    def test_refresh_ignores_a_lock_we_do_not_own(self) -> None:
        fs.state_dir().mkdir(parents=True)
        fs.lock_file().write_text("999999")
        old = time.time() - 9 * 60
        os.utime(fs.lock_file(), (old, old))
        fs.refresh_lock()
        self.assertLess(fs.lock_file().stat().st_mtime, time.time() - 60)

    def test_cli_exits_3_when_locked(self) -> None:
        self.write("skills/demo-skill/SKILL.md")
        with fs.lock():
            rc = cli.main(["disable", "skill", "demo-skill"])
        self.assertEqual(rc, 3)
        self.assertTrue((self.home / "skills/demo-skill").is_dir())


@unittest.skipIf(not POSIX, "POSIX modes")
class PrivateModeTest(SandboxCase):
    def test_atomic_write_is_0600(self) -> None:
        p = self.tmp / "out.json"
        fs.atomic_write(p, "{}")
        self.assertEqual(mode(p), 0o600)
        self.assertEqual(p.read_text(), "{}")
        # no tmp file left behind
        self.assertEqual([x.name for x in self.tmp.glob(".out.json*")], [])

    def test_mcp_disable_writes_0600_backup_and_state(self) -> None:
        raw = {"type": "http", "headers": {"Authorization": "Bearer test-token-000"}}
        saved = mechanisms.claude_mcp_config
        self.addCleanup(setattr, mechanisms, "claude_mcp_config", saved)
        mechanisms.claude_mcp_config = lambda name: (raw, "user", None)
        self.cli_rc = 0                      # stubbed `claude mcp remove` succeeds
        state = {"version": 3, "disabled": {}}
        fails = mechanisms.toggle_mcp("disable", ["example-mcp"], state, "claude",
                                      self.home, "claude-json")
        self.assertEqual(fails, 0)
        entry = state["disabled"]["claude:mcp:example-mcp"]
        self.assertEqual(entry["mechanism"], "remove_backup")
        self.assertEqual(mode(Path(entry["backup"])), 0o600)
        store.save_state(state)
        self.assertEqual(mode(fs.state_file()), 0o600)

    def test_load_tightens_loose_files(self) -> None:
        fs.backup_dir().mkdir(parents=True)
        bp = fs.backup_dir() / "claude__example-mcp.json"
        bp.write_text('{"headers": {"Authorization": "Bearer test-token-000"}}')
        store.save_state({"version": 3, "disabled": {}})
        for p in (bp, fs.state_file()):
            p.chmod(0o644)
        store.load_state()
        self.assertEqual(mode(bp), 0o600)
        self.assertEqual(mode(fs.state_file()), 0o600)

    def test_status_warns_on_open_state_dir(self) -> None:
        fs.state_dir().mkdir(parents=True)
        fs.state_dir().chmod(0o755)
        self.assertTrue(fs.too_open(fs.state_dir()))
        fs.state_dir().chmod(0o700)
        self.assertFalse(fs.too_open(fs.state_dir()))


@unittest.skipIf(not POSIX, "POSIX modes")
class PrivateDirTest(SandboxCase):
    def test_state_backup_dirs_and_log_are_private_under_open_umask(self) -> None:
        self.addCleanup(os.umask, os.umask(0))
        raw = {"type": "http", "headers": {"Authorization": "Bearer test-token-000"}}
        saved = mechanisms.claude_mcp_config
        self.addCleanup(setattr, mechanisms, "claude_mcp_config", saved)
        mechanisms.claude_mcp_config = lambda name: (raw, "user", None)
        self.cli_rc = 0
        self.assertEqual(cli.main(["disable", "mcp", "example-mcp"]), 0)
        for d in (fs.state_dir(), fs.backup_dir()):
            self.assertEqual(mode(d), 0o700, d)
        self.assertEqual(mode(fs.log_file()), 0o600)


@unittest.skipIf(not POSIX, "POSIX modes")
class ReadOnlyNeverChmodsTest(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("skills/demo-skill/SKILL.md")
        store.save_state({"version": 3, "disabled": {}})
        fs.backup_dir().mkdir()
        self.bp = fs.backup_dir() / "claude__example-mcp.json"
        self.bp.write_text("{}")
        for p in (fs.state_file(), self.bp):
            p.chmod(0o644)

    def test_list_status_and_dry_run_leave_modes_alone(self) -> None:
        for argv in (["list"], ["status"], ["disable", "skill", "demo-skill", "--dry-run"]):
            cli.main(argv)
            self.assertEqual(mode(fs.state_file()), 0o644, argv)
            self.assertEqual(mode(self.bp), 0o644, argv)

    def test_status_warns_about_loose_files(self) -> None:
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.main(["status"])
        text = buf.getvalue()
        self.assertIn(f"{fs.state_file()} is looser than 0600", text)
        self.assertIn(f"{self.bp} is looser than 0600", text)


class SchemaV3Test(SandboxCase):
    def _install_fixture(self) -> None:
        fs.state_dir().mkdir(parents=True)
        fs.state_file().write_text(FIXTURE.read_text().replace("@ROOT@", str(self.tmp)))

    def test_v2_state_migrates(self) -> None:
        self._install_fixture()
        state = store.load_state()
        self.assertEqual(state["version"], 3)
        got = {k: v["mechanism"] for k, v in state["disabled"].items()}
        self.assertEqual(got, {"claude:skill:demo-skill": "move",
                               "claude:mcp:example-mcp": "remove_backup",
                               "claude:plugin:demo-plugin": "native_cli"})
        # written back, atomically and privately
        self.assertEqual(json.loads(fs.state_file().read_text())["version"], 3)
        if POSIX:
            self.assertEqual(mode(fs.state_file()), 0o600)

    def test_read_only_load_does_not_write(self) -> None:
        self._install_fixture()
        before = fs.state_file().read_text()
        state = store.load_state(write_back=False)
        self.assertEqual(state["version"], 3)
        self.assertEqual(fs.state_file().read_text(), before)

    def test_newer_schema_is_refused(self) -> None:
        fs.state_dir().mkdir(parents=True)
        fs.state_file().write_text('{"version": 4, "disabled": {}}')
        with self.assertRaises(SystemExit):
            store.load_state()
        self.assertEqual(json.loads(fs.state_file().read_text())["version"], 4)

    def test_v2_migration_is_idempotent(self) -> None:
        self._install_fixture()
        first = store.load_state()
        on_disk = fs.state_file().read_text()
        os.utime(fs.state_file(), (1, 1))
        second = store.load_state()
        self.assertEqual(first, second)
        self.assertEqual(fs.state_file().read_text(), on_disk)
        # no rewrite when nothing changed
        self.assertEqual(fs.state_file().stat().st_mtime, 1)

    def test_shared_with_is_preserved(self) -> None:
        self._install_fixture()
        state = store.load_state()
        state["disabled"]["claude:skill:demo-skill"]["shared_with"] = ["opencode"]
        store.save_state(state)
        again = store.load_state()
        self.assertEqual(again["disabled"]["claude:skill:demo-skill"]["shared_with"],
                         ["opencode"])

    def test_new_entries_carry_mechanism(self) -> None:
        self.write("skills/demo-skill/SKILL.md")
        state = {"version": 3, "disabled": {}}
        mechanisms.toggle_dir_type("disable", "skill", ["demo-skill"], state,
                                   "claude", self.home)
        self.assertEqual(state["disabled"]["claude:skill:demo-skill"]["mechanism"], "move")
