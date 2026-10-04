"""One look for the curses picker, the curses config menu and the styled help.

Importable everywhere: there is NO module-level ``import curses`` (Windows
without windows-curses, numbered fallbacks and the plain CLI all import this).
curses is imported lazily, only inside :func:`init_curses_colors` and
:func:`draw_segments`.

Public API (frozen): ``ROLES``, ``Glyphs``, ``UNICODE``, ``ASCII``,
``supports_unicode``, ``get_glyphs``, ``encodable``, ``Palette``, ``init_curses_colors``,
``ansi``, ``cell_width``, ``truncate``, ``key_chips``, ``tab_bar``,
``title_bar``, ``cost_bar``, ``render_ansi``, ``draw_segments``.

Styling is semantic: callers name a *role* ("live", "pending", ...), never a
raw colour pair. The pure helpers return segment lists ``[(text, role)]`` whose
cell width is exactly ``width`` (so a caller can paint a full row), and which
are testable with no terminal. Wide (CJK) characters count as two cells.
"""
from __future__ import annotations

import os
import sys
import unicodedata
from dataclasses import dataclass

Segment = tuple[str, str]

#: Every role a segment may carry. "text" is the unstyled default; "on",
#: "off" and "changed" are aliases of live / parked / pending.
ROLES = (
    "text", "title", "harness", "type", "live", "parked", "pending", "choice",
    "warning", "error", "accent", "muted", "cursor",
    "chip_key", "chip_label", "tab_active", "tab_inactive",
)
ALIASES = {"on": "live", "off": "parked", "changed": "pending", "dim": "muted"}


# -- glyphs -----------------------------------------------------------------

@dataclass(frozen=True)
class Glyphs:
    live: str
    parked: str
    pointer: str
    bar: tuple[str, ...]      # 1/8 .. 7/8 of a cell; the full cell is `full`
    full: str
    h: str
    v: str
    tl: str
    tr: str
    bl: str
    br: str
    ellipsis: str
    unicode: bool


UNICODE = Glyphs("●", "○", "›", tuple("▏▎▍▌▋▊▉"), "█", "─", "│", "╭", "╮", "╰", "╯", "…", True)
ASCII = Glyphs("*", "o", ">", ("#",) * 7, "#", "-", "|", "+", "+", "+", "+", "...", False)

_PROBE = "".join(UNICODE.bar) + UNICODE.full + "●○›─│╭╮╰╯…"


def supports_unicode(stream=None, env=None) -> bool:
    """False when TERM=dumb or the stream's encoding cannot encode the glyph set."""
    env = os.environ if env is None else env
    if env.get("TERM") == "dumb":
        return False
    stream = sys.stdout if stream is None else stream
    encoding = getattr(stream, "encoding", None) or "ascii"
    try:
        _PROBE.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def get_glyphs(stream=None, env=None) -> Glyphs:
    return UNICODE if supports_unicode(stream, env) else ASCII


_ASCII_PUNCT = str.maketrans({"—": "-", "·": "-", "…": "..."})


def encodable(text: str, stream=None) -> str:
    """*text* as *stream* (default stdout) can write it: common punctuation spelled in
    ASCII, anything else it cannot encode replaced (an ASCII / cp1252 pipe must not crash
    on a dash or a zh-TW message)."""
    stream = sys.stdout if stream is None else stream
    enc = getattr(stream, "encoding", None) or "utf-8"
    try:
        text.encode(enc)
        return text
    except UnicodeEncodeError:
        return text.translate(_ASCII_PUNCT).encode(enc, "replace").decode(enc)
    except LookupError:
        return text


# -- cell widths ------------------------------------------------------------

