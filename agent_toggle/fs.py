"""Filesystem layer: the ONLY place that resolves the home / state dirs.

Every path is computed at call time from Path.home(), so a test (or a caller)
that changes HOME / USERPROFILE gets a consistent view without patching
module constants.
"""
from __future__ import annotations

import contextlib
import errno
import hashlib
import importlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

TEXT_SUFFIXES = {".md", ".sh", ".py", ".js", ".mjs", ".cjs", ".ts", ".json",
                 ".yaml", ".yml", ".toml", ".txt", ".zsh", ".bash"}
# Directories never worth walking when deciding if a companion is shared. The
# `*-disabled` names are legacy sibling park dirs, still there until `migrate`.
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


def user_park(owner: str, sub: str) -> Path:
    """Where `owner`'s user-scope items from `<owner home>/<sub>` park: never inside a
    harness home (Claude Code loads commands/ and rules/ recursively, so a park dir
    nested there would still load). An absolute sub (an OpenCode skills.paths
    redirect) parks as ext-<sha1(its resolved path)[:8]>."""
    base = parked_dir() / "user" / owner
    if not Path(sub).is_absolute():
        return base / sub
    return base / f"ext-{hashlib.sha1(str(Path(sub).resolve()).encode('utf-8')).hexdigest()[:8]}"


def legacy_park(live: Path) -> Path:
    """The legacy sibling park dir `<live>-disabled`: only `migrate` and its hints use it."""
    return live.with_name(live.name + "-disabled")


def park_unignored() -> Path | None:
    """The work-tree root holding parked_dir() WITHOUT ignoring it (e.g. a tracked $HOME),
    else None: outside any work tree a park dir cannot dirty `git status`."""
    park = parked_dir().resolve()        # git_toplevel is resolved; /var vs /private/var
    top = git_toplevel(next(p for p in (park, *park.parents) if p.is_dir()))
    return top if top and not gitignored(park, top) else None


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

WIN = os.name == "nt"
REPLACE_TRIES = 3


def replace(src: Path, dst: Path) -> None:
    """os.replace. On Windows it fails while another program holds `dst` open, so
    retry briefly, then fail loudly (DESIGN s5.11); elsewhere a failure is final."""
    for attempt in range(1, REPLACE_TRIES + 1):
        try:
            os.replace(src, dst)
            return
        except PermissionError as e:
            if not WIN:
                raise
            if attempt == REPLACE_TRIES:
                raise PermissionError(
                    f"{dst} is in use by another program (tried {REPLACE_TRIES}x): {e}") from e
            time.sleep(0.1 * attempt)

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


def lock_held() -> bool:
    """True if a live run holds the lock (status/doctor then see its op in flight)."""
    path = lock_file()
    return path.is_file() and _holder_alive(path)


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
                # a killed run leaves its lock behind; say so instead of "another run"
                dead = not WIN and path.is_file() and not _holder_alive(path)
                raise Locked(f"{path} is held by another agent-toggle run "
                             f"(waited {LOCK_WAIT:g}s)"
                             + (f"; its PID is not running (a killed run?) -- it expires "
                                f"{LOCK_STALE / 60:g} min after it was taken, or remove "
                                f"{path} if no agent-toggle is running" if dead else "")
                             ) from None
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


