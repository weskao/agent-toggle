"""Storage safety: lock, 0600 writes, state schema v3 migration."""
from __future__ import annotations

import contextlib
import io
import json
import os
import time
import unittest
from pathlib import Path
from unittest import mock

from base import SandboxCase

from agent_toggle import cli, fs, mechanisms, store
from agent_toggle.backends import mcp_toml

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
            self.assertEqual(fs.lock_file().read_text(encoding="utf-8"), str(os.getpid()))
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
            fs.lock_file().write_text("999999", encoding="utf-8")      # a rival took it over
        self.assertEqual(fs.lock_file().read_text(encoding="utf-8"), "999999")

    def test_stale_lock_is_taken_over(self) -> None:
        fs.state_dir().mkdir(parents=True)
        fs.lock_file().write_text("999999", encoding="utf-8")
        old = time.time() - 11 * 60
        os.utime(fs.lock_file(), (old, old))
        with fs.lock():
            self.assertEqual(fs.lock_file().read_text(encoding="utf-8"), str(os.getpid()))

    def test_takeover_never_steals_a_lock_a_rival_just_refreshed(self) -> None:
        fs.state_dir().mkdir(parents=True)
        fs.lock_file().write_text("999999", encoding="utf-8")
        old = time.time() - 11 * 60
        os.utime(fs.lock_file(), (old, old))
        real_rename = os.rename

        def rival_wins_the_race(src, dst):
            fs.lock_file().write_text("424242", encoding="utf-8")          # rival replaces the stale lock
            real_rename(src, dst)                        # ...and we move THEIR fresh one

        with mock.patch.object(fs.os, "rename", rival_wins_the_race):
            with self.assertRaises(fs.Locked):
                with fs.lock():
                    self.fail("proceeded despite a live rival lock")
        self.assertEqual(fs.lock_file().read_text(encoding="utf-8"), "424242")     # put back
        self.assertEqual(list(fs.state_dir().glob("lock.stale.*")), [])

    @unittest.skipIf(not POSIX, "PID liveness is POSIX only")
    def test_stale_lock_of_a_live_pid_is_not_taken_over(self) -> None:
        fs.state_dir().mkdir(parents=True)
        fs.lock_file().write_text(str(os.getpid()), encoding="utf-8")      # a running process
        old = time.time() - 11 * 60
        os.utime(fs.lock_file(), (old, old))
        with self.assertRaises(fs.Locked):
            with fs.lock():
                pass
        self.assertEqual(fs.lock_file().read_text(encoding="utf-8"), str(os.getpid()))

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
        fs.lock_file().write_text("999999", encoding="utf-8")
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
        self.assertEqual(p.read_text(encoding="utf-8"), "{}")
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
        bp.write_text('{"headers": {"Authorization": "Bearer test-token-000"}}', encoding="utf-8")
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
        self.bp.write_text("{}", encoding="utf-8")
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
        root = json.dumps(str(self.tmp))[1:-1]          # JSON-escaped: Windows paths hold backslashes
        fs.state_file().write_text(FIXTURE.read_text(encoding="utf-8").replace("@ROOT@", root),
                                   encoding="utf-8")

    def test_v2_state_migrates(self) -> None:
        self._install_fixture()
        state = store.load_state()
        self.assertEqual(state["version"], 3)
        got = {k: v["mechanism"] for k, v in state["disabled"].items()}
        self.assertEqual(got, {"claude:skill:demo-skill": "move",
                               "claude:mcp:example-mcp": "remove_backup",
                               "claude:plugin:demo-plugin": "native_cli"})
        # written back, atomically and privately
        self.assertEqual(json.loads(fs.state_file().read_text(encoding="utf-8"))["version"], 3)
        if POSIX:
            self.assertEqual(mode(fs.state_file()), 0o600)

    def test_read_only_load_does_not_write(self) -> None:
        self._install_fixture()
        before = fs.state_file().read_text(encoding="utf-8")
        state = store.load_state(write_back=False)
        self.assertEqual(state["version"], 3)
        self.assertEqual(fs.state_file().read_text(encoding="utf-8"), before)

    def test_newer_schema_is_refused(self) -> None:
        fs.state_dir().mkdir(parents=True)
        fs.state_file().write_text('{"version": 4, "disabled": {}}', encoding="utf-8")
        with self.assertRaises(SystemExit):
            store.load_state()
        self.assertEqual(json.loads(fs.state_file().read_text(encoding="utf-8"))["version"], 4)

    def test_v2_migration_is_idempotent(self) -> None:
        self._install_fixture()
        first = store.load_state()
        on_disk = fs.state_file().read_text(encoding="utf-8")
        os.utime(fs.state_file(), (1, 1))
        second = store.load_state()
        self.assertEqual(first, second)
        self.assertEqual(fs.state_file().read_text(encoding="utf-8"), on_disk)
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


