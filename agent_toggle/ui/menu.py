"""Numbered-menu fallback for the picker (DESIGN s5.10), for terminals without curses.

Same `pick()` signature and return type as `picker.pick`, so `cli.py` does not care
which one ran. Plain line I/O: filter -> numbered list -> toggle by number -> apply.
"""
from __future__ import annotations

import sys
from pathlib import Path

from .model import (
    SORTS,
    Row,
    collect,
    cycle,
    grouped,
    prefs,
    profile_command,
    profile_listing,
    type_label,
    visible,
)
from .theme import Glyphs, ansi, encodable, get_glyphs

HELP = ("numbers toggle (1 3 5-7)  /text filter (/ alone clears)  s sort  h harness  t type  "
        "p profile  a apply  q cancel  ? help")


def ordered(shown: list[Row]) -> list[Row]:
    """`shown` in screen order: grouped by type, so the numbers count down the screen."""
    return [r for _, grp in grouped(shown) for r in grp]


def _show(out, shown: list[Row], chips: str, pending: int, color: bool = False,
          g: Glyphs | None = None) -> None:
    g = g or get_glyphs(out)
    out.write("\n")
    n = 0
    for type_, grp in grouped(shown):
        out.write(encodable(ansi("title", f"{type_label(type_)} ({len(grp)})", color) + "\n", out))
        for r in grp:
            n += 1
            glyph = ansi("live", g.live, color) if r.staged else ansi("parked", g.parked, color)
            mark = ansi("pending", "+" if r.staged else "-", color) if r.changed else " "
            also = ansi("choice", f"  (+{','.join(r.shared)})", color) if r.shared else ""
            name = ansi("pending", r.name, color) if r.changed else r.name
            out.write(encodable(f"{n:>3}  {glyph} {mark} {r.cost_cell:>7}  "
                                f"{ansi('harness', f'{r.harness:<9}', color)}{name}{also}\n", out))
    staged = ansi("pending", f" | {pending} staged", color) if pending else ""
    out.write(f"{len(shown)} shown | ~{sum(r.tokens for r in shown)} tok | {chips}{staged}\n"
              f"{ansi('muted', HELP, color)}\n")


def _numbers(arg: str, size: int) -> list[int] | None:
    """`1 3 5-7` / `1,3` -> 0-based indexes; None when any token is not a valid row."""
    out: list[int] = []
    for tok in arg.replace(",", " ").split():
        lo, _, hi = tok.partition("-")
        if not (lo.isdigit() and (not hi or hi.isdigit())):
            return None
        a, b = int(lo), int(hi or lo)
        if not 1 <= a <= b <= size:
            return None
        out.extend(range(a - 1, b))
    return out


def loop(rows: list[Row], stdin, stdout, project: Path | None = None,
         dry_run: bool = False, color: bool = False,
         explicit: str | None = None) -> list[Row] | None:
    # settings: hidden harnesses (never `explicit`, from ui --harness), defaults
    kept, names, harness, type_, sort = prefs(rows, explicit)
    harness_opts = ["all", *names]
    type_opts = ["all", *(ty for ty, _ in grouped(kept))]
    harness = harness if harness in harness_opts else "all"
    type_ = type_ if type_ in type_opts else "all"
    query, redraw, g = "", True, get_glyphs(stdout)
    while True:
        shown = ordered(visible(kept, query, harness, type_, sort))
        if redraw:
            chips = f"filter:{'/' + query if query else '-'} harness:{harness} type:{type_} sort:{sort}"
            _show(stdout, shown, chips, sum(1 for r in kept if r.changed), color, g)
        redraw = True
        stdout.write("> ")
        stdout.flush()
        line = stdin.readline()
        if not line:                                  # EOF: never block, never apply
            return None
        cmd = line.strip()
        low = cmd.lower()
        if low in ("q", "quit"):
            return None
        if low in ("a", "apply"):
            return [r for r in kept if r.changed]
        if low == "s":
            sort = cycle(SORTS, sort)
        elif low == "h":
            harness = cycle(harness_opts, harness)
        elif low == "t":
            type_ = cycle(type_opts, type_)
        elif cmd.startswith("/"):
            query = cmd[1:].strip()
        elif low == "p" or low.startswith("p "):          # profiles: list, or run one line
            line = cmd[1:].strip()
            stdout.write((profile_command(kept, line, project, dry_run, explicit) if line else
                          f"{profile_listing()}\np <number|name>  |  p save <name>")
                         + "\n")
            redraw = bool(line)
        elif low in ("?", "help"):
            stdout.write("Numbers tick/untick rows (unticked = parked); nothing is applied "
                         "until `a`. `p` lists profiles; `p <number|name>` stages one, "
                         "`p save <name>` saves the live state.\n")
            redraw = False
        elif cmd and (idx := _numbers(cmd, len(shown))) is not None:
            for i in idx:
                shown[i].staged = not shown[i].staged
        elif cmd:
            stdout.write(f"not understood: {cmd!r} (? for help)\n")
            redraw = False


def pick(state: dict, harnesses: dict, plugins: bool = True, color: bool = False,
         stdin=None, stdout=None, project: Path | None = None,
         dry_run: bool = False, harness: str | None = None) -> list[Row] | None:
    """`color` (cli passes `use_color(...)`) turns on ANSI styling of the rows."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    notes: list[str] = []
    rows = collect(state, harnesses, notes.append, plugins)
    for n in notes:
        stdout.write(f"WARNING  {n}\n")
    if not rows:
        stdout.write("nothing to show\n")
        return None
    return loop(rows, stdin, stdout, project, dry_run, color, harness)
