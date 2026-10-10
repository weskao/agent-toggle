"""Picker rows and filtering, shared by the curses picker and the numbered menu.

No curses import here, so the menu fallback works where curses does not exist.
"""
from __future__ import annotations

from pathlib import Path

from .. import cost, fs, profiles, settings
from ..harnesses import TYPES
from ..i18n import t
from ..mechanisms import resolve_item
from ..output import CliError, Result

SORTS = ("name", "cost")
# picker groups: the types, with Mods (plugins that draw UI) right after Plugins
GROUPS = (*TYPES[:TYPES.index("plugin") + 1], "mod", *TYPES[TYPES.index("plugin") + 1:])


class Row:
    __slots__ = ("harness", "type", "name", "enabled", "staged", "tokens", "would_save",
                 "shared", "path", "since", "mechanism", "basis", "description", "mod")

    def __init__(self, harness: str, type_: str, name: str, enabled: bool,
                 tokens: int = 0, would_save: int = 0, shared: tuple[str, ...] = (),
                 path: str = "", since: str = "", mechanism: str = "", basis: str = "",
                 description: str = "", mod: bool = False):
        self.harness, self.type, self.name = harness, type_, name
        self.enabled = enabled
        self.staged = enabled          # what the user has ticked so far
        self.tokens, self.would_save, self.shared = tokens, would_save, shared
        # detail-pane facts; "" = unknown (a plugin has no path, a live row no `since`)
        self.path, self.since, self.mechanism, self.basis = path, since, mechanism, basis
        self.description = description
        self.mod = mod                 # still type plugin: it toggles (and profiles) as one

    @property
    def group(self) -> str:
        """The picker group: `mod` for a mod plugin, else the type."""
        return "mod" if self.mod else self.type

    @property
    def changed(self) -> bool:
        return self.staged != self.enabled

    @property
    def label(self) -> str:
        return f"{self.harness}/{self.type}/{self.name}"

    @property
    def cost_cell(self) -> str:
        return f"{self.tokens}" if self.enabled else f"({self.would_save})"


def _where(h, type_: str, name: str) -> str:
    """Where a live row sits on disk: the item in its type dir, or the MCP config file."""
    for sub in h.dirs.get(type_, ()):
        if (p := resolve_item(h.home / sub, name)) is not None:
            return str(p)
    return str(h.mcp.file) if type_ == "mcp" and h.mcp else ""


def collect(state: dict, harnesses: dict, warn=lambda m: None,
            plugins: bool = True) -> list[Row]:
    """Every togglable resource on this machine, live and parked, plugins included.

    A dir shared by several harnesses is one row, filed under its owner, so staging
    it cannot also be staged (and then fail) under the other harness.
    """
    parked = {(e.get("harness", "claude"), e.get("type"), e.get("name")): e
              for e in state.get("disabled", {}).values() if isinstance(e, dict)}
    rows = []
    for i in cost.inventory(state, harnesses, warn, plugins):
        e, h = parked.get((i.harness, i.type, i.name), {}), harnesses.get(i.harness)
        mech = e.get("mechanism") or (h.mechanisms.get(i.type, "") if h else "")
        if i.enabled:          # ponytail: a few stats per live row, paid once at startup
            path = _where(h, i.type, i.name) if h else ""
        else:
            path = e.get("parked_at") or e.get("backup") or ""
        rows.append(Row(i.harness, i.type, i.name, i.enabled, i.tokens, i.would_save,
                        i.shared_with, str(path), str(e.get("at") or ""), str(mech), i.basis,
                        i.description, i.mod))
    rows.sort(key=lambda r: (r.harness, r.type, r.name))
    return rows


def hidden_harnesses(explicit: str | None = None) -> set[str]:
    """Harnesses switched off in settings; an explicit --harness is never hidden."""
    return set(settings.HARNESS_NAMES) - set(settings.enabled_harnesses()) - {explicit}


def prefs(rows: list[Row], harness: str | None = None
          ) -> tuple[list[Row], list[str], str, str, str]:
    """The settings view of `rows`: (kept rows, harness names, start harness, type, sort).

    A row is dropped when every harness it belongs to is switched off in settings (an
    explicit `harness`, from `ui --harness`, stays shown); the names are the shown
    harnesses that still have a row, in table order; the start values are
    picker_harness / picker_type / picker_sort."""
    off = hidden_harnesses(harness)
    kept = [r for r in rows if not off.issuperset((r.harness, *r.shared))]
    order = {n: i for i, n in enumerate(settings.HARNESS_NAMES)}
    names = sorted({n for r in kept for n in (r.harness, *r.shared)} - off,
                   key=lambda n: (order.get(n, len(order)), n))
    return (kept, names, settings.get("picker_harness"), settings.get("picker_type"),
            settings.get("picker_sort"))


def grouped(rows: list[Row]) -> list[tuple[str, list[Row]]]:
    """`rows` split by group, in GROUPS order (skill ... plugin, mod, mcp); the order
    inside a group is kept, so a sort still holds under each heading."""
    groups: dict[str, list[Row]] = {}
    for r in rows:
        groups.setdefault(r.group, []).append(r)
    order = {t: n for n, t in enumerate(GROUPS)}
    return sorted(groups.items(), key=lambda g: (order.get(g[0], len(order)), g[0]))


