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
from pathlib import Path
from typing import Callable

from .. import fs

which = shutil.which
runner = subprocess.run

PLUGIN_SCOPES = ("user", "project", "local")      # `claude plugin enable|disable -s`

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
        p = runner([binary, *args], capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=limit, cwd=cwd)     # Windows: not cp1252
    except subprocess.TimeoutExpired:
        return False, f"CLI timed out after {limit:g}s (raise it with {TIMEOUT_ENV}=<seconds>)"
    except OSError as e:                     # cwd gone, binary unexecutable
        return False, f"cannot run CLI{f' in {cwd}' if cwd else ''}: {e}"
    return p.returncode == 0, (p.stdout + p.stderr).strip()


def plugin_where(name: str, claude_home: Path) -> tuple[str | None, str | None]:
    """(scope, cwd) of plugin `name`'s one install, from Claude Code's install registry
    (`<claude_home>/plugins/installed_plugins.json`; a bare name: its one `name@mkt` id).

    Without `-s` the CLI picks the scope from its cwd, so a project-scope plugin toggled
    from another dir lands in user scope. (None, None) when the registry cannot say --
    absent, another shape, several installs, a project dir that is gone: the caller
    then lets the CLI auto-detect, as before. A file read, not `claude plugin list`
    (~0.8 s per call, paid on every pane toggle)."""
    try:
        reg = json.loads((claude_home / "plugins" / "installed_plugins.json")
                         .read_text(encoding="utf-8"))
        rows = [r for k, v in reg["plugins"].items()
                if k == name or ("@" not in name and k.partition("@")[0] == name) for r in v]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None, None
    if len(rows) != 1 or not isinstance(rows[0], dict) or rows[0].get("scope") not in PLUGIN_SCOPES:
        return None, None
    scope, proj = rows[0]["scope"], rows[0].get("projectPath")
    if scope == "user":
        return scope, None
    if isinstance(proj, str) and os.path.isabs(proj) and os.path.isdir(proj):
        return scope, proj
    return None, None


def list_plugins(warn: Callable[[str], None], cwd: str | None = None) -> list[dict]:
    """`claude plugin list --json` through the injectable runner; [] when unavailable.
    `cwd`: its `enabled` is the plugin's state as seen from there."""
    exe = claude_bin()
    if not exe:
        return []
    ok, text = run_cli(exe, ["plugin", "list", "--json"], cwd=cwd, timeout=READ_TIMEOUT)
    try:       # run_cli merges stderr into the text: skip any notice before the array
        data = json.JSONDecoder().raw_decode(text, max(text.find("["), 0))[0] if ok else None
    except ValueError:
        data = None
    if not isinstance(data, list):
        why = f" ({text.splitlines()[-1][:120]})" if not ok and text else ""   # e.g. a timeout
        warn(f"could not read `claude plugin list --json`{why}; plugin rows come from state only")
        return []
    return [p for p in data if isinstance(p, dict) and isinstance(p.get("id"), str)]


def full_plugin_id(bare: str, listed: list[str]) -> str | None:
    """The one listed `name@marketplace` id a plugin parked as the bare `name` stands for.
    None when no id or several (an ambiguous bare name stays its own row)."""
    full = sorted({i for i in listed if i.partition("@")[0] == bare})   # one id may list per scope
    return full[0] if len(full) == 1 else None
