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
import json
import re
from pathlib import Path

from .. import fs

MCP_TOML_RE = re.compile(r"^\[mcp_servers\.([^.\]]+)\]\s*$", re.M)

HELP = "↑↓ move  Tab tick/untick  Enter apply  Esc cancel  type to filter"


class Row:
    __slots__ = ("harness", "type", "name", "enabled", "staged")

    def __init__(self, harness: str, type_: str, name: str, enabled: bool):
        self.harness, self.type, self.name = harness, type_, name
        self.enabled = enabled
        self.staged = enabled          # what the user has ticked so far

    @property
    def changed(self) -> bool:
        return self.staged != self.enabled

    @property
    def label(self) -> str:
        return f"{self.harness}/{self.type}/{self.name}"


# ------------------------------------------------------------- data gathering

def live_names(base: Path, type_: str) -> list[str]:
    """Names of live resources, with nesting addressed by a colon."""
    if not base.is_dir():
        return []
    if type_ == "skill":
        return sorted(p.name for p in base.iterdir()
                      if (p.is_dir() or p.is_symlink()) and not p.name.startswith("."))
    names = []
    for p in sorted(base.rglob("*")):
        if p.name.startswith("."):
            continue
        if p.suffix in (".md", ".toml", ".yaml", ".yml") or p.is_symlink() or (p.is_file() and not p.suffix):
            rel = p.relative_to(base).with_suffix("")
            names.append(str(rel).replace("/", ":"))
    return sorted(set(names))


def live_mcp(home: Path, backend: str | None) -> list[str]:
    if backend == "claude-json":
        try:
            cfg = json.loads(fs.claude_json().read_text())
        except (OSError, json.JSONDecodeError):
            return []
        # Local-scope servers are nested per project; without them the picker
        # silently hides everything added with `claude mcp add -s local`.
        names = set(cfg.get("mcpServers", {}))
        for pdata in cfg.get("projects", {}).values():
            if isinstance(pdata, dict):
                names.update(pdata.get("mcpServers") or {})
        return sorted(names)
    if backend == "toml":
        config = home / "config.toml"
        if not config.is_file():
            return []
        try:
            # Same text-level approach as the write path: no TOML parser needed.
            return sorted(set(MCP_TOML_RE.findall(config.read_text())))
        except OSError:
            return []
    return []


def collect(state: dict, harnesses: dict) -> list[Row]:
    """Every togglable resource on this machine, live and parked.

    Plugins are absent on purpose: enumerating them needs a `claude plugin
    list` subprocess whose output format is not contracted. They stay a CLI
    operation.
    """
    rows: list[Row] = []
    seen: set[tuple[str, str, str]] = set()

    for hname, h in harnesses.items():
        home = h.home
        if not home.is_dir():
            continue
        for type_ in h.types:
            if type_ in h.dirs:
                names = sorted({n for sub in h.dirs[type_]
                                for n in live_names(home / sub, type_)})
            elif type_ == "mcp":
                names = live_mcp(home, h.backend)
            else:
                continue                      # plugin: CLI only
            for name in names:
                key = (hname, type_, name)
                if key not in seen:
                    seen.add(key)
                    rows.append(Row(hname, type_, name, True))

    for entry in state.get("disabled", {}).values():
        key = (entry.get("harness", "claude"), entry["type"], entry["name"])
        if key not in seen and entry["type"] != "plugin":
            seen.add(key)
            rows.append(Row(*key, False))

    rows.sort(key=lambda r: (r.harness, r.type, r.name))
    return rows


def match(rows: list[Row], query: str) -> list[Row]:
    """Case-insensitive AND over whitespace-separated terms."""
    terms = query.lower().split()
    if not terms:
        return rows
    return [r for r in rows if all(t in r.label.lower() for t in terms)]


# -------------------------------------------------------------------- drawing

def draw(win, rows: list[Row], query: str, cur: int, top: int, pending: int) -> None:
    win.erase()
    height, width = win.getmaxyx()
    body = max(1, height - 3)

    header = f" filter: {query}█" if query else " filter: (type to narrow)"
    win.addnstr(0, 0, header.ljust(width - 1), width - 1, curses.A_BOLD)

    for i in range(body):
        idx = top + i
        if idx >= len(rows):
            break
        r = rows[idx]
        box = "[x]" if r.staged else "[ ]"
        mark = "*" if r.changed else " "
        line = f" {mark}{box} {r.harness:<9}{r.type:<8}{r.name}"
        attr = curses.A_REVERSE if idx == cur else curses.A_NORMAL
        if r.changed:
            attr |= curses.A_BOLD
        win.addnstr(1 + i, 0, line.ljust(width - 1), width - 1, attr)

    count = f" {len(rows)} shown"
    if pending:
        count += f"  |  {pending} change(s) staged -- Enter to apply"
    win.addnstr(height - 2, 0, count.ljust(width - 1), width - 1, curses.A_BOLD)
    win.addnstr(height - 1, 0, " " + HELP.ljust(width - 2), width - 1, curses.A_DIM)
    win.noutrefresh()
    curses.doupdate()


def loop(win, rows: list[Row]) -> list[Row] | None:
    curses.curs_set(0)
    win.keypad(True)
    query, cur, top = "", 0, 0

    while True:
        shown = match(rows, query)
        cur = max(0, min(cur, len(shown) - 1))
        height = max(1, win.getmaxyx()[0] - 3)
        top = max(0, min(top, max(0, len(shown) - height)))
        if cur < top:
            top = cur
        elif cur >= top + height:
            top = cur - height + 1

        pending = sum(1 for r in rows if r.changed)
        draw(win, shown, query, cur, top, pending)

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
            cur = 0
        elif key == "\x15":                               # Ctrl-U
            query, cur = "", 0
        elif isinstance(key, str) and key.isprintable():
            query += key
            cur = 0
        # KEY_RESIZE and anything else just redraw


def pick(state: dict, harnesses: dict) -> list[Row] | None:
    rows = collect(state, harnesses)
    if not rows:
        print("nothing to show")
        return None
    return curses.wrapper(loop, rows)
