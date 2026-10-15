"""CLI dispatch, status views, and the sandbox / packaging invariants."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import update_kit
from base import SandboxCase
from test_cli_surface import CliCase
from test_project import ProjectCase

from agent_toggle import __version__, cli, fs, ops, settings, store, update_prompt
from agent_toggle.output import Result

REPO = Path(__file__).resolve().parents[1]


class UiProjectSeamTest(ProjectCase):
    """`ui --project`: the picker gets the project's scope, the plan carries the project."""

    def test_pick_is_scoped_and_apply_plan_carries_the_project(self) -> None:
        try:
            from agent_toggle.ui import picker
        except ImportError:
            self.skipTest("curses unavailable")
        self.run_cli("disable", "skill", "demo-skill", "--project", str(self.proj))
        row = picker.Row("claude", "agent", "demo-agent", True)
        row.staged = False
        with mock.patch.object(picker, "pick", return_value=[row]) as pick, \
                mock.patch.object(ops, "apply_plan", wraps=ops.apply_plan) as spy:
            rc, _, _ = self.run_cli("ui", "--project", str(self.proj))
        self.assertEqual(rc, 0)
        state, table = pick.call_args.args[:2]
        self.assertEqual(list(table), ["claude"])
        self.assertEqual(table["claude"].project, self.proj.resolve())
        self.assertEqual(list(state["disabled"]), [self.key])      # this project's entries only
        self.assertEqual(pick.call_args.kwargs["project"], self.proj.resolve())
        self.assertIs(pick.call_args.kwargs["plugins"], False)     # plugins are user scope
        self.assertEqual(spy.call_args.args[0],
                         [ops.Op("claude", "agent", "disable", "demo-agent", str(self.proj.resolve()))])
        self.assertFalse((self.pclaude / "agents" / "demo-agent.md").exists())

    def test_user_scope_ui_is_unchanged(self) -> None:
        try:
            from agent_toggle.ui import picker
        except ImportError:
            self.skipTest("curses unavailable")
        with mock.patch.object(picker, "pick", return_value=None) as pick:
            self.assertEqual(self.run_cli("ui")[0], 0)
        self.assertIsNone(pick.call_args.kwargs["project"])
        self.assertIn("codex", pick.call_args.args[1])


class HarnessGateTest(SandboxCase):
    def test_unsupported_pair_is_refused(self) -> None:
        (self.tmp / ".openclaw").mkdir()     # installed, so the type gate is what refuses
        # unsupported type is refused as "unsupported" (4)
        self.assertEqual(cli.main(["disable", "mcp", "whatever", "--harness", "openclaw"]), 4)
        # unknown harness is a usage error (2)
        self.assertEqual(cli.main(["disable", "skill", "x", "--harness", "nope"]), 2)


