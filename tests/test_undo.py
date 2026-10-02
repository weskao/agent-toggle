"""undo (reverse the last logged batch) and enable --all (restore everything)."""
from __future__ import annotations

import json
import shutil
from unittest import mock

from test_cli_surface import CliCase, snapshot
from test_conformance import FIXTURES, file_bytes

from agent_toggle import fs, store


class UndoCase(CliCase):
    def setUp(self) -> None:
        super().setUp()
        self.cli_rc = 0
        for h in ("claude", "codex"):
            shutil.copytree(FIXTURES / h, self.tmp, dirs_exist_ok=True)

    def batch(self, batch_id: str, *argv: str) -> tuple[int, dict]:
        """One CLI run under its own batch id (tests share one process, so one pid)."""
        with mock.patch.object(store, "BATCH", batch_id):
            return self.run_json(*argv)

    def state(self) -> dict:
        return json.loads(fs.state_file().read_text(encoding="utf-8"))["disabled"]

    def log_rows(self) -> list[dict]:
        return [json.loads(ln) for ln in fs.log_file().read_text(encoding="utf-8").splitlines()]


class UndoTest(UndoCase):
    def test_undo_reverses_the_last_batch_only(self) -> None:
        before = file_bytes(self.tmp)
        self.assertEqual(self.batch("b1", "disable", "agent", "demo-agent")[0], 0)
        self.assertEqual(self.batch("b2", "disable", "skill", "demo-skill", "--harness", "codex")[0], 0)
        self.assertEqual(self.batch("b3", "disable", "skill", "demo-skill")[0], 0)
        self.assertEqual(self.batch("b3", "disable", "command", "demo-command")[0], 0)
        rc, env = self.run_json("undo")
        self.assertEqual((rc, env["command"]), (0, "undo"))
        self.assertEqual(sorted(self.state()), ["claude:agent:demo-agent",
                                                "codex:skill:demo-skill"])    # earlier batches stay
        self.assertTrue((self.home / "skills" / "demo-skill").exists())
        self.assertTrue((self.home / "commands" / "demo-command.md").exists())
        self.assertEqual(self.run_cli("undo")[0], 0)           # the last batch is now the undo
        self.assertEqual(len(self.state()), 4)
        self.assertNotEqual(file_bytes(self.tmp), before)

    def test_undo_after_enable_reparks(self) -> None:
        self.batch("b1", "disable", "skill", "demo-skill")
        self.batch("b2", "enable", "skill", "demo-skill")
        self.assertEqual(self.run_cli("undo")[0], 0)
        self.assertFalse((self.home / "skills" / "demo-skill").exists())
        self.assertIn("claude:skill:demo-skill", self.state())

    def test_undo_twice_toggles_back(self) -> None:
        before = file_bytes(self.tmp)
        self.batch("b1", "disable", "skill", "demo-skill")
        parked = file_bytes(self.tmp)
        self.assertEqual(self.run_cli("undo")[0], 0)
        self.assertEqual(file_bytes(self.tmp), before)
        self.assertEqual(self.run_cli("undo")[0], 0)                          # undo of the undo
        self.assertEqual(file_bytes(self.tmp), parked)

    def test_undo_rows_are_logged_as_a_new_batch(self) -> None:
        self.batch("b1", "disable", "skill", "demo-skill")
        self.run_cli("undo")
        rows = self.log_rows()
        self.assertEqual(rows[-1]["action"], "enable")
        self.assertNotEqual(rows[-1]["batch"], "b1")

    def test_undo_covers_mcp_in_another_harness(self) -> None:
        self.batch("b1", "disable", "mcp", "example-mcp", "--harness", "codex")
        before = self.state()
        self.assertEqual(len(before), 1)
        self.assertEqual(self.run_cli("undo")[0], 0)
        self.assertEqual(self.state(), {})

    def test_dry_run_writes_nothing(self) -> None:
        self.batch("b1", "disable", "skill", "demo-skill")
        before = snapshot(self.tmp)
        rc, env = self.run_json("undo", "--dry-run")
        self.assertEqual(rc, 0, env)
        self.assertEqual(snapshot(self.tmp), before)
        self.assertEqual([r["action"] for r in env["results"]], ["would-enable"])

    def test_log_without_batch_is_refused(self) -> None:
        fs.private_dir(fs.state_dir())
        fs.log_file().write_text(json.dumps(
            {"ts": "2026-01-01T00:00:00+0000", "type": "skill", "name": "demo-skill",
             "action": "disable", "result": "ok", "detail": ""}) + "\n", encoding="utf-8")
        rc, env = self.run_json("undo")
        self.assertEqual(rc, 1)
        self.assertIn("nothing to undo: log predates undo", env["results"][-1]["detail"])

    def test_empty_log_is_not_an_error(self) -> None:
        rc, out, _ = self.run_cli("undo")
        self.assertEqual(rc, 0)
        self.assertIn("nothing to undo", out)

    def test_failed_rows_do_not_count_as_the_last_batch(self) -> None:
        self.batch("b1", "disable", "skill", "demo-skill")
        self.assertEqual(self.batch("b2", "disable", "skill", "ghost")[0], 1)   # error row only
        self.assertEqual(self.run_cli("undo")[0], 0)
        self.assertTrue((self.home / "skills" / "demo-skill").exists())

    def test_project_scope_rows_never_replay_into_user_scope(self) -> None:
        self.batch("b1", "disable", "skill", "demo-skill")
        with fs.lock():                    # a later batch: a project row for a vanished project
            store.log("disable", "skill", "demo-skill", "ok", harness="claude", batch="b2",
                      project=str(self.tmp / "proj"), scope="project")
        rc, env = self.run_json("undo")
        self.assertEqual(rc, 1)
        self.assertIn("no such directory", env["results"][-1]["detail"])
        self.assertEqual(list(self.state()), ["claude:skill:demo-skill"])   # user entry kept
        self.assertFalse((self.home / "skills" / "demo-skill").exists())

    def test_corrupt_log_rows_are_refused(self) -> None:
        fs.private_dir(fs.state_dir())
        for bad in ({"type": ["x"], "name": "n"}, {"type": "skill"}, {"type": "skill", "name": None},
                    {"type": "bogus", "name": "n"}, {"type": "skill", "name": "../x"}):
            fs.log_file().write_text(json.dumps(
                {"harness": "claude", "action": "disable", "result": "ok", "batch": "b1", **bad})
                + "\n", encoding="utf-8")
            rc, env = self.run_json("undo")
            self.assertEqual(rc, 1, bad)
            self.assertIn("corrupt log row", env["results"][-1]["detail"])

    def test_user_scope_mcp_row_with_local_project_is_still_user_scope(self) -> None:
        """A user-scope row (and claude mcp `local` rows) undo in user scope; only
        scope=project is a --project undo."""
        with fs.lock():
            store.log("disable", "skill", "demo-skill", "ok", harness="claude", batch="b1")
        self.assertEqual(self.run_cli("undo", "--dry-run")[0], 1)    # only: skill not parked -> error row
        rc, env = self.run_json("undo", "--dry-run")
        self.assertNotIn("project-scope", env["results"][-1]["detail"])

    def test_harness_flag_is_a_usage_error(self) -> None:
        self.assertEqual(self.run_cli("undo", "--harness", "codex")[0], 2)


