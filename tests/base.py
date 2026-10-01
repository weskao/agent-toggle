"""Shared sandbox for the test suite. stdlib only, no fixtures, no network."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_toggle.backends import plugin_cli


class SandboxCase(unittest.TestCase):
    """A throwaway HOME / USERPROFILE with a fake ~/.claude tree.

    Every path in the package resolves from Path.home() at call time, so
    pointing the environment at a temp dir is the whole sandbox. The claude
    CLI is stubbed: `cli_calls` records each would-be invocation and
    `cli_rc` is the exit code the stub reports (non-zero by default).
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="agent-toggle-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for var in ("HOME", "USERPROFILE"):
            self.addCleanup(self._restore_env, var, os.environ.get(var))
            os.environ[var] = str(self.tmp)

        self.home = self.tmp / ".claude"
        for sub in ("skills", "agents", "commands", "scripts"):
            (self.home / sub).mkdir(parents=True)

        self.cli_calls: list[tuple[list[str], str | None]] = []
        self.cli_rc = 1
        saved = plugin_cli.which, plugin_cli.runner
        self.addCleanup(setattr, plugin_cli, "which", saved[0])
        self.addCleanup(setattr, plugin_cli, "runner", saved[1])
        plugin_cli.which = lambda name: str(self.tmp / "fake-bin" / name)
        plugin_cli.runner = self._fake_run

    @staticmethod
    def _restore_env(var: str, value: str | None) -> None:
        if value is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = value

    def _fake_run(self, cmd: list[str], **kw) -> subprocess.CompletedProcess:
        self.cli_calls.append((cmd[1:], kw.get("cwd")))
        return subprocess.CompletedProcess(cmd, self.cli_rc, stdout="", stderr="")

    def write(self, rel: str, text: str = "x") -> Path:
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p
