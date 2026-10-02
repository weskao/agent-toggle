"""install-shims: writes the skill shim into installed harnesses only, idempotently."""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import unittest
from pathlib import Path

from base import SandboxCase

from agent_toggle import cli
from agent_toggle.harnesses import harnesses

REPO = Path(cli.__file__).resolve().parent.parent


def shim(home: Path) -> Path:
    return home / "skills" / "agent-toggle" / "SKILL.md"


class InstallShimsCase(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        (self.tmp / ".codex").mkdir()          # ~/.claude comes from the sandbox

    def run_cli(self, *argv: str) -> tuple[int, dict]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(["install-shims", "--json", *argv])
        return rc, json.loads(buf.getvalue())

    def installed(self) -> set[str]:
        return {h for h, rec in harnesses().items() if shim(rec.home).is_file()}

    def test_writes_only_existing_harnesses(self) -> None:
        rc, env = self.run_cli()
        self.assertEqual(rc, 0)
        self.assertEqual(self.installed(), {"claude", "codex"})
        self.assertEqual({r["harness"] for r in env["results"] if r["status"] == "ok"},
                         {"claude", "codex"})
        self.assertEqual({r["harness"] for r in env["results"] if r["status"] == "skipped"},
                         {"grok", "opencode", "openclaw", "vibe", "devin", "agy"})
        text = shim(self.tmp / ".claude").read_text(encoding="utf-8")
        self.assertIn("--json", text)
        self.assertNotIn("__AGENT_TOGGLE_ROOT__", text)
        self.assertIn("agent_toggle.py", text)     # checkout fallback line kept

    def test_rerun_is_idempotent(self) -> None:
        self.run_cli()
        first = shim(self.tmp / ".claude").read_text(encoding="utf-8")
        rc, _ = self.run_cli()
        self.assertEqual(rc, 0)
        self.assertEqual(shim(self.tmp / ".claude").read_text(encoding="utf-8"), first)

    def test_dry_run_writes_nothing(self) -> None:
        (self.tmp / ".claude" / ".gitignore").write_text("junk\n", encoding="utf-8")
        rc, env = self.run_cli("--dry-run")
        self.assertEqual(rc, 0)
        self.assertEqual(self.installed(), set())
        self.assertEqual((self.tmp / ".claude" / ".gitignore").read_text(encoding="utf-8"), "junk\n")
        self.assertTrue(all(r["status"] in ("planned", "skipped") for r in env["results"]))

    def test_no_harness_is_exit_4(self) -> None:
        for h in (".claude", ".codex"):
            os.rename(self.tmp / h, self.tmp / (h + "-gone"))
        rc, env = self.run_cli()
        self.assertEqual(rc, 4)
        self.assertFalse(env["ok"])

    def test_park_dirs_appended_once_from_table(self) -> None:
        ignore = self.tmp / ".claude" / ".gitignore"
        ignore.write_text("skills-disabled/\nkeep", encoding="utf-8")          # one present, no trailing newline
        self.run_cli()
        self.run_cli()
        lines = ignore.read_text(encoding="utf-8").splitlines()
        for d in ("skills-disabled/", "agents-disabled/", "commands-disabled/"):
            self.assertEqual(lines.count(d), 1, d)
        self.assertIn("keep", lines)
        self.assertFalse((self.tmp / ".codex" / ".gitignore").exists())   # never created

    def test_template_in_package_dir(self) -> None:
        self.assertTrue((Path(cli.__file__).parent / "shims" / "claude.md.tmpl").is_file())
        self.assertFalse((REPO / "shims").exists())

    @unittest.skipIf(os.name == "nt", "install.sh is a POSIX wrapper; Windows uses the console script")
    def test_install_sh_wrapper(self) -> None:
        env = {**os.environ, "HOME": str(self.tmp), "USERPROFILE": str(self.tmp)}
        p = subprocess.run(["bash", str(REPO / "install.sh")], env=env,
                           capture_output=True, text=True, timeout=60, encoding="utf-8")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.installed(), {"claude", "codex"})


if __name__ == "__main__":
    unittest.main()
