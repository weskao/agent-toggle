"""Picker rows and filtering, shared by the curses picker and the numbered menu.

No curses import here, so the menu fallback works where curses does not exist.
"""
from __future__ import annotations

from pathlib import Path

from .. import cost, profiles
from ..output import CliError, Result

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


# ------------------------------------------------------------ typing cue (G3)

TYPING_HINT = "typing: Backspace edits, Ctrl-U clears, Enter applies, Esc quits (nothing applied)"


def header_text(query: str, typing: bool) -> str:
    """The picker's top line. A filter being typed is `/query` plus a cursor block and a
    hint, so the mode shows without colour; idle, it says how to start one."""
    if typing or query:
        return f" filter: /{query}█   {TYPING_HINT}"
    return " filter: (press / to type one, ? for keys)"


# ------------------------------------------------------------ profile key (G9)

PROFILE_USAGE = "profile: <number|name> or apply <number|name> or save <name>"


def profile_names() -> list[str]:
    return [p.stem for p in profiles.stored()]


def profile_listing() -> str:
    names = profile_names()
    return ("profiles: " + "  ".join(f"{n}) {name}" for n, name in enumerate(names, 1))
            if names else "no profiles saved (save one with: save <name>)")


def profile_command(rows: list[Row], line: str, project: Path | None = None,
                    dry_run: bool = False) -> str:
    """Run one profile prompt line and return a one-line result (never raises, never prints).

    `save <name>` writes what is live now through `profiles.cmd_save`; `[apply] <number|name>`
    only STAGES the profile's ticks on `rows` -- Enter then applies them through the
    same plan as any other tick, so `--dry-run` previews it. Names and scope are
    validated by `profiles` exactly as `profile save|apply` does."""
    line = line.strip()
    if not line:
        return ""
    verb, _, arg = line.partition(" ")
    if verb.lower() in ("save", "apply"):
        verb, arg = verb.lower(), arg.strip()
    else:
        verb, arg = "apply", line
    if not arg:
        return PROFILE_USAGE
    try:
        if verb == "save":
            if dry_run:
                return "save is off under --dry-run (it writes a file)"
            out = Result(json_mode=True)            # json_mode: nothing is printed over curses
            profiles.cmd_save(arg, None, None, out, project)
            warned = f"; WARNING: {out.warnings[0]}" if out.warnings else ""
            return (f"saved profile {arg}: {out.rows[0]['items']} items as live now "
                    f"(staged ticks not included){warned}")
        names = profile_names()
        name = names[int(arg) - 1] if arg.isascii() and arg.isdecimal() and 1 <= int(arg) <= len(names) else arg
        by = {(r.harness, r.type, r.name): r for r in rows}
        staged = same = skipped = 0
        for it in profiles.load_scoped(name, project)["items"]:
            r = by.get((it["harness"], it["type"], it["name"]))
            if r is None:
                skipped += 1
                continue
            r.staged = it["live"]
            staged, same = staged + r.changed, same + (not r.changed)
        return (f"profile {name}: {staged} staged, {same} already as profiled, "
                f"{skipped} not on this machine" + (" -- Enter to apply" if staged else ""))
    except CliError as e:                           # same messages as the CLI
        return f"error: {e.msg}"
    except (OSError, ValueError) as e:              # a name too long, a NUL or a lone surrogate
        return f"error: {getattr(e, 'strerror', None) or e}"
