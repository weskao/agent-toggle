"""Claude CLI access: binary lookup and the subprocess runner.

`which` and `runner` are module-level hooks so tests can stub them and never
spawn the real claude CLI.
"""
from __future__ import annotations

import os
import shutil
import subprocess

from .. import fs

which = shutil.which
runner = subprocess.run


def claude_bin() -> str | None:
    """Find the claude CLI. It is often absent from a hook/agent PATH."""
    found = which("claude")
    if found:
        return found
    home = fs.home()
    for cand in (home / ".local/bin/claude", home / ".claude/local/claude"):
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def run_cli(binary: str | None, args: list[str], cwd: str | None = None) -> tuple[bool, str]:
    """Run a harness CLI. `cwd` matters: `claude mcp -s local` is per-project."""
    if not binary:
        return False, "CLI not found on PATH"
    try:
        p = runner([binary, *args], capture_output=True, text=True,
                   timeout=120, cwd=cwd)
    except subprocess.TimeoutExpired:
        return False, "CLI timed out after 120s"
    except OSError as e:                     # cwd gone, binary unexecutable
        return False, f"cannot run CLI{f' in {cwd}' if cwd else ''}: {e}"
    return p.returncode == 0, (p.stdout + p.stderr).strip()