class EnableRefusesTamperedStateTest(SandboxCase):
    """DESIGN s6.1 row 2: enable never replays a tampered origin / parked_at / backup."""

    def setUp(self) -> None:
        super().setUp()
        self.write("skills/demo-skill/SKILL.md", "demo")
        self.assertEqual(cli.main(["disable", "skill", "demo-skill"]), 0)
        self.key = "claude:skill:demo-skill"
        self.parked = Path(store.load_state()["disabled"][self.key]["parked_at"])

    def tamper(self, **fields) -> dict:
        state = store.load_state()
        state["disabled"][self.key].update(fields)
        store.save_state(state)
        return state["disabled"][self.key]

    def enable_json(self, *argv: str) -> tuple[int, dict]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main([*argv, "--json"])
        return rc, json.loads(buf.getvalue())

    def assert_refused(self, entry: dict, *argv: str) -> None:
        argv = argv or ("enable", "skill", "demo-skill")
        before = fs.state_file().read_bytes()
        rc, env = self.enable_json(*argv)
        self.assertEqual(rc, 1)
        row = env["results"][0]
        self.assertEqual(row["status"], "error")
        self.assertTrue(row["detail"].startswith("refused: "), row["detail"])
        self.assertEqual(fs.state_file().read_bytes(), before)     # state untouched
        self.assertEqual(store.load_state()["disabled"][self.key], entry)

    def test_origin_outside_the_home_is_refused(self) -> None:
        elsewhere = self.tmp / "elsewhere" / "demo-skill"
        entry = self.tamper(origin=str(elsewhere))
        self.assert_refused(entry)
        self.assertTrue(self.parked.is_dir())                      # nothing moved
        self.assertFalse(elsewhere.parent.exists())

    def test_dotdot_origin_is_refused(self) -> None:
        entry = self.tamper(origin=str(self.home / "skills" / ".." / ".." / "demo-skill"))
        self.assert_refused(entry)
        self.assertTrue(self.parked.is_dir())

    def test_parked_at_outside_the_park_dirs_is_refused(self) -> None:
        victim = self.tmp / "outside" / "demo-skill"
        victim.mkdir(parents=True)
        entry = self.tamper(parked_at=str(victim))
        self.assert_refused(entry)
        self.assertTrue(victim.is_dir())
        self.assertFalse((self.home / "skills" / "demo-skill").exists())

    def test_companion_outside_its_roots_is_refused(self) -> None:
        secret = self.tmp / "secret.txt"
        secret.write_text("s", encoding="utf-8")
        entry = self.tamper(companions=[{"to": str(secret),
                                         "from": str(self.tmp / "planted" / "x.txt")}])
        self.assert_refused(entry)
        self.assertTrue(secret.is_file())
        self.assertFalse((self.tmp / "planted").exists())

    def test_missing_parked_at_is_refused_not_a_crash(self) -> None:
        state = store.load_state()
        del state["disabled"][self.key]["parked_at"]
        store.save_state(state)
        self.assert_refused(state["disabled"][self.key])

    def test_entry_for_another_harness_is_refused(self) -> None:
        entry = self.tamper(harness="codex")
        self.assert_refused(entry)

    def test_dry_run_reports_the_refusal_too(self) -> None:
        self.tamper(origin=str(self.tmp / "elsewhere" / "demo-skill"))
        rc, env = self.enable_json("enable", "skill", "demo-skill", "--dry-run")
        self.assertEqual(rc, 1)
        self.assertTrue(env["results"][0]["detail"].startswith("refused: "))

    def test_legit_entry_still_restores(self) -> None:
        self.assertEqual(cli.main(["enable", "skill", "demo-skill"]), 0)
        self.assertEqual((self.home / "skills/demo-skill/SKILL.md").read_text(encoding="utf-8"),
                         "demo")
        self.assertEqual(store.load_state()["disabled"], {})

    def test_mcp_backup_outside_the_backup_dir_is_refused(self) -> None:
        codex = self.tmp / ".codex"
        codex.mkdir()
        cfg = codex / "config.toml"
        cfg.write_text('[mcp_servers.example-mcp]\ncommand = "x"\n', encoding="utf-8")
        self.assertEqual(cli.main(["disable", "mcp", "example-mcp", "--harness", "codex"]), 0)
        evil = self.tmp / "evil.json"
        evil.write_text(json.dumps({"toml": "[mcp_servers.evil]\ncommand = \"y\"\n"}),
                        encoding="utf-8")
        self.key = "codex:mcp:example-mcp"
        entry = self.tamper(backup=str(evil))
        before = cfg.read_bytes()
        self.assert_refused(entry, "enable", "mcp", "example-mcp", "--harness", "codex")
        self.assertEqual(cfg.read_bytes(), before)

    def test_local_scope_claude_mcp_entry_still_restores(self) -> None:
        # `project` on a claude mcp entry is the local-scope cwd, not --project scope
        proj = self.tmp / "work"
        proj.mkdir()
        fs.backup_dir().mkdir(parents=True, exist_ok=True)
        bp = fs.backup_dir() / "claude__example-mcp.json"
        bp.write_text('{"type": "http"}', encoding="utf-8")
        state = store.load_state()
        state["disabled"]["claude:mcp:example-mcp"] = {
            "mechanism": "remove_backup", "harness": "claude", "type": "mcp",
            "name": "example-mcp", "backend": "claude-json", "backup": str(bp),
            "scope": "local", "project": str(proj), "at": "2026-01-01T00:00:00+0000"}
        store.save_state(state)
        self.cli_rc = 0
        self.assertEqual(cli.main(["enable", "mcp", "example-mcp"]), 0)
        self.assertEqual(self.cli_calls[-1][1], str(proj))

    def test_local_scope_root_project_is_refused(self) -> None:
        fs.backup_dir().mkdir(parents=True, exist_ok=True)
        bp = fs.backup_dir() / "claude__example-mcp.json"
        bp.write_text('{"type": "http"}', encoding="utf-8")
        self.key = "claude:mcp:example-mcp"
        state = store.load_state()
        state["disabled"][self.key] = {
            "mechanism": "remove_backup", "harness": "claude", "type": "mcp",
            "name": "example-mcp", "backend": "claude-json", "backup": str(bp),
            "scope": "local", "project": Path(str(self.tmp)).anchor,
            "at": "2026-01-01T00:00:00+0000"}
        store.save_state(state)
        self.cli_rc = 0
        self.assert_refused(state["disabled"][self.key], "enable", "mcp", "example-mcp")
        self.assertEqual(self.cli_calls, [])


