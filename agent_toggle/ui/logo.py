"""The agent-toggle logo: the `help` banner and the top of the `config` menu.

Rows are embedded FIGlet literals (``ansi_shadow`` / ``pagga``), so there is no runtime
font dependency. Two tiers, widest first; :func:`pick` returns the first that fits, or
``()`` when none does (a narrow window gets no logo).

Modes (the ``logo`` setting): ``color`` (cyan -> blue -> magenta, the picker's choice /
type / pending colours), ``mono`` (one colour), ``animated`` (color plus a light sweep:
once when `help` prints to a terminal; on entry and after 5 s idle in the config menu, like aicp)
and ``off``. Colour is xterm-256, like claude's orange in theme.py; with colour off the
rows come back as plain glyphs. curses is never imported here: the config menu passes
its own module to :func:`curses_attrs`.
"""
from __future__ import annotations

import time
from collections.abc import Sequence

MODES: tuple[str, ...] = ("color", "mono", "animated", "off")
DEFAULT_MODE = "animated"

#: Widest first. Every row of a tier is the same width.
TIERS: tuple[tuple[str, ...], ...] = (
    (
        " █████╗  ██████╗ ███████╗███╗   ██╗████████╗████████╗ ██████╗  ██████╗  ██████╗ ██╗     ███████╗",
        "██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝╚══██╔══╝██╔═══██╗██╔════╝ ██╔════╝ ██║     ██╔════╝",
        "███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║█████╗██║   ██║   ██║██║  ███╗██║  ███╗██║     █████╗  ",
        "██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║╚════╝██║   ██║   ██║██║   ██║██║   ██║██║     ██╔══╝  ",
        "██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║      ██║   ╚██████╔╝╚██████╔╝╚██████╔╝███████╗███████╗",
        "╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝      ╚═╝    ╚═════╝  ╚═════╝  ╚═════╝ ╚══════╝╚══════╝",
    ),
    (
        "░█▀█░█▀▀░█▀▀░█▀█░▀█▀░░░░░▀█▀░█▀█░█▀▀░█▀▀░█░░░█▀▀",
        "░█▀█░█░█░█▀▀░█░█░░█░░▄▄▄░░█░░█░█░█░█░█░█░█░░░█▀▀",
        "░▀░▀░▀▀▀░▀▀▀░▀░▀░░▀░░░░░░░▀░░▀▀▀░▀▀▀░▀▀▀░▀▀▀░▀▀▀",
    ),
)

#: Letter face takes the gradient; every other glyph (box strokes, shades) is shadow.
FACE = frozenset("█▀▄▌▐")
#: xterm-256 cyan -> blue -> magenta (cube steps 0,5,5 .. 5,1,5).
RAMP = (51, 45, 39, 33, 63, 99, 135, 171, 207)
#: Ink ids beside the ramp indexes 0..8.
SHADOW, GLINT, GLOW = -1, -2, -3
_SGR = {SHADOW: "38;5;61", GLINT: "1;38;5;231", GLOW: "38;5;195",
        **{i: f"38;5;{c}" for i, c in enumerate(RAMP)}}
_MONO_FACE = 1
REACH = 2            # columns lit either side of the glint
PASS = 0.5           # seconds per sweep
FRAMES = 17          # frames per sweep (~0.03 s each)
IDLE = 5.0           # seconds without a key before the config menu sweeps again (aicp: _SHIMMER_EVERY)
RESET = "\x1b[0m"


def pick(columns: int, lines: int | None = None, reserve: int = 0) -> tuple[str, ...]:
    """The widest tier that fits *columns* (the last cell stays empty: filling it wraps)
    and, when *lines* is given, *lines* minus *reserve* rows of UI and one blank row."""
    for rows in TIERS:
        if columns >= len(rows[0]) + 1 and (lines is None or lines >= len(rows) + 1 + reserve):
            return rows
    return ()


