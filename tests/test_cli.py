"""CLI dispatch, status views, and the sandbox / packaging invariants."""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from base import SandboxCase

from agent_toggle import cli, fs

REPO = Path(__file__).resolve().parents[1]


class HarnessGateTest(SandboxCase):
    def test_unsupported_pair_is_refused(self) -> None:
        (self.tmp / ".openclaw").mkdir()     # installed, so the type gate is what refuses
        # unsupported type is refused as "unsupported" (4)
        self.assertEqual(cli.main(["disable", "mcp", "whatever", "--harness", "openclaw"]), 4)
        # unknown harness is a usage error (2)
        self.assertEqual(cli.main(["disable", "skill", "x", "--harness", "nope"]), 2)


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
                           capture_output=True, text=True, cwd=self.tmp, timeout=60)
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