class CheckedConfigWriteTest(SandboxCase):
    """codex config.toml and ~/.claude.json edits are verified and rolled back."""

    @staticmethod
    def failing_verify(expected=None):
        def verify(before: str, after: str) -> str:
            raise ValueError("forced verify failure")
        return verify

    def setUp(self) -> None:
        super().setUp()
        codex = self.tmp / ".codex"
        codex.mkdir()
        self.cfg = codex / "config.toml"
        self.cfg.write_bytes(b'model = "m"\r\n\r\n[mcp_servers.example-mcp]\r\ncommand = "x"\r\n')

    def test_bad_toml_remove_is_rolled_back(self) -> None:
        before = self.cfg.read_bytes()
        with mock.patch.object(fs, "toml_verify", self.failing_verify):
            rc = cli.main(["disable", "mcp", "example-mcp", "--harness", "codex"])
        self.assertEqual(rc, 1)
        self.assertEqual(self.cfg.read_bytes(), before)
        self.assertEqual(store.load_state()["disabled"], {})
        self.assertFalse(list(fs.backup_dir().glob("*.json")))

    def test_bad_toml_add_is_rolled_back_and_entry_kept(self) -> None:
        self.assertEqual(cli.main(["disable", "mcp", "example-mcp", "--harness", "codex"]), 0)
        before = self.cfg.read_bytes()
        with mock.patch.object(fs, "toml_verify", self.failing_verify):
            rc = cli.main(["enable", "mcp", "example-mcp", "--harness", "codex"])
        self.assertEqual(rc, 1)
        self.assertEqual(self.cfg.read_bytes(), before)
        self.assertIn("codex:mcp:example-mcp", store.load_state()["disabled"])
        self.assertEqual(cli.main(["enable", "mcp", "example-mcp", "--harness", "codex"]), 0)

    def test_crlf_toml_round_trips_byte_identical(self) -> None:
        before = self.cfg.read_bytes()
        self.assertEqual(cli.main(["disable", "mcp", "example-mcp", "--harness", "codex"]), 0)
        self.assertEqual(self.cfg.read_bytes(), b'model = "m"\r\n\r\n')
        self.assertEqual(cli.main(["enable", "mcp", "example-mcp", "--harness", "codex"]), 0)
        self.assertEqual(self.cfg.read_bytes(), before)

    def test_toml_edited_between_read_and_write_is_not_overwritten(self) -> None:
        real = mcp_toml.toml_block

        def racing(text, name):
            span = real(text, name)
            # another tool writes after our read
            self.cfg.write_bytes(self.cfg.read_bytes() + b'[other]\r\nx = 1\r\n')
            return span
        with mock.patch.object(mcp_toml, "toml_block", racing):
            with self.assertRaises(fs.WriteError):
                mcp_toml.codex_mcp_remove(self.cfg, "example-mcp")
        self.assertTrue(self.cfg.read_bytes().endswith(b'[other]\r\nx = 1\r\n'))
        self.assertIn(b"[mcp_servers.example-mcp]", self.cfg.read_bytes())

    def test_toml_verify_checks_the_edit_itself_not_a_copy_of_it(self) -> None:
        text = 'a = 1\n\n[mcp_servers.x]\nk = 1\n'
        block = "[mcp_servers.x]\nk = 1\n"
        ok = mcp_toml.remove_verify(text, "x", block)
        self.assertIn(ok(text, "a = 1\n\n"), ("", "unverified (no tomllib)"))
        for bad_after in ("a = 1\n", text, "a = 1\n\n[mcp_servers.x]\n"):
            with self.assertRaises(ValueError, msg=bad_after):
                mcp_toml.remove_verify(text, "x", block)(text, bad_after)
        with self.assertRaises(ValueError):                    # file moved under us
            ok(text + "# new\n", "a = 1\n\n# new\n")
        added = mcp_toml.add_verify("a = 1\n", block)
        with self.assertRaises(ValueError):
            added("a = 1\n", "a = 1\n" + "[mcp_servers.y]\n")
        with self.assertRaises(ValueError):
            added("a = 1\n# new\n", "a = 1\n# new\n\n" + block)

    def test_toml_verify_is_unverified_without_tomllib(self) -> None:
        text, block = 'a = 1\n\n[mcp_servers.x]\nk = 1\n', "[mcp_servers.x]\nk = 1\n"
        with mock.patch.object(fs, "_tomllib", lambda: None):
            self.assertEqual(mcp_toml.remove_verify(text, "x", block)(text, "a = 1\n\n"),
                             "unverified (no tomllib)")
            self.assertEqual(mcp_toml.add_verify("a = 1\n", block)("a = 1\n", "a = 1\n\n" + block),
                             "unverified (no tomllib)")

    def test_corrupt_backup_fails_the_row_not_the_batch(self) -> None:
        self.cfg.write_bytes(self.cfg.read_bytes()
                             + b'\r\n[mcp_servers.second-mcp]\r\ncommand = "y"\r\n')
        self.assertEqual(cli.main(["disable", "mcp", "example-mcp", "second-mcp",
                                   "--harness", "codex"]), 0)
        entry = store.load_state()["disabled"]["codex:mcp:example-mcp"]
        for payload in ("{}", '{"toml": 5}', "[]", "not json"):
            Path(entry["backup"]).write_text(payload, encoding="utf-8")
            rc = cli.main(["enable", "mcp", "example-mcp", "second-mcp", "--harness", "codex"])
            self.assertEqual(rc, 1, payload)
            self.assertEqual(list(store.load_state()["disabled"]), ["codex:mcp:example-mcp"])
            self.assertIn(b"[mcp_servers.second-mcp]", self.cfg.read_bytes())    # batch went on
            self.assertEqual(cli.main(["disable", "mcp", "second-mcp", "--harness", "codex"]), 0)

    def test_bad_connector_write_is_rolled_back(self) -> None:
        cj = self.tmp / ".claude.json"
        cj.write_bytes(b'{"claudeAiMcpEverConnected": ["claude.ai Demo"],\n'
                       b' "projects": {"/p": {"disabledMcpServers": []}}}')
        before = cj.read_bytes()
        with mock.patch.object(fs, "json_verify", self.failing_verify):
            rc = cli.main(["disable", "mcp", "claude.ai Demo"])
        self.assertEqual(rc, 1)
        self.assertEqual(cj.read_bytes(), before)
        self.assertEqual(store.load_state()["disabled"], {})


class LogRowsCarryHarnessAndBatchTest(SandboxCase):
    def test_toggle_rows(self) -> None:
        (self.tmp / ".codex" / "skills" / "demo-skill").mkdir(parents=True)
        self.assertEqual(cli.main(["disable", "skill", "demo-skill", "--harness", "codex"]), 0)
        self.assertEqual(cli.main(["enable", "skill", "demo-skill", "--harness", "codex"]), 0)
        rows = [json.loads(x) for x in fs.log_file().read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(r["harness"], r["batch"], r["action"]) for r in rows],
                         [("codex", store.BATCH, "disable"), ("codex", store.BATCH, "enable")])