class ApplyPlanSeamTest(SandboxCase):
    """cmd_toggle and cmd_ui share ONE apply path: ops.apply_plan."""

    def test_cmd_toggle_goes_through_apply_plan(self) -> None:
        self.write("skills/demo-skill/SKILL.md")
        with mock.patch.object(ops, "apply_plan", wraps=ops.apply_plan) as spy:
            self.assertEqual(cli.main(["disable", "skill", "demo-skill"]), 0)
        plan = spy.call_args.args[0]
        self.assertEqual(plan, [ops.Op("claude", "skill", "disable", "demo-skill")])
        self.assertEqual(spy.call_args.kwargs["batch"], store.BATCH)
        self.assertFalse((self.home / "skills/demo-skill").exists())
        self.assertFalse(fs.reload_file().exists())   # only `ui` signals: the mod reloads its own runs itself

    def test_cmd_ui_goes_through_apply_plan(self) -> None:
        try:
            from agent_toggle.ui import picker
        except ImportError:
            self.skipTest("curses unavailable")
        self.write("skills/demo-skill/SKILL.md")
        row = picker.Row("claude", "skill", "demo-skill", True)
        row.staged = False
        with mock.patch.object(picker, "pick", return_value=[row]), \
                mock.patch.object(ops, "apply_plan", wraps=ops.apply_plan) as spy:
            self.assertEqual(cli.main(["ui"]), 0)
        self.assertEqual(spy.call_args.args[0],
                         [ops.Op("claude", "skill", "disable", "demo-skill")])
        self.assertFalse((self.home / "skills/demo-skill").exists())
        self.assertIn("claude:skill:demo-skill", store.load_state()["disabled"])
        # Enter applied it: open Claude Code sessions (the mod polls this file) reload the skill
        sig = json.loads(fs.reload_file().read_text(encoding="utf-8"))
        self.assertEqual([(r["type"], r["name"], r["action"], r["status"]) for r in sig["results"]],
                         [("skill", "demo-skill", "disable", "ok")])

    def test_type_without_a_mechanism_is_an_unsupported_row(self) -> None:
        out = Result()
        fails = ops.apply_plan([ops.Op("openclaw", "mcp", "disable", "x")], out, dry_run=True)
        self.assertEqual((fails, out.rows[0]["status"]), (1, "unsupported"))

    def test_one_lock_hold_for_the_whole_plan(self) -> None:
        for n in ("a", "b"):
            self.write(f"skills/{n}/SKILL.md")
        with mock.patch.object(fs, "lock", wraps=fs.lock) as spy:
            fails = ops.apply_plan([ops.Op("claude", "skill", "disable", n) for n in "ab"],
                                   Result())
        self.assertEqual((fails, spy.call_count), (0, 1))
        self.assertEqual(len(store.load_state()["disabled"]), 2)


class StatusTest(SandboxCase):
    def test_parked_drift_flags_untracked_and_live_twins(self) -> None:
        parked, live = self.home / "skills-disabled", self.home / "skills"
        for n in ("tracked", "orphan", "twin"):
            (parked / n).mkdir(parents=True)
        (live / "twin").mkdir()
        items = sorted(parked.iterdir())
        untracked, twins = cli.parked_drift(items, parked, live, {str(parked / "tracked")})
        # tracked item is not drift
        self.assertEqual(sorted(p.name for p in untracked), ["orphan", "twin"])
        # only the live-twin is reported as a twin
        self.assertEqual(twins, ["twin"])

    def test_status_skips_dotfiles_in_park_dir(self) -> None:
        for rel in (".DS_Store", "orphan/SKILL.md"):
            (park := self.user_parked("skills", rel)).parent.mkdir(parents=True, exist_ok=True)
            park.write_text("x", encoding="utf-8")
        self.write("skills/.DS_Store")
        out = Result()
        cli.cmd_status(store.load_state(), out)
        info = next(r for r in out.rows if r.get("home") == str(self.home))["parked"]["skill"]
        # .DS_Store is neither a parked item nor a live twin, matching doctor
        self.assertEqual((info["parked"], info["untracked"], info["live_twins"]), (1, 1, []))

    def test_status_script_exits_zero_with_temp_home(self) -> None:
        # HOME / USERPROFILE already point at the sandbox and are inherited.
        p = subprocess.run([sys.executable, str(REPO / "agent_toggle.py"), "status"],
                           capture_output=True, text=True, cwd=self.tmp, timeout=60, encoding="utf-8")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn(str(fs.state_file()), p.stdout)


class SandboxInvariantTest(SandboxCase):
    def test_sandbox_home_is_under_tempdir(self) -> None:
        tmp_root = Path(tempfile.gettempdir()).resolve()
        self.assertTrue(Path.home().resolve().is_relative_to(tmp_root))
        self.assertEqual(fs.home(), self.tmp)

    def test_import_resolves_to_package_dir(self) -> None:
        import agent_toggle
        self.assertEqual(Path(agent_toggle.__file__).resolve().parent, REPO / "agent_toggle")


