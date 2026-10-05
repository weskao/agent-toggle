"""Interactive terminal picker for agent-toggle.

Built on curses (stdlib) so there is nothing to install. Each row shows its state
glyph: ● live, ○ parked (ASCII: * and o). Toggling a row stages the opposite state;
a staged row carries a +/- marker (+ = will be restored, - = will be parked).

Nothing is applied while the picker is open. Changes are staged, the screen is
torn down, and only then are the real operations run -- so their output (which
companion files moved, which were kept because they are shared) is readable
instead of fighting the curses display for the terminal.

Layout: title bar, harness tabs, filter line, the list grouped by type (with a
detail pane for the cursor row when the window is wide), a status line and key
chips. All styling goes through ``theme`` roles, so monochrome stays usable.
"""
from __future__ import annotations

import curses
import sys
from pathlib import Path

from .. import __version__
from ..spinner import spinner
from ..i18n import t
from . import theme
from .model import (
    SORTS,
    Row,
    collect,
    cycle,
    fmt_tokens,
    grouped,
    prefs,
    profile_command,
    profile_listing,
    short_path,
    type_label,
    strict,
    subsequence,
    visible,
)

DETAIL_MIN = 100            # narrower than this: list only, no detail pane
BODY_TOP = 3                # title, tabs, filter line
BACKSPACE = (curses.KEY_BACKSPACE, "\x7f", "\b")
ENTER = ("\n", "\r", curses.KEY_ENTER)
COMMANDS = ("s", "t", "h", "p", "?", "/", *"0123456789")


def sort_label(sort: str) -> str:
    return t("picker.sort.cost", "cost") if sort == "cost" else t("picker.sort.name", "name")


class View:
    """What the screen shows; `loop` changes it, `draw` paints it."""

    def __init__(self, rows: list[Row], names: list[str], harness: str = "all",
                 type_: str = "all", sort: str = "name") -> None:
        self.rows = rows
        self.tabs = ["all", *names]
        self.types = ["all", *(ty for ty, _ in grouped(rows))]
        self.harness = harness if harness in self.tabs else "all"
        self.type_ = type_ if type_ in self.types else "all"
        self.sort = sort if sort in SORTS else "name"
        self.query, self.typing, self.note = "", False, ""
        self.fuzzy = False                 # the shown rows matched by letters in order, not text
        self.cur = self.top = 0
        self.page = 1
        self.top_cost = max((r.tokens or r.would_save for r in rows), default=0)

    @property
    def filtering(self) -> bool:
        """A filter is being typed: printable keys are text, not commands."""
        return self.typing or bool(self.query)

    def shown(self) -> list[Row]:
        """The visible rows in screen order (grouped by type)."""
        found = visible(self.rows, self.query, self.harness, self.type_, self.sort)
        terms = self.query.lower().split()
        # fuzzy results are all-or-nothing: if the first row is no plain match, none is
        self.fuzzy = bool(found and terms and not strict(found[0], terms))
        return [r for _, grp in grouped(found) for r in grp]

    def tab(self, step: int = 0, to: int | None = None) -> None:
        i = self.tabs.index(self.harness)
        i = (i + step) % len(self.tabs) if to is None else to
        if 0 <= i < len(self.tabs):
            self.harness, self.cur = self.tabs[i], 0


# -------------------------------------------------------------------- drawing

def pad(text: str, cells: int) -> str:
    """`text` space-padded to `cells` terminal cells (CJK-safe ljust)."""
    return text + " " * max(0, cells - theme.cell_width(text))


def _sep(g: theme.Glyphs) -> str:
    return " · " if g.unicode else " | "


def summary(v: View, g: theme.Glyphs) -> str:
    parts = [t("picker.summary.resources", "%s resources", len(v.rows)),
             t("picker.summary.live", "~%s tok live", fmt_tokens(sum(r.tokens for r in v.rows)))]
    staged = [r for r in v.rows if r.changed]
    if staged:
        delta = sum(r.would_save if r.staged else -r.tokens for r in staged)
        sign = "+" if delta >= 0 else "-"
        parts.append(t("picker.summary.staged", "%s staged (%s tok)", len(staged),
                       sign + fmt_tokens(abs(delta))))
    return _sep(g).join(parts)


