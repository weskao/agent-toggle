"""Update check on top of update-kit, plus the "skip this version" memory.

    from agent_toggle.update_check import check
    found = check("1.0.0", cache_path=Path.home() / ".agent-toggle" / "update-check.json")
    if found:
        print(f"{found.latest} is available (you have {found.current})")

The lookup itself -- ``git ls-remote`` of the release tags, the 10-minute cache in
``~/.update-kit/state.json``, the timeout -- is :func:`update_kit.check`; ``latest`` is
the tag as it returns it (``v1.2.0``). Only the skipped version lives in *cache_path*.

To overlap the lookup with the command's own work instead of paying for
it at exit, start it early and collect the result at the end:

    started = start("1.0.0", cache_path=...)
    ...  # the command's own work
    found = collect(started)

To ask the user what to do instead of only hinting, hand :func:`offer` the
UI as a callback -- it owns what each answer does, the UI only picks one:

    offer(started, ask, cache_path=..., upgrade=["uv", "tool", "upgrade", "agent-toggle"])

where ``ask(found)`` returns ``UPDATE_NOW``, ``SKIP`` or ``SKIP_VERSION``.
When the upgrade depends on the version or takes several steps, pass a
callable instead: ``upgrade=lambda found: [[...], [...]]`` runs each command
in order and stops at the first one that fails.
``SKIP_VERSION`` is remembered in *cache_path*; a newer release asks again.

Nothing here is allowed to raise to the caller: a missing cache, a bad
payload, or a dead network all become ``None``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "DEFAULT_TIMEOUT",
    "REPO",
    "SKIP",
    "SKIP_VERSION",
    "UPDATE_NOW",
    "UPGRADE_FAILED",
    "Started",
    "UpdateAvailable",
    "check",
    "collect",
    "offer",
    "skip",
    "start",
]

REPO = "weskao/agent-toggle"
#: How long :func:`collect` waits for the background lookup before giving up on it.
DEFAULT_TIMEOUT = 0.8

#: Answers an ``ask`` callback returns to :func:`offer`.
UPDATE_NOW = "update-now"
SKIP = "skip"
SKIP_VERSION = "skip-version"
#: What :func:`offer` returns when UPDATE_NOW's command could not run or failed.
UPGRADE_FAILED = "upgrade-failed"

#: What a skipped version may look like before it is trusted: it is read from a file.
_VERSION_RE = re.compile(r"v?\d{1,6}(\.\d{1,6}){1,3}([a-zA-Z0-9.+-]{0,20})?")


@dataclass(frozen=True)
class UpdateAvailable:
    current: str
    latest: str


def check(current: str, *, cache_path: Path) -> UpdateAvailable | None:
    """Return an update when the latest release is newer than *current* and not the
    version skipped in *cache_path*, else None. Never raises."""
    try:
        import update_kit  # lazy: a checkout run or a stale env lacks it

        latest = update_kit.check(repo=REPO, current=current)
        if not isinstance(latest, str) or _bare(latest) == _bare(_load_cache(cache_path).get("skipped")):
            return None
        return UpdateAvailable(current=current, latest=latest)
    except Exception:  # noqa: BLE001 - a hint must never fail the command
        return None


@dataclass
class Started:
    """A background check in flight; pass to :func:`collect` to get its result."""

    thread: threading.Thread
    _result: list = field(default_factory=list)


def start(current: str, *, cache_path: Path) -> Started:
    """Run :func:`check` on a daemon thread so it overlaps the caller's own work."""
    result: list = []

    def run() -> None:
        result.append(check(current, cache_path=cache_path))

    thread = threading.Thread(target=run, name="agent-toggle-update-check", daemon=True)
    thread.start()
    return Started(thread=thread, _result=result)


def collect(started: Started | None, *, timeout: float = DEFAULT_TIMEOUT) -> UpdateAvailable | None:
    """Wait for a :func:`start`-ed check and return its result. Never raises."""
    if started is None:
        return None
    try:
        # Usually already done: it ran alongside the caller's own work.
        started.thread.join(timeout)
        return started._result[0] if started._result else None
    except Exception:  # noqa: BLE001 - a hint must never fail the command
        return None


def skip(cache_path: Path, version: str) -> None:
    """Stop reporting *version*; a newer release is reported again. Never raises."""
    data = _load_cache(cache_path)
    data["skipped"] = version
    _write_cache(cache_path, data)


def offer(
    started: Started | None,
    ask: Callable[[UpdateAvailable], str],
    *,
    cache_path: Path,
    upgrade: Sequence[str] | Callable[[UpdateAvailable], Sequence[Sequence[str]]],
    run: Callable[..., object] = subprocess.run,
) -> str | None:
    """Collect *started*, let *ask* pick an answer, then act on it.

    *upgrade* is one command, or a callable building the commands from the
    found release, run in order. Returns the answer acted on
    (``UPGRADE_FAILED`` when a command is missing or exits nonzero — the
    rest are then skipped), or None when there was nothing to offer. Ctrl+C
    inside *ask* is ``SKIP``. Never raises.
    """
    try:
        found = collect(started)
        if found is None:
            return None
        try:
            answer = ask(found)
        except KeyboardInterrupt:
            return SKIP
        if answer == SKIP_VERSION:
            skip(cache_path, found.latest)
        elif answer == UPDATE_NOW:
            steps = upgrade(found) if callable(upgrade) else [upgrade]
            for step in steps:
                try:
                    done = run(list(step), check=False)
                except OSError:
                    return UPGRADE_FAILED
                if getattr(done, "returncode", 1) != 0:
                    return UPGRADE_FAILED
        return answer
    except Exception:  # noqa: BLE001 - an update offer must never fail the command
        return None


def _bare(version: object) -> str | None:
    """*version* without a leading ``v`` (an old cache holds PyPI's ``0.10.0``, now tags)."""
    return version.removeprefix("v") if isinstance(version, str) else None


def _load_cache(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):    # RecursionError: a deeply nested file
        return {}
    if not isinstance(data, dict):
        return {}
    # Only a valid skipped version survives: a hostile file can't smuggle anything
    # (e.g. a 100k-deep dict that json.dumps can't write back), and old
    # checked_at/latest keys are dropped on the next write.
    skipped = data.get("skipped")
    return {"skipped": skipped} if isinstance(skipped, str) and _VERSION_RE.fullmatch(skipped) else {}


def _write_cache(path: Path, data: dict) -> None:
    # tmp + os.replace never follows a symlink at *path*; 0600 from creation.
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp.unlink(missing_ok=True)
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(data) + "\n")
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    except (OSError, RecursionError, ValueError):
        return
