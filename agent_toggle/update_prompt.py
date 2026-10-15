"""`update` / `--update` and the one-line "newer release" hint, both through update_kit.

    cmd_update()   # install the newest tag, then refresh the plugin and shims; returns the exit code
    hint()         # after a command: one stderr line when a newer release exists (TTY only)

No prompts: the old arrow-key panel and its skip-version bookkeeping are gone.
"""
from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence
from functools import cached_property

from . import __version__, i18n, settings
from .backends import plugin_cli
from .ui import theme

DIST = "agent-toggle"
REPO = "weskao/agent-toggle"
PLUGIN = "agent-toggle@agent-toggle"


def after_steps() -> list[list[str]]:
    """Run with the NEW binary once the install is done: the Claude Code plugin when it is
    installed, then the shims."""
    steps: list[list[str]] = []
    # bare "claude": what a person pastes; _run() resolves it, as claude is often off PATH
    if plugin_cli.claude_bin() and any(p["id"] == PLUGIN for p in plugin_cli.list_plugins(lambda _msg: None)):
        steps += [["claude", "plugin", "marketplace", "update", DIST],
                  ["claude", "plugin", "update", PLUGIN]]
    return [*steps, [DIST, "install-shims"]]


class _LazySteps(Sequence):
    """after_steps(), computed on first use: update_kit only reads `after` once an install
    will happen, so a dev refusal / "already up to date" skips `claude plugin list` (0.8 s)."""

    @cached_property
    def _steps(self) -> list[list[str]]:
        return after_steps()

    def __len__(self) -> int:
        return len(self._steps)

    def __getitem__(self, i):
        return self._steps[i]


def _run(cmd, **kw):
    """update_kit's subprocess runner: their output stays off stdout and no step may prompt,
    and a step like `install-shims` must not re-check for updates and print the hint."""
    if cmd[0] == "claude":
        cmd = [plugin_cli.claude_bin() or "claude", *cmd[1:]]
    # update_kit passes its own env (no-prompt git vars) for the install step: merge, don't replace
    kw["env"] = {**kw.get("env", os.environ), "AGENT_TOGGLE_UPDATE_CHECK": "0"}
    return subprocess.run(cmd, stdout=sys.stderr, stdin=subprocess.DEVNULL, **kw)


def cmd_update() -> int:
    # imported here, like telegram_kit: a checkout run (`python3 agent_toggle.py`) or a stale env lacks it
    import update_kit
    return update_kit.update(app=DIST, repo=REPO, dist=DIST, lang=i18n.LANGUAGE,
                             after=_LazySteps(), out=sys.stderr, run=_run)


def hint(*, json_mode: bool = False) -> None:
    """One stderr line when a newer release exists: only on a terminal, never under --json,
    off with the `update_check` setting (env AGENT_TOGGLE_UPDATE_CHECK wins). Never raises."""
    try:
        import update_kit
        if json_mode or not settings.get("update_check") or not update_kit.should_hint(sys.stderr):
            return
        latest = update_kit.check(repo=REPO, current=__version__)
        if latest:
            print(theme.encodable(update_kit.hint(DIST, latest, __version__, i18n.LANGUAGE), sys.stderr),
                  file=sys.stderr)
    except Exception:  # noqa: BLE001 - a hint must never fail the command
        pass
