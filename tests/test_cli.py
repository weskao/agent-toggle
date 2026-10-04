"""CLI dispatch, status views, and the sandbox / packaging invariants."""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

from base import SandboxCase
from test_project import ProjectCase

from agent_toggle import cli, fs, ops, store
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
        self.write("skills-disabled/.DS_Store")
        self.write("skills-disabled/orphan/SKILL.md")
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
