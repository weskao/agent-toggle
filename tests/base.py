"""Shared sandbox for the test suite. stdlib only, no fixtures, no network."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_toggle import fs
from agent_toggle.backends import plugin_cli


def _can_symlink() -> bool:
    """Windows needs privilege / Developer Mode to create symlinks; probe, don't assume."""
    d = Path(tempfile.mkdtemp(prefix="agent-toggle-symlink-probe-"))
    try:
        (d / "t").mkdir()
        (d / "l").symlink_to(d / "t", target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        return False
    finally:
        shutil.rmtree(d, ignore_errors=True)


CAN_SYMLINK = _can_symlink()

# Never hit the network for an update check, in-process or in a subprocess that
# inherits this environment (SandboxCase.setUp only touches HOME/TERM/..., so it stays).
os.environ["AGENT_TOGGLE_UPDATE_CHECK"] = "0"


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
        # opencode's home follows XDG_CONFIG_HOME; never let the real one leak in
        self.addCleanup(self._restore_env, "XDG_CONFIG_HOME", os.environ.get("XDG_CONFIG_HOME"))
        os.environ.pop("XDG_CONFIG_HOME", None)
        # colour is decided from the environment; start every test from a known terminal
        for var in ("NO_COLOR", "FORCE_COLOR", "TERM"):
            self.addCleanup(self._restore_env, var, os.environ.get(var))
            os.environ.pop(var, None)
        os.environ["TERM"] = "xterm"

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

    def git_init(self, path: Path | None = None) -> None:
        """Make `path` (default: the claude home) a git work tree, as a dotfiles repo is."""
        subprocess.run(["git", "init", "-q", str(path or self.home)], check=True)

    def user_parked(self, sub: str, *rest: str, owner: str = "claude") -> Path:
        """Where a user-scope item of `<owner home>/<sub>` parks (fs.user_park)."""
        return fs.user_park(owner, sub).joinpath(*rest)

    def write_parked(self, rel: str, text: str = "x", owner: str = "claude") -> Path:
        """write() into the user park dir: `skills/a/SKILL.md` -> user_parked("skills", ...)."""
        sub, _, rest = rel.partition("/")
        p = self.user_parked(sub, rest, owner=owner)
        fs.private_dir(fs.parked_dir())          # 0700, as a real disable makes it
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def write(self, rel: str, text: str = "x") -> Path:
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p
