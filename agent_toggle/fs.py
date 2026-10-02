"""Filesystem layer: the ONLY place that resolves the home / state dirs.

Every path is computed at call time from Path.home(), so a test (or a caller)
that changes HOME / USERPROFILE gets a consistent view without patching
module constants.
"""
from __future__ import annotations

import contextlib
import importlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

TEXT_SUFFIXES = {".md", ".sh", ".py", ".js", ".mjs", ".cjs", ".ts", ".json",
                 ".yaml", ".yml", ".toml", ".txt", ".zsh", ".bash"}
# Directories never worth walking when deciding if a companion is shared.
PRUNE = {".git", "node_modules", "cache", "__pycache__", "dist", "build",
         "skills-disabled", "agents-disabled", "commands-disabled", "rules-disabled",
         "prompts-disabled", "command-disabled", "venv"}


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


def lock_file() -> Path:
    return state_dir() / "lock"


def profiles_dir() -> Path:
    return state_dir() / "profiles"


def parked_dir() -> Path:
    return state_dir() / "parked"


def contained(path: Path, *roots: Path) -> bool:
    """True if `path` sits inside one of `roots` (DESIGN s6.1 rows 1-2).

    Refuses any `..` part. Only the PARENT is resolved: the item itself may be
    a symlink and is never followed. A root inside our own state dir (parked,
    backups) must not be reached through a symlink at all, since nothing
    legitimate puts one there; harness homes may be symlinked (dotfile repos).
    """
    path = Path(path)
    if ".." in path.parts:
        return False
    parent = path.parent.resolve()
    sd = state_dir()
    for root in map(Path, roots):
        if not parent.is_relative_to(root.resolve()):
            continue
        if root.is_relative_to(sd):
            chain = [sd, *(p for p in path.parent.parents if p.is_relative_to(sd)),
                     path.parent]
            if any(p.is_symlink() for p in chain):
                continue
        return True
    return False


# ------------------------------------------------- lock + private atomic write

LOCK_WAIT = 5.0           # seconds a second run waits before giving up
LOCK_STALE = 600.0        # a lock older than this is a crashed run's leftover


class Locked(Exception):
    """Another agent-toggle run holds the lock (the CLI maps this to exit 3)."""


def private_dir(path: Path) -> Path:
    """mkdir the state dir and (one level below it) `path`, both mode 0700."""
    sd = state_dir()
    sd.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path != sd:
        path.mkdir(mode=0o700, exist_ok=True)
    return path


def _holder_alive(path: Path) -> bool:
    """True if the PID recorded in the lock belongs to a running process.
    Unknown (non-POSIX, unreadable / empty file) counts as not alive."""
    if os.name == "nt":
        return False
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
        if pid <= 0:
            return False
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                      # exists, owned by someone else
    except (OSError, ValueError):
        return False
    return True


def refresh_lock() -> None:
    """Bump the mtime of a lock WE hold, so a batch longer than LOCK_STALE is
    not mistaken for a crashed run. No-op when we do not hold it."""
    path = lock_file()
    try:
        if path.read_text(encoding="utf-8") == str(os.getpid()):
            os.utime(path)
    except OSError:
        pass


