"""Interactive terminal picker for agent-toggle.

Built on curses (stdlib) so there is nothing to install. The checkbox shows
the ENABLED state: `[x]` is live, `[ ]` is parked. Unticking something disables
it, ticking it back enables it.

Nothing is applied while the picker is open. Changes are staged, the screen is
torn down, and only then are the real operations run -- so their output (which
companion files moved, which were kept because they are shared) is readable
instead of fighting the curses display for the terminal.
"""
from __future__ import annotations

import curses

from .. import cost

HELP = "Tab tick  Enter apply  Esc cancel  s sort  h/t filter  ? keys  / type to filter"
LONG_HELP = (
    "Keys",
    "",
    "  Up/Down, PgUp/PgDn   move",
    "  Tab                  tick / untick the row (unticked = parked)",
    "  Enter                apply the staged changes (with --dry-run: show the plan)",
    "  Esc, Ctrl-C          cancel; nothing is changed",
    "  s                    cycle sort: name -> cost (biggest first)",
    "  h                    cycle the harness filter",
    "  t                    cycle the type filter",
    "  ?                    this screen",
    "  /                    start typing a filter (needed to type a filter that",
    "                       begins with s, h, t or ?; any other letter just types)",
    "  Backspace, Ctrl-U    edit / clear the filter",
    "",
    "Cost is ~tokens loaded at session start (chars/4); a parked row shows",
    "(N) = what restoring it would load. A shared dir is one row, filed under",
    "the harness that owns it.",
    "",
    "any key to close",
)
SORTS = ("name", "cost")


class Row:
    __slots__ = ("harness", "type", "name", "enabled", "staged", "tokens", "would_save",
                 "shared")

    def __init__(self, harness: str, type_: str, name: str, enabled: bool,
                 tokens: int = 0, would_save: int = 0, shared: tuple[str, ...] = ()):
        self.harness, self.type, self.name = harness, type_, name
        self.enabled = enabled
        self.staged = enabled          # what the user has ticked so far
        self.tokens, self.would_save, self.shared = tokens, would_save, shared

    @property
    def changed(self) -> bool:
        return self.staged != self.enabled

    @property
    def label(self) -> str:
        return f"{self.harness}/{self.type}/{self.name}"

    @property
    def cost_cell(self) -> str:
        return f"{self.tokens}" if self.enabled else f"({self.would_save})"


# ------------------------------------------------------------- data gathering

def collect(state: dict, harnesses: dict, warn=lambda m: None,
            plugins: bool = True) -> list[Row]:
    """Every togglable resource on this machine, live and parked, plugins included.

    A dir shared by several harnesses is one row, filed under its owner, so staging
    it cannot also be staged (and then fail) under the other harness.
    """
    rows = [Row(i.harness, i.type, i.name, i.enabled, i.tokens, i.would_save, i.shared_with)
            for i in cost.inventory(state, harnesses, warn, plugins)]
    rows.sort(key=lambda r: (r.harness, r.type, r.name))
    return rows


def match(rows: list[Row], query: str) -> list[Row]:
    """Case-insensitive AND over whitespace-separated terms."""
    terms = query.lower().split()
    if not terms:
        return rows
    return [r for r in rows if all(t in r.label.lower() for t in terms)]


def visible(rows: list[Row], query: str = "", harness: str = "all", type_: str = "all",
            sort: str = "name") -> list[Row]:
    """The rows on screen: filter chips, then the text query, then the sort."""
    shown = [r for r in rows
             if (harness == "all" or harness in (r.harness, *r.shared))
             and (type_ == "all" or r.type == type_)]
    shown = match(shown, query)
    if sort == "cost":
        shown = sorted(shown, key=lambda r: (-r.tokens, -r.would_save, r.harness, r.type, r.name))
    return shown


def cycle(options: tuple[str, ...] | list[str], current: str) -> str:
    """The option after `current` (wraps; an unknown current restarts at the first)."""
    return options[(options.index(current) + 1) % len(options)] if current in options \
        else options[0]


# -------------------------------------------------------------------- drawing

# curses colour-pair ids; 0 is the terminal default.
P_HARNESS, P_TYPE, P_LIVE, P_PARKED, P_PENDING = 1, 2, 3, 4, 5


def state_pair(r: Row) -> int:
    """Pair id for a row's state cell: staged change > ticked (live) > unticked (parked)."""
    return P_PENDING if r.changed else P_LIVE if r.staged else P_PARKED


def init_colors(color: bool) -> bool:
    """Set up the pairs; True only when colours are wanted AND the terminal has them."""
    if not color:
        return False
    try:
        if not curses.has_colors():
            return False
        curses.start_color()
        try:
            curses.use_default_colors()
            bg = -1
        except curses.error:
            bg = curses.COLOR_BLACK
        for pid, fg in ((P_HARNESS, curses.COLOR_CYAN), (P_TYPE, curses.COLOR_BLUE),
                        (P_LIVE, curses.COLOR_GREEN), (P_PARKED, curses.COLOR_YELLOW),
                        (P_PENDING, curses.COLOR_MAGENTA)):
            curses.init_pair(pid, fg, bg)
    except curses.error:
        return False
    return True