def inks(rows: Sequence[str], mode: str, glint: int | None = None) -> list[list[tuple[str, int | None]]]:
    """Each row as runs of ``(text, ink)``: a ramp index, SHADOW, GLINT, GLOW, or None
    for spaces. ``glint`` is the sweep's column (None = at rest); shadow is never lit."""
    span = max(1, len(rows[0]) - 1)
    out = []
    for row in rows:
        runs: list[tuple[str, int | None]] = []
        for x, ch in enumerate(row):
            if ch == " ":
                ink = None
            elif ch not in FACE:
                ink = SHADOW
            elif glint is not None and abs(x - glint) <= REACH:
                ink = GLINT if x == glint else GLOW
            elif mode == "mono":
                ink = _MONO_FACE
            else:
                ink = round(x / span * (len(RAMP) - 1))
            if runs and runs[-1][1] == ink:
                runs[-1] = (runs[-1][0] + ch, ink)
            else:
                runs.append((ch, ink))
        out.append(runs)
    return out


def paint(rows: Sequence[str], mode: str, color: bool, glint: int | None = None,
          indent: int = 0) -> list[str]:
    """*rows* as ANSI lines indented by *indent*; plain glyphs when *color* is off."""
    pad = " " * indent
    if not color:
        return [pad + row.rstrip() for row in rows]
    return [pad + "".join(text if ink is None else f"\x1b[{_SGR[ink]}m{text}" for text, ink in runs)
            + RESET for runs in inks(rows, mode, glint)]


def sweep(width: int) -> range:
    """The glint's columns for one left-to-right pass of about FRAMES frames."""
    return range(-REACH, width + REACH, max(2, -(-(width + 2 * REACH) // FRAMES)))


def frames(rows: Sequence[str], indent: int = 0) -> list[list[str]]:
    """One sweep; the last frame is the static ``color`` paint, so it settles into it."""
    return ([paint(rows, "animated", True, x, indent) for x in sweep(len(rows[0]))]
            + [paint(rows, "animated", True, indent=indent)])


def _encodes(rows: Sequence[str], stream) -> bool:
    try:
        "".join(rows).encode(getattr(stream, "encoding", None) or "utf-8")
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def banner(stream, mode: str, color: bool, columns: int) -> None:
    """Print the logo and a blank line for `help` (nothing when off, too narrow, or the
    stream cannot encode the glyphs). Piped output gets plain glyphs; ``animated`` sweeps
    once, only on a colour TTY, rewriting just the logo's rows."""
    rows = () if mode == "off" else pick(columns)
    if not rows or not _encodes(rows, stream):
        return
    stream.write("\n".join(paint(rows, mode, color)) + "\n\n")
    stream.flush()
    if not (mode == "animated" and color and _tty(stream)):
        return
    up = f"\x1b[{len(rows) + 1}A\r"
    try:
        for lines in frames(rows):
            time.sleep(PASS / FRAMES)
            stream.write(up + "\n".join(lines) + "\n\n")
            stream.flush()
    finally:
        stream.write(RESET)     # Ctrl-C mid-frame must not leave the colour on


def _tty(stream) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def curses_attrs(curses, first_pair: int = 32) -> dict[int, int] | None:
    """ink -> curses attribute, from colour pairs *first_pair* onward. None when the
    terminal lacks 256 colours or the pairs (the menu then draws the logo plain)."""
    try:
        if curses.COLORS < 256 or curses.COLOR_PAIRS <= first_pair + len(_SGR):
            return None
        try:
            curses.use_default_colors()
            bg = -1
        except curses.error:
            bg = curses.COLOR_BLACK
        attrs = {}
        for n, (ink, sgr) in enumerate(_SGR.items()):
            pair = first_pair + n
            curses.init_pair(pair, int(sgr.rsplit(";", 1)[1]), bg)
            attrs[ink] = curses.color_pair(pair) | (curses.A_BOLD if sgr.startswith("1;") else 0)
        return attrs
    except (curses.error, AttributeError):
        return None


def draw_curses(win, y: int, x: int, rows: Sequence[str], mode: str,
                attrs: dict[int, int] | None, glint: int | None = None) -> None:
    """Paint *rows* at (y, x) on a curses window; plain when *attrs* is None. The caller
    has checked the logo fits, so cells never fall off the window."""
    import curses
    for dy, runs in enumerate(inks(rows, mode, glint)):
        cx = x
        for text, ink in runs:
            attr = 0 if attrs is None or ink is None else attrs[ink]
            try:
                win.addstr(y + dy, cx, text, attr)
            except curses.error:    # a resize raced the draw; next frame redraws
                return
            cx += len(text)

