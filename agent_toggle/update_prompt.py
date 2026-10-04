"""The update check's UI: start it before a command, offer the update after it.

    started = start()          # None when update_check is off; never raises
    ...                        # the command
    offer(started, json_mode=..., color=...)   # never raises, never touches stdout

On a terminal (stdin AND stderr are TTYs) and not under --json, :func:`ask` shows
a small arrow-key panel on stderr; elsewhere :func:`hint` prints two plain lines
on stderr. What each answer does lives in :func:`update_check.offer`.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

from . import __version__, fs, settings, update_check
from .i18n import t
from .output import use_color
from .ui import theme

DIST = "agent-toggle"
UPGRADE = ["uv", "tool", "upgrade", DIST]
RELEASE_NOTES = "https://github.com/weskao/agent-toggle/releases/tag/v%s"
ANSWERS = (update_check.UPDATE_NOW, update_check.SKIP, update_check.SKIP_VERSION)


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _clean(text: str) -> str:
    """Defence in depth: version text may come from the network; never print control chars."""
    return _CONTROL.sub("", text)


def cache_path() -> Path:
    return fs.state_dir() / "update-check.json"


def start() -> update_check.Started | None:
    """Kick off the PyPI check on a daemon thread, unless the `update_check` setting
    (env AGENT_TOGGLE_UPDATE_CHECK wins) is off. Never raises."""
    try:
        if not settings.get("update_check"):
            return None
        return update_check.start(DIST, __version__, cache_path=cache_path())
    except Exception:  # noqa: BLE001 - a hint must never fail the command
        return None


def is_interactive() -> bool:
    try:
        return sys.stdin.isatty() and sys.stderr.isatty()
    except (AttributeError, ValueError, OSError):
        return False


def _available(found: update_check.UpdateAvailable) -> str:
    return t("update.available", "agent-toggle %s is available (you have %s)",
             _clean(found.latest), _clean(found.current))


def hint(found: update_check.UpdateAvailable) -> str:
    """The `ask` off a terminal (CI, pipes, --json): two plain lines, never a block."""
    print(_available(found), file=sys.stderr)
    print("  " + " ".join(UPGRADE), file=sys.stderr)
    return update_check.SKIP


def _lines(found: update_check.UpdateAvailable, selected: int, color: bool,
           g: theme.Glyphs) -> list[str]:
    rows = (
        (t("update.now", "Update now"), " ".join(UPGRADE)),
        (t("update.skip", "Skip"), t("update.skip_detail", "ask again next run")),
        (t("update.skip_version", "Skip until next version"),
         t("update.skip_version_detail", "ask again once a newer version ships")),
    )
    width = max(theme.cell_width(name) for name, _ in rows)
    sparkle = "✨ " if g.unicode else ""
    lines = [theme.ansi("title", sparkle + _available(found), color)]
    for i, (name, detail) in enumerate(rows, 1):
        on = i == selected
        label = f"{g.pointer if on else ' '} {i}) {name}"
        label += " " * (width - theme.cell_width(name))
        lines.append("  " + theme.ansi("cursor" if on else "text", label, color)
                     + "  " + theme.ansi("muted", detail, color))
    keys = (t("update.keys", "↑↓ select · ⏎ confirm · q skip") if g.unicode
            else t("update.keys_ascii", "up/down select, enter confirm, q skip"))
    lines += ["  " + t("update.notes", "Release notes: %s", RELEASE_NOTES % _clean(found.latest).removeprefix("v")),
              "  " + theme.ansi("muted", keys, color)]
    return lines


def choose(found: update_check.UpdateAvailable, keys: Iterable[str], out, color: bool,
           g: theme.Glyphs) -> str:
    """Draw the panel on *out* and walk *keys* (up/down/enter/quit/1-3) to an answer.
    Running out of keys (EOF) is Skip."""
    selected = 1
    lines = _lines(found, selected, color, g)
    out.write("\n".join(lines) + "\n")
    for key in keys:
        if key == "quit":
            break
        if key in ("1", "2", "3"):
            return ANSWERS[int(key) - 1]
        if key == "enter":
            return ANSWERS[selected - 1]
        if key not in ("up", "down"):
            continue
        selected = (selected - (1 if key == "up" else -1) - 1) % len(ANSWERS) + 1
        out.write(f"\x1b[{len(lines)}A\x1b[J")
        lines = _lines(found, selected, color, g)
        out.write("\n".join(lines) + "\n")
        out.flush()
    return update_check.SKIP


def _posix_keys(fd: int) -> Iterator[str]:
    """Semantic keys from a cbreak-mode terminal; stops at EOF."""
    import select
    while True:
        ch = os.read(fd, 1)
        if not ch:
            return
        if ch == b"\x1b":
            seq = b""
            while len(seq) < 2 and select.select([fd], [], [], 0.05)[0]:
                seq += os.read(fd, 1)
            yield {b"[A": "up", b"OA": "up", b"[B": "down", b"OB": "down"}.get(seq, "quit")
        else:
            yield {b"\r": "enter", b"\n": "enter", b"k": "up", b"j": "down",
                   b"q": "quit", b"Q": "quit"}.get(ch, ch.decode("ascii", "replace"))


def _typed_keys(stream, out) -> Iterator[str]:
    """The numbered fallback: one typed line per answer ('' = the highlighted row)."""
    out.write(t("update.choose", "Choose 1-3 (Enter = 1, q = skip): "))
    out.flush()
    line = stream.readline()
    if not line:
        return
    line = line.strip().lower()
    yield "enter" if not line else ("quit" if line == "q" else line)


def _flush_input(termios, fd: int) -> None:
    try:
        termios.tcflush(fd, termios.TCIFLUSH)
    except (AttributeError, OSError):   # no tcflush on this platform / not a real tty
        pass


def ask(found: update_check.UpdateAvailable, color_mode: str = "auto") -> str:
    """The `ask` on a terminal: the arrow-key panel on stderr (cbreak mode, so Ctrl-C
    stays a signal -- update_check.offer reads it as Skip); typed-number fallback
    where raw keys are unavailable."""
    out = sys.stderr
    color = use_color(out, color_mode)
    g = theme.get_glyphs(out)
    try:
        import termios
        import tty
        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
    except Exception:  # noqa: BLE001 - no termios / not a real tty (termios.error): typed input
        return choose(found, _typed_keys(sys.stdin, out), out, color, g)
    try:
        tty.setcbreak(fd, termios.TCSANOW)
        _flush_input(termios, fd)       # typeahead must not answer the prompt
        return choose(found, _posix_keys(fd), out, color, g)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        _flush_input(termios, fd)       # and the tail of a multi-byte escape must not leak out


def offer(started: update_check.Started | None, *, json_mode: bool = False,
          color_mode: str = "auto") -> str | None:
    """After the command: ask (TTY, not --json) or hint about a newer release. Writes to
    stderr only and never raises, so neither stdout nor the exit code can change."""
    if started is None:
        return None
    if is_interactive() and not json_mode:
        def asker(found):
            return ask(found, color_mode)
    else:
        asker = hint
    def run(cmd, **kw):                 # uv's output must never reach stdout
        return subprocess.run(cmd, stdout=sys.stderr, **kw)
    answer = update_check.offer(started, asker, cache_path=cache_path(), upgrade=UPGRADE, run=run)
    if answer == update_check.UPGRADE_FAILED:
        try:
            mark = theme.ansi("warning", "⚠" if theme.get_glyphs(sys.stderr).unicode else "!",
                              use_color(sys.stderr, color_mode))
            print(mark + " " + t("update.failed", "upgrade did not finish — run it yourself: %s",
                                 " ".join(UPGRADE)), file=sys.stderr)
        except Exception:  # noqa: BLE001, S110 - a hint must never fail the command
            pass
    return answer