def _cw(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def cell_width(text: str) -> int:
    """Terminal cells *text* occupies (CJK = 2, combining marks = 0)."""
    return sum(_cw(ch) for ch in text)


def truncate(text: str, width: int, ellipsis: str = "") -> str:
    """*text* cut to at most *width* cells; *ellipsis* replaces the tail when it was cut."""
    if width <= 0:
        return ""
    if cell_width(text) <= width:
        return text
    room = width - cell_width(ellipsis)
    if room < 0:
        ellipsis, room = "", width
    out, used = [], 0
    for ch in text:
        w = _cw(ch)
        if used + w > room:
            break
        out.append(ch)
        used += w
    return "".join(out) + ellipsis


# -- palette ----------------------------------------------------------------

# role -> (curses colour name or None, attrs); attrs are curses A_* names.
# Colour mode: foreground colour + attrs. Mono mode: attrs only (bold/reverse/dim).
_COLOUR = {
    "text": (None, ()),
    "title": (None, ("BOLD",)),
    "harness": ("CYAN", ("BOLD",)),
    "type": ("BLUE", ()),
    "live": ("GREEN", ()),
    "parked": ("YELLOW", ()),
    "pending": ("MAGENTA", ()),
    "choice": ("CYAN", ()),
    "warning": ("YELLOW", ("BOLD",)),
    "error": ("RED", ("BOLD",)),
    "accent": ("BLUE", ("BOLD",)),
    "muted": (None, ("DIM",)),
    "cursor": (None, ("BOLD", "REVERSE")),
    "chip_key": ("CYAN", ("BOLD",)),
    "chip_label": (None, ("DIM",)),
    "tab_active": (None, ("BOLD", "REVERSE")),
    "tab_inactive": (None, ("DIM",)),
}
_MONO = {
    "text": (), "title": ("BOLD",), "harness": ("BOLD",), "type": (), "live": ("BOLD",),
    "parked": ("DIM",), "pending": ("BOLD",), "choice": (), "warning": ("BOLD",),
    "error": ("BOLD", "REVERSE"), "accent": ("BOLD",), "muted": ("DIM",),
    "cursor": ("REVERSE",), "chip_key": ("BOLD",), "chip_label": ("DIM",),
    "tab_active": ("BOLD", "REVERSE"), "tab_inactive": ("DIM",),
}


class Palette:
    """role -> curses attribute. ``mono`` is True when no colour pairs are in use."""

    def __init__(self, attrs: dict[str, int], mono: bool) -> None:
        self.attrs = attrs
        self.mono = mono

    def attr(self, role: str) -> int:
        """Curses attribute for *role* (aliases allowed); unknown roles are unstyled (0)."""
        return self.attrs.get(ALIASES.get(role, role), 0)

    __getitem__ = attr


def _attrs(curses, names) -> int:
    value = 0
    for name in names:
        value |= getattr(curses, "A_" + name)
    return value


def init_curses_colors(enabled: bool, curses_mod=None) -> Palette:
    """Map every role to a curses attribute; call once after ``initscr``.

    *enabled* is the caller's colour decision (``output.use_color``: --color,
    NO_COLOR, TERM...). When it is False, or the terminal has no colour, or
    pair setup fails, the mono mapping (bold/reverse/dim only) is used.
    *curses_mod* is for tests; leave it None.
    """
    curses = curses_mod
    if curses is None:
        import curses
    mono = Palette({r: _attrs(curses, a) for r, a in _MONO.items()}, True)
    try:
        if not enabled or not curses.has_colors():
            return mono
        curses.start_color()
        try:
            curses.use_default_colors()
            bg = -1
        except curses.error:
            bg = curses.COLOR_BLACK
        attrs: dict[str, int] = {}
        pairs: dict[str, int] = {}
        for role, (colour, names) in _COLOUR.items():
            attr = _attrs(curses, names)
            if colour:
                if colour not in pairs:
                    pairs[colour] = len(pairs) + 1
                    curses.init_pair(pairs[colour], getattr(curses, "COLOR_" + colour), bg)
                attr |= curses.color_pair(pairs[colour])
            attrs[role] = attr
        return Palette(attrs, False)
    except curses.error:
        return mono


# -- ANSI (non-curses text: help, prompts) ----------------------------------

_SGR = {
    "title": "1", "harness": "1;36", "type": "34", "live": "32", "parked": "33",
    "pending": "35", "choice": "36", "warning": "1;33", "error": "1;31", "accent": "1;34",
    "muted": "2", "cursor": "1;7", "chip_key": "1;36", "chip_label": "2",
    "tab_active": "1;7", "tab_inactive": "2",
}


def ansi(role: str, text: str, enabled: bool) -> str:
    """*text* wrapped in the role's ANSI escape when *enabled*; verbatim otherwise."""
    code = _SGR.get(ALIASES.get(role, role))
    if not enabled or not code or not text:
        return text
    return f"\x1b[{code}m{text}\x1b[0m"


def render_ansi(segments: list[Segment], enabled: bool) -> str:
    return "".join(ansi(role, text, enabled) for text, role in segments)


def draw_segments(win, y: int, x: int, segments: list[Segment], palette: Palette) -> int:
    """Paint *segments* on a curses window at (y, x); returns the next free column.
    Cells that fall off the window are dropped, never raised."""
    import curses
    rows, w = win.getmaxyx()
    for text, role in segments:
        room = w - x - (1 if y >= rows - 1 else 0)      # the last cell of the last row can't be set
        if room <= 0:
            break
        text = truncate(text, room)
        try:
            win.addstr(y, x, text, palette.attr(role))
        except curses.error:
            break
        x += cell_width(text)
    return x


# -- pure segment builders --------------------------------------------------

def _fit(segments: list[Segment], width: int) -> list[Segment]:
    """Clip to *width* cells, then pad with unstyled spaces to exactly *width*."""
    if width <= 0:
        return []
    out, used = [], 0
    for text, role in segments:
        text = truncate(text, width - used)
        if text:
            out.append((text, role))
            used += cell_width(text)
    if used < width:
        out.append((" " * (width - used), "text"))
    return out


def key_chips(pairs: list[tuple[str, str]], width: int, g: Glyphs = UNICODE) -> list[Segment]:
    """``[(key, label), ...]`` as ``key label  key label``. Whole chips only: one that
    does not fit is dropped (with every later one), never half-drawn."""
    out: list[Segment] = []
    used = 0
    for key, label in pairs:
        gap = 2 if out else 0
        need = gap + cell_width(key) + 1 + cell_width(label)
        if used + need > width:
            break
        if gap:
            out.append(("  ", "text"))
        out += [(key, "chip_key"), (" " + label, "chip_label")]
        used += need
    return _fit(out, width)


def tab_bar(tabs: list[str], active: int, width: int, g: Glyphs = UNICODE) -> list[Segment]:
    """`` one │ two │ three `` with the *active* index highlighted; clipped to *width*."""
    out: list[Segment] = []
    for i, label in enumerate(tabs):
        if i:
            out.append((g.v, "muted"))
        out.append((f" {label} ", "tab_active" if i == active else "tab_inactive"))
    return _fit(out, width)


def title_bar(left: str, right: str, width: int, g: Glyphs = UNICODE) -> list[Segment]:
    """*left* (title) and *right* (muted) on one row. The left text is ellipsised first,
    the right text dropped only when nothing sensible of the left would remain."""
    if width <= 0:
        return []
    right_w = cell_width(right)
    if right and right_w + 2 < width:
        left = truncate(left, width - right_w - 1, g.ellipsis)
        gap = width - cell_width(left) - right_w
        return _fit([(left, "title"), (" " * gap, "text"), (right, "muted")], width)
    return _fit([(truncate(left, width, g.ellipsis), "title")], width)


def cost_bar(value: float, max_value: float, width: int, g: Glyphs = UNICODE) -> list[Segment]:
    """A horizontal bar *width* cells wide, ``value / max_value`` full (eighth-cell
    resolution in Unicode). Any value > 0 shows at least a sliver."""
    if width <= 0:
        return []
    eighths = 0
    if max_value > 0 and value > 0:
        eighths = max(1, min(width * 8, round(min(value, max_value) / max_value * width * 8)))
    full, rem = divmod(eighths, 8)
    if g.unicode:
        text = g.full * full + (g.bar[rem - 1] if rem else "")
    else:
        text = g.full * (full + (1 if rem >= 4 or (rem and not full) else 0))
    return _fit([(text, "accent")], width)