def highlight(name: str, query: str, role: str, fuzzy: bool = False) -> list[theme.Segment]:
    """`name` with the query terms' matches in the accent role (fuzzy: the letters found)."""
    low, hit = name.lower(), [False] * len(name)
    for term in query.lower().split():
        if fuzzy:
            for i in subsequence(term, low) or ():
                hit[i] = True
            continue
        start = low.find(term)
        while start >= 0:
            hit[start:start + len(term)] = [True] * len(term)
            start = low.find(term, start + 1)
    out: list[theme.Segment] = []
    for ch, h in zip(name, hit):
        r = "accent" if h else role
        if out and out[-1][1] == r:
            out[-1] = (out[-1][0] + ch, r)
        else:
            out.append((ch, r))
    return out


def row_segments(r: Row, width: int, v: View, g: theme.Glyphs, is_cur: bool,
                 any_shared: bool) -> list[theme.Segment]:
    """One list row, exactly `width` cells: pointer, glyph, marker, name, [harness],
    cost, [bar], [+shared]. Optional columns drop as the width shrinks."""
    head = [(g.pointer if is_cur else " ", "accent"), (" ", "text"),
            (g.live, "live") if r.staged else (g.parked, "parked"), (" ", "text"),
            (("+" if r.staged else "-") if r.changed else " ", "pending"), (" ", "text")]
    tail: list[theme.Segment] = []
    if v.harness == "all" and width >= 60:
        tail.append((" " + pad(theme.truncate(r.harness, 8), 8), theme.harness_role(r.harness)))
    cell = fmt_tokens(r.tokens) if r.enabled else f"({fmt_tokens(r.would_save)})"
    tail.append((cell.rjust(8), "text" if r.enabled else "muted"))
    if width >= 56:
        bar = theme.cost_bar(r.tokens or r.would_save, v.top_cost, 5, g)
        tail += [(" ", "text"), *((s, "accent" if r.enabled else "muted") for s, _ in bar)]
    if any_shared and width >= 70:
        badge = t("picker.badge.shared", "+shared") if r.shared else ""
        tail.append((" " + pad(theme.truncate(badge, 8), 8), "choice"))
    room = width - 6 - sum(theme.cell_width(s) for s, _ in tail)
    if room < 4:                                     # very narrow: name and state only
        tail, room = [], width - 6
    name = theme.truncate(r.name, room, g.ellipsis)
    role = "pending" if r.changed else "text" if r.staged else "muted"
    body = [*highlight(name, v.query, role, v.fuzzy), (" " * (room - theme.cell_width(name)), "text"), *tail]
    if is_cur:
        body = [(s, "cursor") for s, _ in body]
    return head + body


def heading(type_: str, count: int, width: int, g: theme.Glyphs) -> list[theme.Segment]:
    label = f" {type_label(type_)} "
    rest = width - theme.cell_width(label) - len(str(count)) - 1
    return [(label, "title"), (str(count), "muted"), (" " + g.h * max(0, rest), "muted")]


