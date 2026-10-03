"""Picker rows and filtering, shared by the curses picker and the numbered menu.

No curses import here, so the menu fallback works where curses does not exist.
"""
from __future__ import annotations

from .. import cost

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
