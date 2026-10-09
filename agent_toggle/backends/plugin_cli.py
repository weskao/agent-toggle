"""Claude CLI access: binary lookup and the subprocess runner.

`which` and `runner` are module-level hooks so tests can stub them and never
spawn the real claude CLI.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Callable

from .. import fs

which = shutil.which
runner = subprocess.run

TIMEOUT_ENV = "AGENT_TOGGLE_CLI_TIMEOUT"
MUTATE_TIMEOUT = 120      # enable / disable / install / mcp add|remove: may fetch from a marketplace
READ_TIMEOUT = 30         # read-only `plugin list`


def claude_bin() -> str | None:
    """Find the claude CLI. It is often absent from a hook/agent PATH."""
    found = which("claude")
    if found:
        return found
    home = fs.home()
    for cand in (home / ".local/bin/claude", home / ".local/bin/claude.exe",   # .exe: Windows
                 home / ".claude/local/claude"):
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def _timeout(default: float) -> float:
    """`AGENT_TOGGLE_CLI_TIMEOUT` (seconds, > 0) beats the per-call default; junk is warned about, not fatal."""
    raw = os.environ.get(TIMEOUT_ENV)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
        if 0 < value < float("inf"):
            return value
    except ValueError:
        pass
    print(f"agent-toggle: ignoring {TIMEOUT_ENV}={raw!r} (need a positive number of seconds)",
          file=sys.stderr)
    return default


def run_cli(binary: str | None, args: list[str], cwd: str | None = None,
            timeout: float = MUTATE_TIMEOUT) -> tuple[bool, str]:
    """Run a harness CLI. `cwd` matters: `claude mcp -s local` is per-project.
    `timeout` is the per-call default; the env var overrides it."""
    if not binary:
        return False, "CLI not found on PATH"
    limit = _timeout(timeout)
    try:
        p = runner([binary, *args], capture_output=True, text=True,
                   timeout=limit, cwd=cwd)
    except subprocess.TimeoutExpired:
        return False, f"CLI timed out after {limit:g}s (raise it with {TIMEOUT_ENV}=<seconds>)"
    except OSError as e:                     # cwd gone, binary unexecutable
        return False, f"cannot run CLI{f' in {cwd}' if cwd else ''}: {e}"
    return p.returncode == 0, (p.stdout + p.stderr).strip()


def list_plugins(warn: Callable[[str], None]) -> list[dict]:
    """`claude plugin list --json` through the injectable runner; [] when unavailable."""
    exe = claude_bin()
    if not exe:
        return []
    ok, text = run_cli(exe, ["plugin", "list", "--json"], timeout=READ_TIMEOUT)
    try:       # run_cli merges stderr into the text: skip any notice before the array
        data = json.JSONDecoder().raw_decode(text, max(text.find("["), 0))[0] if ok else None
    except ValueError:
        data = None
    if not isinstance(data, list):
        warn("could not read `claude plugin list --json`; plugin rows come from state only")
        return []
    return [p for p in data if isinstance(p, dict) and isinstance(p.get("id"), str)]


def full_plugin_id(bare: str, listed: list[str]) -> str | None:
    """The one listed `name@marketplace` id a plugin parked as the bare `name` stands for.
    None when no id or several (an ambiguous bare name stays its own row)."""
    full = sorted({i for i in listed if i.partition("@")[0] == bare})   # one id may list per scope
    return full[0] if len(full) == 1 else None