class EnableAllTest(UndoCase):
    def disable_some(self) -> None:
        self.batch("b1", "disable", "skill", "demo-skill")
        self.batch("b2", "disable", "agent", "demo-agent")
        self.batch("b3", "disable", "skill", "demo-skill", "--harness", "codex")
        self.batch("b3", "disable", "mcp", "example-mcp", "--harness", "codex")

    def test_enable_all_empties_state_across_harnesses(self) -> None:
        before = file_bytes(self.tmp)
        self.disable_some()
        rc, env = self.run_json("enable", "--all")
        self.assertEqual((rc, env["command"]), (0, "enable"), env)
        self.assertEqual(self.state(), {})
        self.assertEqual(file_bytes(self.tmp), before)

    def test_harness_filter(self) -> None:
        self.disable_some()
        self.assertEqual(self.run_cli("enable", "--all", "--harness", "codex")[0], 0)
        self.assertEqual(sorted(self.state()), ["claude:agent:demo-agent", "claude:skill:demo-skill"])

    def test_nothing_disabled(self) -> None:
        rc, out, _ = self.run_cli("enable", "--all")
        self.assertEqual(rc, 0)
        self.assertIn("nothing disabled", out)

    def test_dry_run_writes_nothing(self) -> None:
        self.disable_some()
        before = snapshot(self.tmp)
        rc, env = self.run_json("enable", "--all", "--dry-run")
        self.assertEqual(rc, 0, env)
        self.assertEqual(snapshot(self.tmp), before)
        self.assertEqual({r["action"] for r in env["results"]}, {"would-enable"})

    def test_project_entries_never_restore_into_user_scope(self) -> None:
        self.batch("b1", "disable", "skill", "demo-skill")
        state = json.loads(fs.state_file().read_text(encoding="utf-8"))
        entry = state["disabled"].pop("claude:skill:demo-skill")
        key = store.make_key("claude", "skill", "demo-skill", self.tmp / "proj")
        state["disabled"][key] = dict(entry, project=str(self.tmp / "proj"))
        fs.state_file().write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(self.run_cli("enable", "--all")[0], 1)     # project dir is gone
        self.assertEqual(list(self.state()), [key])
        self.assertFalse((self.home / "skills" / "demo-skill").exists())

    def test_missing_harness_home_is_an_error_row(self) -> None:
        self.disable_some()
        shutil.rmtree(self.tmp / ".codex")
        rc, env = self.run_json("enable", "--all")
        self.assertEqual(rc, 1)
        bad = [r for r in env["results"] if r["status"] == "error"]
        self.assertTrue(bad and all(r["harness"] == "codex" for r in bad))
        self.assertEqual(sorted(self.state()), ["codex:mcp:example-mcp", "codex:skill:demo-skill"])

    def test_usage_errors(self) -> None:
        for argv in (("enable", "--all", "x"), ("enable", "--all", "skill", "demo-skill"),
                     ("enable", "--all", "skill"), ("disable", "--all"),
                     ("disable", "--all", "skill", "x"), ("enable",), ("enable", "skill")):
            self.assertEqual(self.run_cli(*argv)[0], 2, argv)

    def test_both_spellings_match(self) -> None:
        self.disable_some()
        self.assertEqual(self.run_cli("--enable", "--all", "--dry-run", "--json"),
                         self.run_cli("enable", "--all", "--dry-run", "--json"))