def type_label(type_: str) -> str:
    """The heading for a type group (`all` = the no-filter value)."""
    return {"all": t("picker.all", "All"), "skill": t("picker.type.skill", "Skills"),
            "agent": t("picker.type.agent", "Agents"),
            "command": t("picker.type.command", "Commands"),
            "rule": t("picker.type.rule", "Rules"), "plugin": t("picker.type.plugin", "Plugins"),
            "mod": t("picker.type.mod", "Mods"),
            "mcp": t("picker.type.mcp", "MCP")}.get(type_, type_)


def fmt_tokens(n: int) -> str:
    """1234 -> `1.2k`; small numbers as they are."""
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def short_path(path: str) -> str:
    """`path` with the home dir spelled `~`."""
    try:
        rel = Path(path).relative_to(fs.home())
    except ValueError:
        return path
    return "~/" + rel.as_posix() if rel.parts else "~"


def _hay(r: Row) -> str:
    # the group as its heading says it ("mods"; "mod" is a substring): a mod's type is plugin
    return f"{r.label} {r.group}s {r.path}".lower()


def strict(r: Row, terms: list[str]) -> bool:
    """Every term is a substring of the row's label or path."""
    hay = _hay(r)
    return all(t in hay for t in terms)


def subsequence(term: str, text: str) -> list[int] | None:
    """Positions of `term`'s letters in `text`, in order (greedy), or None."""
    at, out = 0, []
    for ch in term:
        at = text.find(ch, at) + 1
        if not at:
            return None
        out.append(at - 1)
    return out


def match(rows: list[Row], query: str) -> list[Row]:
    """Case-insensitive AND over whitespace-separated terms, matched against the label
    (harness/type/name) and the path. When nothing matches that way, fall back to fuzzy:
    each term's letters in order inside the label (`ctxmd` finds `context-md`)."""
    terms = query.lower().split()
    if not terms:
        return rows
    hits = [r for r in rows if strict(r, terms)]
    return hits or [r for r in rows
                    if all(subsequence(t, r.label.lower()) is not None for t in terms)]


def visible(rows: list[Row], query: str = "", harness: str = "all", type_: str = "all",
            sort: str = "name") -> list[Row]:
    """The rows on screen: filter chips, then the text query, then the sort."""
    shown = [r for r in rows
             if (harness == "all" or harness in (r.harness, *r.shared))
             and (type_ == "all" or r.group == type_)]
    shown = match(shown, query)
    if sort == "cost":
        shown = sorted(shown, key=lambda r: (-r.tokens, -r.would_save, r.harness, r.type, r.name))
    return shown


def toggle_same_name(rows: list[Row], cur: Row) -> int:
    """Stage every row on `cur`'s harness named like `cur` (any type, shown or not) to
    the opposite of `cur`'s staged tick; returns how many rows that touched."""
    same = [r for r in rows if r.harness == cur.harness and r.name == cur.name]
    live = not cur.staged
    for r in same:
        r.staged = live
    return len(same)


def cycle(options: tuple[str, ...] | list[str], current: str) -> str:
    """The option after `current` (wraps; an unknown current restarts at the first)."""
    return options[(options.index(current) + 1) % len(options)] if current in options \
        else options[0]



# ------------------------------------------------------------ profile key (G9)

PROFILE_USAGE = "profile: <number|name> or apply <number|name> or save <name>"


def profile_names() -> list[str]:
    return [p.stem for p in profiles.stored()]


def profile_listing() -> str:
    names = profile_names()
    return ("profiles: " + "  ".join(f"{n}) {name}" for n, name in enumerate(names, 1))
            if names else "no profiles saved (save one with: save <name>)")


def profile_command(rows: list[Row], line: str, project: Path | None = None,
                    dry_run: bool = False, harness: str | None = None) -> str:
    """Run one profile prompt line and return a one-line result (never raises, never prints).

    `save <name>` writes what is live now through `profiles.cmd_save`; `[apply] <number|name>`
    only STAGES the profile's ticks on `rows` -- Enter then applies them through the
    same plan as any other tick, so `--dry-run` previews it. Names and scope are
    validated by `profiles` exactly as `profile save|apply` does. An item of a harness
    switched off in settings (other than the explicit `harness`) counts as hidden."""
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
        off = hidden_harnesses(harness)
        staged = same = skipped = hidden = 0
        for it in profiles.load_scoped(name, project)["items"]:
            r = by.get((it["harness"], it["type"], it["name"]))
            if r is None:
                if it["harness"] in off:
                    hidden += 1
                else:
                    skipped += 1
                continue
            r.staged = it["live"]
            staged, same = staged + r.changed, same + (not r.changed)
        return (f"profile {name}: {staged} staged, {same} already as profiled, "
                f"{skipped} not on this machine"
                + (f", {hidden} hidden by settings" if hidden else "")
                + (" -- Enter to apply" if staged else ""))
    except CliError as e:                           # same messages as the CLI
        return f"error: {e.msg}"
    except (OSError, ValueError) as e:              # a name too long, a NUL or a lone surrogate
        return f"error: {getattr(e, 'strerror', None) or e}"