def atomic_write(path: Path, text: str, mode: int = 0o600, newline: str | None = None) -> None:
    """Write `text` to `path` via a same-dir tmp file created with `mode`.
    `newline=""` writes line endings as given (a user file edited in place)."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)          # leftover from a crashed run with our PID
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline=newline) as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        replace(tmp, path)
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
        replace(tmp, path)
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


def toml_parse(text: str):
    """Parse-check `text`: tomllib, or on Python 3.10 the structural checker in
    toml_check. Raises ValueError. The result is a dict tree (values undecoded on 3.10)."""
    toml = _tomllib()
    if toml is not None:
        return toml.loads(text)              # TOMLDecodeError is a ValueError
    from . import toml_check
    return toml_check.parse(text)


def toml_verify(expected: str | None = None):
    """verify(): the result parses (toml_parse) and, if given, equals `expected` --
    the before text with the one [mcp_servers.<name>] block removed/appended."""
    def verify(before: str, after: str) -> str:
        toml_parse(after)
        if expected is not None and after != expected:
            raise ValueError("result differs from the expected edit")
        return ""
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

    try:
        os.rename(src, target)                      # same filesystem: one atomic step
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
        _copy_move(src, target)
    return target


def move_leftovers(src: Path, target: Path) -> tuple[Path, Path]:
    """(half copy, trash) a killed cross-fs move can leave beside `target` / `src`."""
    return (target.with_name(f".{target.name}.agent-toggle-tmp"),
            src.with_name(f".{src.name}.agent-toggle-del"))


def remove_leftover(p: Path) -> None:
    """Delete one of move_leftovers()' paths (a link is unlinked, never followed)."""
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p)
    elif p.exists() or p.is_symlink():
        p.unlink()


def _fsync(path: Path | str) -> None:
    """Flush one file's data or one dir's entries (POSIX; Windows has no dir fsync).
    A dir fsync is best effort: some FUSE / SMB filesystems refuse it (EINVAL)."""
    if WIN or os.path.islink(path):
        return
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        if not os.path.isdir(path):
            raise


def _fsync_tree(root: Path) -> None:
    if not root.is_dir() or root.is_symlink():
        _fsync(root)
        return
    for dirpath, _, files in os.walk(root):
        for f in files:
            _fsync(os.path.join(dirpath, f))
        _fsync(dirpath)


def _copy_move(src: Path, target: Path) -> None:
    """A cross-filesystem move that a kill cannot leave as two half copies (G12).

    Copy into a temp sibling of `target`, rename it into place (atomic: same fs),
    rename `src` aside (atomic: its own fs), then delete that. At every point
    either `src` is intact or `target` is complete; the caller's write-ahead state
    entry says which op was running (mechanisms.settle resolves it next run).
    Symlinks stay links and modes follow copy2, as shutil.move's fallback did."""
    tmp, trash = move_leftovers(src, target)
    remove_leftover(tmp)                 # a killed earlier attempt's half copy
    try:
        if src.is_symlink():
            os.symlink(os.readlink(src), tmp)
        elif src.is_dir():
            shutil.copytree(src, tmp, symlinks=True)
        else:
            shutil.copy2(src, tmp)
        _fsync_tree(tmp)                 # flushed before the source goes (macOS: no F_FULLFSYNC)
        # ponytail: stdlib has no atomic no-replace rename; re-check to shrink the race
        # with something (a sync job) re-creating the target during a long copy
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"{target} appeared during the copy -- refusing to overwrite")
        replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            remove_leftover(tmp)
        raise
    try:
        _fsync(target.parent)
        remove_leftover(trash)           # only ever exists once its copy was complete
        replace(src, trash)
    except BaseException:                # src untouched and complete: drop our copy,
        if src.exists() or src.is_symlink():   # renamed back first so `target` is never
            with contextlib.suppress(OSError):  # partial (settle removes a left `tmp`)
                replace(target, tmp)
                remove_leftover(tmp)
        raise
    # ponytail: a trash dir that cannot be deleted (permissions) stays as a dot-named
    # duplicate of what was parked; report it if that ever shows up in the wild.
    with contextlib.suppress(OSError):
        remove_leftover(trash)


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


def git_toplevel(d: Path) -> Path | None:
    """The resolved root of the git work tree containing `d`; None if `d` is in none
    (or git is missing)."""
    try:
        p = subprocess.run(["git", "-C", str(d), "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, encoding="utf-8", timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return Path(p.stdout.strip()).resolve() if p.returncode == 0 and p.stdout.strip() else None


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