def detail_lines(r: Row, width: int, g: theme.Glyphs) -> list[list[theme.Segment]]:
    """The detail pane for the cursor row: one segment list per screen line."""
    lab = 11
    val = max(1, width - lab)
    out = [[(theme.truncate(r.name, width, g.ellipsis), "title")], []]

    def field(label: str, text: str, role: str = "text") -> None:
        for n in range(0, max(1, len(text)), val):          # a long path wraps
            out.append([(pad(label if n == 0 else "", lab), "muted"), (text[n:n + val], role)])

    state = t("picker.state.live", "live") if r.enabled else t("picker.state.parked", "parked")
    field(t("picker.d.harness", "harness"), r.harness, theme.harness_role(r.harness))
    field(t("picker.d.type", "type"), r.type, "type")
    field(t("picker.d.state", "state"),
          f"{g.live if r.enabled else g.parked} {state}", "live" if r.enabled else "parked")
    if r.changed:
        to = t("picker.state.live", "live") if r.staged else t("picker.state.parked", "parked")
        field("", f"{t('picker.d.staged', 'staged')} {'→' if g.unicode else '->'} {to}",
              "pending")
    if r.path:
        field(t("picker.d.path", "path"), short_path(r.path))
    field(t("picker.d.cost", "cost"),
          t("picker.d.cost_live", "~%s tok at session start", fmt_tokens(r.tokens)) if r.enabled
          else t("picker.d.cost_parked", "0 now; ~%s tok if restored", fmt_tokens(r.would_save)))
    if r.enabled and r.tokens:
        field(t("picker.d.saves", "would save"), f"~{fmt_tokens(r.tokens)} tok", "live")
    if r.basis:
        field("", r.basis, "muted")
    if r.shared:
        field(t("picker.d.shared", "shared with"), ", ".join(r.shared), "choice")
    if r.since:
        field(t("picker.d.since", "since"), r.since[:19].replace("T", " "))
    if r.mechanism:
        field(t("picker.d.mechanism", "mechanism"), r.mechanism)
    return out


def chips(v: View, g: theme.Glyphs) -> list[tuple[str, str]]:
    ud, lr = ("↑↓", "←→") if g.unicode else ("Up/Dn", "Lt/Rt")
    if v.typing:
        return [("Tab", t("picker.chip.toggle", "toggle")), ("Enter", t("picker.chip.apply", "apply")),
                ("Esc", t("picker.chip.cancel", "cancel")), ("Bksp", t("picker.chip.edit", "edit")),
                ("^U", t("picker.chip.clear", "clear filter")), (ud, t("picker.chip.move", "move"))]
    return [("Space", t("picker.chip.toggle", "toggle")), ("Enter", t("picker.chip.apply", "apply")),
            ("Esc", t("picker.chip.cancel", "cancel")), ("?", t("picker.chip.help", "help")),
            ("/", t("picker.chip.filter", "search")), (lr, t("picker.chip.harness", "harness")),
            ("t", t("picker.chip.type", "type")), ("s", t("picker.chip.sort", "sort")),
            ("p", t("picker.chip.profile", "profile")), ("a", t("picker.chip.all", "all"))]