HINT = f"agent-toggle v99.0.0 is available (you have {__version__}) — run `agent-toggle update`"


class UpdateHintTest(CliCase):
    """The one-line hint and `update` / `--update`. No network: update_kit.check / update are
    patched; AGENT_TOGGLE_UPDATE_CHECK=0 (tests/base.py) is lifted here only."""

    def setUp(self) -> None:
        super().setUp()
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("AGENT_TOGGLE_UPDATE_CHECK", None)
        self.tty = mock.patch.object(update_kit, "should_hint", return_value=True)
        self.tty.start()
        self.addCleanup(self.tty.stop)
        self.check = mock.patch.object(update_kit, "check", return_value="v99.0.0")
        self.check_mock = self.check.start()
        self.addCleanup(self.check.stop)

    def test_hint_on_stderr_on_a_tty(self) -> None:
        rc, out, err = self.run_cli("status")
        self.assertEqual(rc, 0)
        self.assertEqual(err.splitlines()[-1], HINT)
        self.assertNotIn("99.0.0", out)

    def test_no_hint_off_a_tty(self) -> None:
        update_kit.should_hint.return_value = False
        self.assertNotIn("99.0.0", self.run_cli("status")[2])
        self.check_mock.assert_not_called()

    def test_opt_out_by_env_and_by_setting(self) -> None:
        with mock.patch.dict(os.environ, {"AGENT_TOGGLE_UPDATE_CHECK": "0"}):
            self.assertNotIn("99.0.0", self.run_cli("status")[2])
        settings.set("update_check", False)
        self.assertNotIn("99.0.0", self.run_cli("status")[2])
        self.check_mock.assert_not_called()

    def test_json_stdout_is_one_document_without_a_hint(self) -> None:
        rc, out, err = self.run_cli("status", "--json")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["command"], "status")     # the WHOLE stdout
        self.assertNotIn("99.0.0", err)

    def test_every_exit_path_hints_and_keeps_its_exit_code(self) -> None:
        with fs.lock():
            locked = self.run_cli("disable", "skill", "x")
        self.assertEqual(locked[0], 3)
        cases = {("--help",): 0, ("--version",): 0, ("help", "status"): 0, ("help", "nope"): 2,
                 ("disable",): 2, ("--color", "bogus", "status"): 2,
                 ("disable", "skill", "x", "--harness", "codex"): 4}
        for argv, want in cases.items():
            rc, _, err = self.run_cli(*argv)
            self.assertEqual(rc, want, argv)
            self.assertIn(HINT, err, argv)
        self.assertIn(HINT, locked[2])

    def test_update_and_completion_never_hint(self) -> None:
        with mock.patch.object(update_kit, "update", return_value=0):
            self.assertNotIn("99.0.0", self.run_cli("update")[2])
        self.assertNotIn("99.0.0", self.run_cli("completion", "bash")[2])

    def test_update_and_dash_dash_update_exit_with_update_kits_code(self) -> None:
        for argv in (("update",), ("--update",), ("--json", "--update")):
            for code in (0, 1, 2):
                seen = {}

                def fake(**kw):
                    seen.update(kw, to_stderr=kw["out"] is sys.stderr)   # run_cli swaps stderr
                    kw["out"].write("kit says hi\n")
                    return code
                with mock.patch.object(update_kit, "update", side_effect=fake), \
                        mock.patch.object(update_prompt, "after_steps", return_value=[["x"]]):
                    rc, out, err = self.run_cli(*argv)
                self.assertEqual((rc, out, err), (code, "", "kit says hi\n"), argv)
                self.assertEqual((seen["app"], seen["repo"], seen["dist"], seen["after"], seen["to_stderr"]),
                                 ("agent-toggle", "weskao/agent-toggle", "agent-toggle", [["x"]], True))

    def test_update_help_describes_the_new_behaviour(self) -> None:
        self.assertIn("newest release", self.run_cli("help", "update")[1])


