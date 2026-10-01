"""Filesystem layer: the ONLY place that resolves the home / state dirs.

Every path is computed at call time from Path.home(), so a test (or a caller)
that changes HOME / USERPROFILE gets a consistent view without patching
module constants.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

TEXT_SUFFIXES = {".md", ".sh", ".py", ".js", ".mjs", ".cjs", ".ts", ".json",
                 ".yaml", ".yml", ".toml", ".txt", ".zsh", ".bash"}
# Directories never worth walking when deciding if a companion is shared.
PRUNE = {".git", "node_modules", "cache", "__pycache__", "dist", "build",
         "skills-disabled", "agents-disabled", "commands-disabled", "venv"}


# ------------------------------------------------------------------- paths

def home() -> Path:
    return Path.home()


def state_dir() -> Path:
    return home() / ".agent-toggle"


def state_file() -> Path:
    return state_dir() / "state.json"


def log_file() -> Path:
    return state_dir() / "log.jsonl"


def backup_dir() -> Path:
    return state_dir() / "mcp-backups"


def companion_dir() -> Path:
    return state_dir() / "companions"


def legacy_state_dir() -> Path:
    return home() / ".claude-toggle"


def claude_json() -> Path:
    return home() / ".claude.json"


# ------------------------------------------------------------ the safe move

def safe_move(src: Path, dest_dir: Path) -> Path:
    """Move src INTO dest_dir, refusing every way this can silently corrupt data.

    The bug this exists to prevent: `mv X dest/` when `dest` does not exist
    RENAMES X to dest. The first item becomes the directory and its contents
    end up scattered at the parked-dir root. shutil.move() behaves the same.
    So: create the directory, then prove it is a directory, before touching src.
    """
    if not (src.exists() or src.is_symlink()):
        raise FileNotFoundError(f"{src} does not exist")

    # Order matters: mkdir(exist_ok=True) raises FileExistsError when the path
    # exists as a FILE, so the is_dir() guard has to come first to give a
    # useful error instead of a confusing one.
    if dest_dir.exists() and not dest_dir.is_dir():
        raise NotADirectoryError(
            f"{dest_dir} exists but is not a directory -- refusing to move "
            f"{src.name} (it would be renamed INTO that path and destroyed)"
        )
    dest_dir.mkdir(parents=True, exist_ok=True)
    if not dest_dir.is_dir():                       # lost a race, or odd fs
        raise NotADirectoryError(f"{dest_dir} is not a directory after mkdir")

    target = dest_dir / src.name
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"{target} already exists -- refusing to overwrite")

    shutil.move(str(src), str(target))
    return target


def prune_empty(start: Path, stop: Path) -> None:
    """Drop directories left empty by a move, up to but excluding `stop`.

    Restoring commands/orch/batch.md otherwise leaves an empty `orch/` behind
    in the park dir, which accumulates and inflates the parked counts.
    """
    cur = start
    while cur != stop and stop in cur.parents:
        try:
            cur.rmdir()          # refuses non-empty, which is the guard
        except OSError:
            return
        cur = cur.parent


def gitignored(path: Path, repo: Path) -> bool:
    """True if git ignores `path`. Unknown (no git / not a repo) counts as False."""
    try:
        target_str = str(path) + "/"
        p = subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", target_str],
                           capture_output=True, timeout=10)
        return p.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def iter_text_files(root: Path):
    if root.is_file():
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in PRUNE and not d.startswith(".")]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix.lower() in TEXT_SUFFIXES:
                yield p


def read_text(p: Path) -> str:
    try:
        if p.stat().st_size > 2_000_000:
            return ""
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
