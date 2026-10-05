"""A loading spinner on stderr, same look and rules as ``~/scripts/lib/spinner.sh``.

Drawn only when stderr is a TTY and NO_COLOR is unset; otherwise ``spinner()`` does nothing,
so pipes, CI and tests stay silent. Non-UTF-8 locales get ASCII frames.
"""
from __future__ import annotations

import contextlib
import os
import sys
import threading

BRAILLE, ASCII = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏", "|/-\\"
INTERVAL = 0.08
CYAN, RESET = "\x1b[38;5;87m", "\x1b[0m"


def enabled(stream=None) -> bool:
    stream = sys.stderr if stream is None else stream
    try:
        return stream.isatty() and not os.environ.get("NO_COLOR")
    except (AttributeError, ValueError, OSError):
        return False


@contextlib.contextmanager
def spinner(message: str, stream=None):
    stream = sys.stderr if stream is None else stream
    if not enabled(stream):
        yield
        return
    try:
        frames = BRAILLE if "utf" in (getattr(stream, "encoding", "") or "").lower() else ASCII
    except Exception:  # noqa: BLE001 -- a odd stream just gets the safe frames
        frames = ASCII
    stop = threading.Event()

    def spin() -> None:
        i = 0
        while not stop.is_set():
            stream.write(f"\r{CYAN}{frames[i % len(frames)]}{RESET} {message}\x1b[K")
            stream.flush()
            i += 1
            stop.wait(INTERVAL)

    t = threading.Thread(target=spin, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join()
        stream.write("\r\x1b[K")
        stream.flush()
