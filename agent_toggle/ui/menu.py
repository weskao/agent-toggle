"""Numbered-menu fallback for the picker (DESIGN s5.10), for terminals without curses.

Same `pick()` signature and return type as `picker.pick`, so `cli.py` does not care
which one ran. Plain line I/O: filter -> numbered list -> toggle by number -> apply.
"""
from __future__ import annotations

import sys

from .model import SORTS, Row, collect, cycle, visible

HELP = ("numbers toggle (1 3 5-7)  /text filter  s sort  h harness  t type  "
        "a apply  q cancel  ? help")


def _show(out, shown: list[Row], chips: str, pending: int) -> None:
    out.write("\n")
    for n, r in enumerate(shown, 1):
        also = f"  (+{','.join(r.shared)})" if r.shared else ""
        out.write(f"{n:>3} {'*' if r.changed else ' '}{'[x]' if r.staged else '[ ]'} "
                  f"{r.cost_cell:>7}  {r.harness:<9}{r.type:<8}{r.name}{also}\n")
    out.write(f"{len(shown)} shown | ~{sum(r.tokens for r in shown)} tok | {chips}"
              f"{f' | {pending} staged' if pending else ''}\n{HELP}\n")


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


def loop(rows: list[Row], stdin, stdout) -> list[Row] | None:
    query, harness, type_, sort = "", "all", "all", "name"
    harness_opts = ["all", *sorted({n for r in rows for n in (r.harness, *r.shared)})]
    type_opts = ["all", *sorted({r.type for r in rows})]
    redraw = True
    while True:
        shown = visible(rows, query, harness, type_, sort)
        if redraw:
            chips = f"filter:{query or '-'} harness:{harness} type:{type_} sort:{sort}"
            _show(stdout, shown, chips, sum(1 for r in rows if r.changed))
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
            return [r for r in rows if r.changed]
        if low == "s":
            sort = cycle(SORTS, sort)
        elif low == "h":
            harness = cycle(harness_opts, harness)
        elif low == "t":
            type_ = cycle(type_opts, type_)
        elif cmd.startswith("/"):
            query = cmd[1:].strip()
        elif low in ("?", "help"):
            stdout.write("Numbers tick/untick rows (unticked = parked); nothing is applied "
                         "until `a`.\n")
            redraw = False
        elif cmd and (idx := _numbers(cmd, len(shown))) is not None:
            for i in idx:
                shown[i].staged = not shown[i].staged
        elif cmd:
            stdout.write(f"not understood: {cmd!r} (? for help)\n")
            redraw = False


def pick(state: dict, harnesses: dict, plugins: bool = True, color: bool = False,
         stdin=None, stdout=None) -> list[Row] | None:
    """`color` is accepted for signature parity and ignored (plain text)."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    notes: list[str] = []
    rows = collect(state, harnesses, notes.append, plugins)
    for n in notes:
        stdout.write(f"WARNING  {n}\n")
    if not rows:
        stdout.write("nothing to show\n")
        return None
    return loop(rows, stdin, stdout)