def draw(win, rows: list[Row], query: str, cur: int, top: int, pending: int,
         chips: str = "", color: bool = False) -> None:
    win.erase()
    height, width = win.getmaxyx()
    body = max(1, height - 3)

    header = f" filter: {query}█" if query else " filter: (type to narrow, ? for keys)"
    win.addnstr(0, 0, header.ljust(width - 1), width - 1, curses.A_BOLD)

    for i in range(body):
        idx = top + i
        if idx >= len(rows):
            break
        r = rows[idx]
        box = "[x]" if r.staged else "[ ]"
        mark = "*" if r.changed else " "
        also = f"  (+{','.join(r.shared)})" if r.shared else ""
        parts = (f" {mark}{box} {r.cost_cell:>7}  ", f"{r.harness:<9}", f"{r.type:<8}",
                 f"{r.name}{also}")
        line = "".join(parts)
        attr = curses.A_REVERSE if idx == cur else curses.A_NORMAL
        if r.changed:
            attr |= curses.A_BOLD
        if not color:
            win.addnstr(1 + i, 0, line.ljust(width - 1), width - 1, attr)
        elif idx == cur:                # one reversed bar, tinted by state
            win.addnstr(1 + i, 0, line.ljust(width - 1), width - 1,
                        attr | curses.color_pair(state_pair(r)))
        else:
            x = 0
            for n, (text, pid) in enumerate(zip(parts, (state_pair(r), P_HARNESS, P_TYPE, 0))):
                if x >= width - 1:
                    break
                if n == len(parts) - 1:
                    text = text.ljust(width - 1 - x)
                win.addnstr(1 + i, x, text, width - 1 - x, attr | curses.color_pair(pid))
                x += len(text)

    count = f" {len(rows)} shown  |  ~{sum(r.tokens for r in rows)} tok  |  {chips}"
    if pending:
        count += f"  |  {pending} staged -- Enter to apply"
    win.addnstr(height - 2, 0, count.ljust(width - 1), width - 1, curses.A_BOLD)
    win.addnstr(height - 1, 0, " " + HELP.ljust(width - 2), width - 1, curses.A_DIM)
    win.noutrefresh()
    curses.doupdate()


def show_help(win) -> None:
    win.erase()
    height, width = win.getmaxyx()
    for y, text in enumerate(LONG_HELP[:height]):
        win.addnstr(y, 0, text, width - 1)
    win.refresh()
    try:
        win.get_wch()
    except (curses.error, KeyboardInterrupt):
        pass


def loop(win, rows: list[Row], color: bool = False) -> list[Row] | None:
    color = init_colors(color)
    curses.curs_set(0)
    win.keypad(True)
    query, cur, top = "", 0, 0
    typing = False                     # True after `/`: s/h/t/? are then letters, not commands
    harness, type_, sort = "all", "all", "name"
    harness_opts = ["all", *sorted({n for r in rows for n in (r.harness, *r.shared)})]
    type_opts = ["all", *sorted({r.type for r in rows})]

    while True:
        shown = visible(rows, query, harness, type_, sort)
        cur = max(0, min(cur, len(shown) - 1))
        height = max(1, win.getmaxyx()[0] - 3)
        top = max(0, min(top, max(0, len(shown) - height)))
        if cur < top:
            top = cur
        elif cur >= top + height:
            top = cur - height + 1

        pending = sum(1 for r in rows if r.changed)
        draw(win, shown, query, cur, top, pending,
             f"harness:{harness} type:{type_} sort:{sort}", color)

        try:
            key = win.get_wch()
        except (curses.error, KeyboardInterrupt):
            return None

        if key in ("\x1b", "\x03"):                       # Esc / Ctrl-C
            return None
        if key in ("\n", "\r", curses.KEY_ENTER):
            return [r for r in rows if r.changed]
        if key == "\t" and shown:
            shown[cur].staged = not shown[cur].staged
            cur += 1
        elif key in (curses.KEY_UP, "\x10"):
            cur -= 1
        elif key in (curses.KEY_DOWN, "\x0e"):
            cur += 1
        elif key == curses.KEY_NPAGE:
            cur += height
        elif key == curses.KEY_PPAGE:
            cur -= height
        elif key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
            query = query[:-1]
            typing = typing and bool(query)
            cur = 0
        elif key == "\x15":                               # Ctrl-U
            query, typing, cur = "", False, 0
        elif not (typing or query) and key in ("s", "h", "t", "?", "/"):
            if key == "s":
                sort = cycle(SORTS, sort)
            elif key == "h":
                harness = cycle(harness_opts, harness)
            elif key == "t":
                type_ = cycle(type_opts, type_)
            elif key == "?":
                show_help(win)
            else:
                typing = True
            cur = 0
        elif isinstance(key, str) and key.isprintable():
            query += key
            cur = 0
        # KEY_RESIZE and anything else just redraw


def pick(state: dict, harnesses: dict, plugins: bool = True, color: bool = False) -> list[Row] | None:
    """`plugins=False` skips the `claude plugin list` call (a dry run never shells out)."""
    notes: list[str] = []
    rows = collect(state, harnesses, notes.append, plugins)
    if not rows:
        print("nothing to show")
        return None
    try:
        return curses.wrapper(loop, rows, color)
    finally:
        for n in notes:                 # after curses is torn down, where they are readable
            print(f"WARNING  {n}")