def draw(win, v: View, shown: list[Row], pal: theme.Palette, g: theme.Glyphs,
         dry_run: bool = False) -> None:
    win.erase()
    h, w = win.getmaxyx()

    def put(y: int, segs, x: int = 0) -> int:
        return theme.draw_segments(win, y, x, segs, pal)

    put(0, theme.title_bar(f" agent-toggle  v{__version__}", summary(v, g) + " ", w, g))
    counts = [len(visible(v.rows, v.query, name, v.type_)) for name in v.tabs]
    labels = [f"{t('picker.all', 'All') if n == 'all' else n} {c}" for n, c in zip(v.tabs, counts)]
    active = v.tabs.index(v.harness)
    first = 0                                        # scroll the tabs so the active one shows
    while first < active and sum(theme.cell_width(s) + 3 for s in labels[first:active + 1]) > w:
        first += 1
    put(1, theme.tab_bar(labels[first:], active - first, w, g))

    if v.filtering:
        left = [(f" {g.search} ", "accent"), (v.query, "text"),
                ((g.full if g.unicode else "_") if v.typing else "", "accent")]
        if v.fuzzy:
            left.append((f"  {'≈' if g.unicode else '~'} " + t("picker.search.fuzzy", "fuzzy match"),
                         "warning"))
    else:
        left = [(f" {g.search} ", "accent"),
                (t("picker.search.idle", "Type to search (name, path, fuzzy)"), "muted")]
    end = put(2, left)
    right = (f"{t('picker.filter.type', 'type:')} {type_label(v.type_)}  "
             f"{t('picker.filter.sort', 'sort:')} {sort_label(v.sort)} ")
    if end + theme.cell_width(right) + 2 <= w:
        put(2, [(right, "muted")], w - theme.cell_width(right))

    body = max(0, h - BODY_TOP - 2)                  # 0 on a tiny window: status + keys only
    v.page = max(1, body)                            # draw owns the page size and scroll
    dw = min(48, w * 2 // 5) if w >= DETAIL_MIN else 0
    lw = w - dw - (1 if dw else 0)
    lines: list[tuple] = []
    at: list[int] = []                               # row index -> line index
    for type_, grp in grouped(shown):
        lines.append(("head", type_, len(grp)))
        for r in grp:
            at.append(len(lines))
            lines.append(("row", r))
    if shown:                                        # keep the cursor (and its heading) on screen
        line = at[v.cur]
        want = line - 1 if lines[line - 1][0] == "head" else line
        v.top = min(v.top, want)
        v.top = max(v.top, line - body + 1)
        v.top = max(0, min(v.top, len(lines) - body))
    else:
        v.top = 0
        if body:
            put(BODY_TOP, clip([(" " + t("picker.empty", "nothing matches -- Ctrl-U clears "
                                         "the filter, Left/Right picks another harness, t "
                                         "another type"), "muted")], lw, g.ellipsis))
    any_shared = any(r.shared for r in shown)
    cursor_row = shown[v.cur] if shown else None
    for i, line in enumerate(lines[v.top:v.top + body]):
        y = BODY_TOP + i
        if line[0] == "head":
            put(y, heading(line[1], line[2], lw, g))
        else:
            put(y, row_segments(line[1], lw, v, g, line[1] is cursor_row, any_shared))
    if dw:
        pane = detail_lines(cursor_row, dw - 2, g) if cursor_row else []
        for i in range(body):
            put(BODY_TOP + i, [(g.v, "muted"), (" ", "text"), *(pane[i] if i < len(pane) else [])],
                lw)

    if v.note:                                       # last profile result, until the next key
        put(h - 2, [(" " + v.note, "error" if v.note.startswith("error") else "accent")])
    else:
        status = t("picker.status.shown", "%s of %s shown", len(shown), len(v.rows))
        if shown:
            status += f"{_sep(g)}{v.cur + 1}/{len(shown)}"
        if dry_run:
            status += _sep(g) + t("picker.status.dry_run", "dry run: Enter only shows the plan")
        put(h - 2, [(" " + status, "muted")])
    put(h - 1, [(" ", "text"), *theme.key_chips(chips(v, g), w - 2, g)])
    win.refresh()


def help_lines(g: theme.Glyphs) -> list[tuple[str, str]]:
    ud, lr = ("↑↓", "←→") if g.unicode else ("Up/Down", "Left/Right")
    return [
        (f"{ud} PgUp/Dn Home/End", t("picker.help.move", "move (Ctrl-P / Ctrl-N too)")),
        ("Space  Tab", t("picker.help.toggle", "toggle the row (live <-> parked), next row")),
        ("Enter", t("picker.help.apply", "apply staged changes (--dry-run: plan only)")),
        ("Esc  Ctrl-C", t("picker.help.cancel", "cancel; nothing is changed")),
        ("/", t("picker.help.filter", "search name / harness / path (fuzzy); any other letter starts one too")),
        ("Bksp  Ctrl-U", t("picker.help.edit", "edit / clear the filter")),
        (f"{lr}  0-9  h", t("picker.help.harness", "harness tab (0 = All)")),
        ("t", t("picker.help.type", "cycle the type filter")),
        ("s", t("picker.help.sort", "cycle sort: name / cost (biggest first)")),
        ("p", t("picker.help.profile", "profiles: stage one, or save what is live")),
        ("a  Ctrl-A", t("picker.help.all", "toggle every visible row")),
        ("?", t("picker.help.help", "this help")),
    ]


def clip(segs: list[theme.Segment], cells: int, ellipsis: str = "") -> list[theme.Segment]:
    """`segs` cut (with `ellipsis`) or space-padded to exactly `cells` cells."""
    out: list[theme.Segment] = []
    used = 0
    for s, role in segs:
        if theme.cell_width(s) > cells - used:
            s = theme.truncate(s, cells - used, ellipsis)
            out.append((s, role))
            used += theme.cell_width(s)
            break
        out.append((s, role))
        used += theme.cell_width(s)
    return out + [(" " * max(0, cells - used), "text")]


def show_help(win, pal: theme.Palette, g: theme.Glyphs) -> None:
    """A centered box over the current screen; any key closes it."""
    h, w = win.getmaxyx()
    keys = help_lines(g)
    kw = max(theme.cell_width(k) for k, _ in keys) + 2
    body = [[(pad(k, kw), "chip_key"), (d, "text")] for k, d in keys]
    body += [[], [(t("picker.help.cost", "Cost = ~tokens loaded at session start (chars/4)."),
                   "muted")],
             [(t("picker.help.close", "any key closes this help"), "muted")]]
    inner = min(w - 4, max(sum(theme.cell_width(s) for s, _ in ln) for ln in body) + 2)
    bw, bh = inner + 2, min(h, len(body) + 2)
    y0, x0 = max(0, (h - bh) // 2), max(0, (w - bw) // 2)
    title = f" {t('picker.help.title', 'Keys')} "
    theme.draw_segments(win, y0, x0, [(g.tl + g.h, "accent"), (title, "title"),
                                      (g.h * max(0, inner - 1 - theme.cell_width(title)) + g.tr,
                                       "accent")], pal)
    for i, ln in enumerate(body[:max(0, bh - 2)]):
        theme.draw_segments(win, y0 + 1 + i, x0, [(g.v + " ", "accent"),
                                                  *clip(ln, inner - 1, g.ellipsis),
                                                  (g.v, "accent")], pal)
    if bh >= 2:
        theme.draw_segments(win, y0 + bh - 1, x0, [(g.bl + g.h * inner + g.br, "accent")], pal)
    win.refresh()
    try:
        win.get_wch()
    except (curses.error, KeyboardInterrupt):
        pass


def ask_profile(win, pal: theme.Palette | None = None, g: theme.Glyphs = theme.ASCII) -> str:
    """The profile prompt: saved profiles listed, one line read (Enter keeps it, Esc or
    Ctrl-C gives "" = cancel). The grammar is `model.profile_command`'s."""
    pal = pal or theme.Palette({}, True)
    text = ""
    while True:
        win.erase()
        height = win.getmaxyx()[0]
        lines = [(profile_listing(), "title"), ("", "text"),
                 (t("picker.profile.hint", "number or name = stage that profile; `save <name>` "
                    "= save what is live now"), "muted"),
                 (t("picker.profile.keys", "Enter runs it, Esc cancels"), "muted")]
        for y, seg in enumerate(lines[:max(0, height - 1)]):     # draw_segments never raises
            theme.draw_segments(win, y, 1, [seg], pal)
        theme.draw_segments(win, min(len(lines), max(0, height - 1)), 1,
                            [("profile> ", "accent"), (text + (g.full if g.unicode else "_"), "text")], pal)
        win.refresh()
        try:
            key = win.get_wch()
        except (curses.error, KeyboardInterrupt):
            return ""
        if key in ("\x1b", "\x03"):
            return ""
        if key in ENTER:
            return text
        if key in BACKSPACE:
            text = text[:-1]
        elif key == "\x15":
            text = ""
        elif isinstance(key, str) and key.isprintable():
            text += key


# ----------------------------------------------------------------------- loop

def loop(win, rows: list[Row], color: bool = False, project: Path | None = None,
         dry_run: bool = False, glyphs: theme.Glyphs | None = None,
         harness: str | None = None) -> list[Row] | None:
    pal = theme.init_curses_colors(color)
    g = glyphs or theme.get_glyphs()
    # Esc answers in 0.1 s, not the 1 s default; shorter would split a slow SSH arrow key
    for setup in (lambda: curses.curs_set(0), lambda: curses.set_escdelay(100)):
        try:
            setup()
        except (curses.error, AttributeError):
            pass
    win.keypad(True)
    v = View(*prefs(rows, harness))      # `ui --harness X` shows X even when it is off

    while True:
        shown = v.shown()
        v.cur = max(0, min(v.cur, len(shown) - 1))
        draw(win, v, shown, pal, g, dry_run)
        try:
            key = win.get_wch()
        except (curses.error, KeyboardInterrupt):
            return None
        v.note = ""

        if key in ("\x1b", "\x03"):                       # Esc / Ctrl-C
            return None
        if key in ENTER:            # only the rows on offer: a hidden harness is never touched
            return [r for r in v.rows if r.changed]
        if key == "\t" or key == " " and not v.filtering:
            if shown:                                     # an empty list: Space is a no-op
                shown[v.cur].staged = not shown[v.cur].staged
            v.cur += 1
        elif key in (curses.KEY_UP, "\x10"):
            v.cur -= 1
        elif key in (curses.KEY_DOWN, "\x0e"):
            v.cur += 1
        elif key == curses.KEY_NPAGE:
            v.cur += v.page
        elif key == curses.KEY_PPAGE:
            v.cur -= v.page
        elif key == curses.KEY_HOME:
            v.cur = 0
        elif key == curses.KEY_END:
            v.cur = len(shown) - 1
        elif key in (curses.KEY_LEFT, curses.KEY_RIGHT):
            v.tab(-1 if key == curses.KEY_LEFT else 1)
        elif key in BACKSPACE:
            v.query = v.query[:-1]
            v.typing, v.cur = v.typing and bool(v.query), 0
        elif key == "\x15":                               # Ctrl-U
            v.query, v.typing, v.cur = "", False, 0
        elif key == "\x01" or key == "a" and not v.filtering:     # Ctrl-A / a
            live = not all(r.staged for r in shown)
            for r in shown:
                r.staged = live
        elif not v.filtering and key in COMMANDS:
            if key == "p":
                v.note = profile_command(v.rows, ask_profile(win, pal, g), project, dry_run,
                                         harness)
            elif key == "s":
                v.sort = cycle(SORTS, v.sort)
            elif key == "h":
                v.tab(1)
            elif key == "t":
                v.type_ = cycle(v.types, v.type_)
            elif key == "?":
                show_help(win, pal, g)
            elif key == "/":
                v.typing = True
            else:
                v.tab(to=int(key))
            if key != "?":
                v.cur = 0
        elif isinstance(key, str) and key.isprintable():
            v.query += key
            v.typing, v.cur = True, 0
        # KEY_RESIZE and anything else: just redraw at the new size


def pick(state: dict, harnesses: dict, plugins: bool = True, color: bool = False,
         project: Path | None = None, dry_run: bool = False,
         harness: str | None = None) -> list[Row] | None:
    """`plugins=False` skips the `claude plugin list` call (a dry run never shells out);
    `harness` (ui --harness) is shown even when it is switched off in settings."""
    notes: list[str] = []
    with spinner(t("picker.loading", "Loading resources…")):
        rows = collect(state, harnesses, notes.append, plugins)
    if not rows:
        print("nothing to show")
        return None
    wheel = sys.stdout.isatty()
    try:
        if wheel:                       # xterm "alternate scroll": the wheel sends Up/Down here
            sys.stdout.write("\x1b[?1007h")
            sys.stdout.flush()
        return curses.wrapper(loop, rows, color, project, dry_run, None, harness)
    finally:
        if wheel:
            sys.stdout.write("\x1b[?1007l")
            sys.stdout.flush()
        for n in notes:                 # after curses is torn down, where they are readable
            print(f"WARNING  {n}")
