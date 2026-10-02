"""CLI dispatch, status views, and the sandbox / packaging invariants."""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

from base import SandboxCase

from agent_toggle import cli, fs, ops, store
from agent_toggle.output import Result

REPO = Path(__file__).resolve().parents[1]


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