class SettingsApplyTest(CliCase):
    def test_default_harness_replaces_claude_for_disable(self) -> None:
        (self.tmp / ".codex" / "skills" / "a").mkdir(parents=True)
        self.write("skills/a/SKILL.md")
        settings.set("default_harness", "codex")
        self.assertEqual(self.run_cli("disable", "skill", "a")[0], 0)
        self.assertFalse((self.tmp / ".codex" / "skills" / "a").exists())
        self.assertTrue((self.home / "skills" / "a").exists())
        self.assertEqual(self.run_cli("enable", "skill", "a")[0], 0)
        self.assertTrue((self.tmp / ".codex" / "skills" / "a").exists())

    def test_color_setting_between_env_and_flag(self) -> None:
        settings.set("color", "always")
        self.assertIn("\x1b[", self.run_cli("status")[1])
        self.assertNotIn("\x1b[", self.run_cli("status", "--color", "never")[1])
        with mock.patch.dict(os.environ, {"AGENT_TOGGLE_COLOR": "never"}):
            self.assertNotIn("\x1b[", self.run_cli("status")[1])
        settings.set("color", "auto")
        self.assertNotIn("\x1b[", self.run_cli("status")[1])             # not a TTY

    def test_an_off_default_harness_still_acts_but_warns(self) -> None:
        self.write("skills/a/SKILL.md")
        settings.set("harness.claude", False)
        rc, _, err = self.run_cli("disable", "skill", "a")
        self.assertEqual(rc, 0)
        self.assertFalse((self.home / "skills" / "a").exists())
        self.assertEqual(err.count("default harness 'claude' is turned off in settings "
                                   "(agent-toggle config)"), 1)
        rc, _, err = self.run_cli("enable", "skill", "a", "--harness", "claude")
        self.assertEqual((rc, err), (0, ""))                 # explicit: no warning

    def test_status_notes_harnesses_hidden_by_settings(self) -> None:
        settings.set("harness.codex", False)
        settings.set("harness.grok", False)
        self.assertIn("2 harness(es) hidden by settings (agent-toggle config)",
                      self.run_cli("status")[1])
        self.assertNotIn("hidden by settings", self.run_cli("status", "--harness", "codex")[1])

    def test_install_shims_skips_harnesses_off_in_settings(self) -> None:
        (self.tmp / ".codex").mkdir()
        settings.set("harness.codex", False)
        rc, out, _ = self.run_cli("install-shims")
        self.assertEqual(rc, 0)
        self.assertIn("skipped codex (off in settings)", out)
        self.assertFalse((self.tmp / ".codex" / "skills" / "agent-toggle").exists())
        self.assertTrue((self.home / "skills" / "agent-toggle" / "SKILL.md").is_file())
        self.assertEqual(self.run_cli("install-shims", "--harness", "codex")[0], 0)   # explicit
        self.assertTrue((self.tmp / ".codex" / "skills" / "agent-toggle" / "SKILL.md").is_file())

    def test_ctrl_c_during_the_command_skips_the_update_hint(self) -> None:
        with mock.patch.object(update_prompt, "hint") as hint, \
                mock.patch.object(cli, "_main", side_effect=KeyboardInterrupt), \
                self.assertRaises(KeyboardInterrupt):
            cli.main(["status"])
        hint.assert_not_called()

    def test_an_item_shared_with_an_enabled_harness_is_not_hidden(self) -> None:
        (self.home / "skills" / "a").mkdir(parents=True)
        (self.home / "skills" / "a" / "SKILL.md").write_text("x", encoding="utf-8")
        self.assertEqual(self.run_cli("disable", "skill", "a")[0], 0)
        key = "claude:skill:a"
        state = fs.state_file()
        data = json.loads(state.read_text(encoding="utf-8"))
        data["disabled"][key]["shared_with"] = ["opencode"]
        state.write_text(json.dumps(data), encoding="utf-8")
        settings.set("harness.claude", False)
        for argv in (["list"], ["cost"]):
            rows = self.run_json(*argv)[1]["results"]
            self.assertIn("claude", {r["harness"] for r in rows}, argv)
        self.assertNotIn("hidden by settings", self.run_cli("list")[1])
        settings.set("harness.opencode", False)               # every owner off: hidden again
        self.assertIn("1 hidden by settings", self.run_cli("list")[1])

    def test_ctrl_c_while_checking_for_the_update_keeps_the_exit_code(self) -> None:
        with mock.patch.object(update_prompt, "hint", side_effect=KeyboardInterrupt):
            self.assertEqual(cli.main(["status"]), 0)

    def test_hidden_by_settings_is_a_json_warning_and_a_cost_text_note(self) -> None:
        (self.tmp / ".codex" / "skills" / "a").mkdir(parents=True)
        self.assertEqual(self.run_cli("disable", "skill", "a", "--harness", "codex")[0], 0)
        for argv in (["status"], ["list"], ["cost"]):          # nothing hidden: no new warning
            self.assertFalse([w for w in self.run_json(*argv)[1]["warnings"]
                              if "hidden by settings" in w], argv)
        self.assertNotIn("hidden by settings", self.run_cli("cost")[1])
        settings.set("harness.codex", False)
        for argv in (["status"], ["list"], ["cost"]):
            warnings = self.run_json(*argv)[1]["warnings"]
            self.assertTrue([w for w in warnings if "hidden by settings (agent-toggle config)" in w], argv)
            self.assertFalse([w for w in self.run_json(*argv, "--harness", "codex")[1]["warnings"]
                              if "hidden by settings" in w], argv)    # asked for: nothing hidden
        self.assertIn("hidden by settings (agent-toggle config)", self.run_cli("cost")[1])
        self.assertNotIn("hidden by settings", self.run_cli("cost", "--harness", "codex")[1])

    def test_disabled_harness_is_hidden_unless_asked_for(self) -> None:
        (self.tmp / ".codex" / "skills" / "a").mkdir(parents=True)
        self.assertEqual(self.run_cli("disable", "skill", "a", "--harness", "codex")[0], 0)
        settings.set("harness.codex", False)
        for argv in (["status"], ["list"], ["cost"]):
            harnesses = {r["harness"] for r in self.run_json(*argv)[1]["results"]}
            self.assertNotIn("codex", harnesses, argv)
            shown = {r["harness"] for r in self.run_json(*argv, "--harness", "codex")[1]["results"]}
            self.assertIn("codex", shown, argv)
        out = self.run_cli("list")[1]       # never "nothing disabled" while status counts it
        self.assertNotIn("nothing disabled", out)
        self.assertIn("1 hidden by settings (agent-toggle config)", out)


class HarnessGateProjectTest(ProjectCase):
    """A harness switched off in settings hides its project entries; --harness brings them back."""

    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.p("disable", "skill", "demo-skill")[0], 0)
        settings.set("harness.claude", False)

    def test_the_parked_project_line_in_status_is_hidden(self) -> None:
        self.assertNotIn(f"project {self.proj.resolve()}", self.run_cli("status")[1])
        self.assertIn(f"project {self.proj.resolve()}", self.run_cli("status", "--harness", "claude")[1])

    def test_the_stale_parked_warning_is_hidden_until_asked_for(self) -> None:
        shutil.rmtree(self.parked("skills-disabled", "demo-skill"))
        rc, out, _ = self.run_cli("status")
        self.assertEqual(rc, 0)
        self.assertNotIn("parked item missing", out)
        self.assertIn("parked item missing", self.run_cli("status", "--harness", "claude")[1])

    def test_cost_project_text_notes_what_settings_hide(self) -> None:
        self.assertIn("hidden by settings (agent-toggle config)",
                      self.run_cli("cost", "--project", str(self.proj))[1])