@contextlib.contextmanager
def lock():
    """Hold <state_dir>/lock (O_EXCL create, PID inside) for a whole batch."""
    path = lock_file()
    private_dir(state_dir())
    deadline = time.monotonic() + LOCK_WAIT
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            try:
                if (time.time() - path.stat().st_mtime > LOCK_STALE
                        and not _holder_alive(path)):
                    # Takeover is stat-then-rename, so a rival may replace the
                    # lock in between: rename can then move THEIR fresh lock.
                    # Re-stat what we actually moved and put it back if so.
                    # ponytail: the lock path is briefly absent during put-back;
                    # fine for a CLI, use flock/O_TMPFILE if contention matters.
                    stale = path.with_name(f"lock.stale.{os.getpid()}")
                    os.rename(path, stale)
                    if time.time() - stale.stat().st_mtime <= LOCK_STALE:
                        try:
                            os.link(stale, path)     # fails if a third run took it
                            stale.unlink(missing_ok=True)
                        except OSError:
                            pass
                        raise Locked(f"{path} is held by another agent-toggle run")
                    stale.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue                 # released / taken over by a rival
            if time.monotonic() >= deadline:
                raise Locked(f"{path} is held by another agent-toggle run "
                             f"(waited {LOCK_WAIT:g}s)") from None
            time.sleep(0.05)
    pid = str(os.getpid())
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(pid)
        yield
    finally:
        try:                             # only remove a lock that is still ours
            if path.read_text(encoding="utf-8") == pid:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    """Write `text` to `path` via a same-dir tmp file created with `mode`."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)          # leftover from a crashed run with our PID
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


class WriteError(Exception):
    """A checked_write was rolled back; the message names the file and why."""


def _replace_bytes(path: Path, data: bytes, mode: int) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)                  # os.open's mode is umask-filtered
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def checked_write(path: Path, text: str, verify) -> str:
    """Replace `path` with `text`, then re-read it and run verify(before, after).

    Returns verify's note ("" when fully verified). On ANY exception the
    previous bytes and mode are put back (a new file is removed) and
    WriteError is raised. Bytes, not text mode, so line endings survive.
    A symlinked config (dotfile repo) is written through, keeping the link.
    """
    path = Path(os.path.realpath(path))
    try:
        old = path.read_bytes()
        old_mode = path.stat().st_mode & 0o7777
    except FileNotFoundError:
        old, old_mode = None, 0o600
    try:
        _replace_bytes(path, text.encode("utf-8"), old_mode)
        after = path.read_bytes().decode("utf-8")
        return verify("" if old is None else old.decode("utf-8", errors="replace"), after)
    except BaseException as e:           # Ctrl-C mid-verify must not leave the write
        try:
            if _untouched(path, old, old_mode):
                pass                     # failed before os.replace: nothing to put back
            elif old is None:
                path.unlink(missing_ok=True)
            else:
                _replace_bytes(path, old, old_mode)
        except OSError as r:
            raise WriteError(f"{path}: write failed ({e}) AND restore failed ({r})") from e
        if not isinstance(e, Exception):
            raise
        raise WriteError(f"{path}: write rolled back ({type(e).__name__}: {e})") from e


def _untouched(path: Path, old: bytes | None, old_mode: int) -> bool:
    """True if `path` still holds exactly its pre-write bytes and mode (or is still absent)."""
    try:
        return path.read_bytes() == old and path.stat().st_mode & 0o7777 == old_mode
    except FileNotFoundError:
        return old is None
    except OSError:
        return False


def json_verify(expected: str | None = None):
    """verify(): the result parses as JSON and, if given, equals `expected` exactly."""
    def verify(before: str, after: str) -> str:
        json.loads(after)
        if expected is not None and after != expected:
            raise ValueError("result differs from the expected edit")
        return ""
    return verify


def _tomllib():
    """The tomllib module, or None on Python 3.10 (no dependency may be added)."""
    try:
        return importlib.import_module("tomllib")
    except ImportError:
        return None


def toml_verify(expected: str | None = None):
    """verify(): the result parses (when tomllib exists) and, if given, equals
    `expected` -- the before text with the one [mcp_servers.<name>] block
    removed/appended. Note is `unverified (no tomllib)` only when neither ran."""
    def verify(before: str, after: str) -> str:
        toml = _tomllib()
        if toml is not None:
            toml.loads(after)                # TOMLDecodeError is a ValueError
        if expected is not None and after != expected:
            raise ValueError("result differs from the expected edit")
        return "" if toml is not None or expected is not None else "unverified (no tomllib)"
    return verify


def tighten(path: Path) -> None:
    """chmod 0600 when group/world can read it. Best effort."""
    try:
        if path.is_file() and path.stat().st_mode & 0o077:
            path.chmod(0o600)
    except OSError:
        pass


def too_open(path: Path, mask: int = 0o044) -> bool:
    """True if `path` has any of the `mask` bits set (default: group/world read);
    always False where modes don't apply."""
    try:
        return os.name != "nt" and bool(path.stat().st_mode & mask)
    except OSError:
        return False


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


def sync_markers(d: Path) -> list[str]:
    """Names of `.synced-from-*` markers in `d`: a sync job may re-create parked items."""
    try:
        return sorted(p.name for p in d.glob(".synced-from-*"))
    except OSError:
        return []


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
