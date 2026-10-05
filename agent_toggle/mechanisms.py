"""The toggle strategies: park/restore a file or dir, drive the plugin CLI,
remove/re-add an MCP server."""
from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import time
from pathlib import Path
from typing import NamedTuple

from . import fs
from .backends.flag_json import FlagError, FlagMissing, read_flag, set_flag
from .backends.mcp_json import (
    ProjectMcpError,
    _finite,
    _no_dup,
    _not_json,
    claude_mcp_config,
    claudeai_connector_names,
    project_mcp_backup,
    project_mcp_remove,
    project_mcp_restore,
    read_project_mcp,
    toggle_claudeai_connector,
    write_project_mcp,
)
from .backends.mcp_toml import codex_mcp_add, codex_mcp_remove
from .backends.plugin_cli import claude_bin, full_plugin_id, list_plugins, run_cli
from .companions import move, park_companions, restore_companions
from .fs import gitignored, prune_empty
from .harnesses import PROBE_SUFFIXES, harnesses, project_view
from .output import CliError, Result, die
from .store import (
    BATCH,
    begin,
    check_entry,
    checkpoints,
    end,
    log,
    make_key,
    parse_key,
    project_digest,
)


def fail_row(out: Result, dry_run: bool, harness: str, type_: str, action: str, name: str,
          msg: str, status: str = "error", *, batch: str = BATCH,
          project: str | None = None) -> int:
    """Record one failed item (returns 1, the fail count to add)."""
    out.row(harness, type_, name, f"would-{action}" if dry_run else action, status, msg,
            **({"project": project} if project else {}))
    if not dry_run:
        log(action, type_, name, status, msg, harness=harness, batch=batch, project=project,
            scope="project" if project else "user")
    return 1


def redact(text: str, config) -> str:
    """`text` with every string value of `config` (a raw MCP entry, or its JSON text)
    masked as `***`. CLI output can echo the config it was handed, and output shows
    names and paths, never backed-up values (DESIGN s6.1 row 3). Mask BEFORE truncating.
    ponytail: values under 4 chars stay (masking them would garble the message)."""
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except ValueError:
            config = [config]
    vals, todo = set(), [config]
    while todo:
        x = todo.pop()
        if isinstance(x, dict):
            todo += x.values()
        elif isinstance(x, list):
            todo += x
        elif isinstance(x, str) and len(x) >= 4:
            vals |= {x, json.dumps(x)[1:-1]}          # as given, and JSON-escaped
    for v in sorted(vals, key=len, reverse=True):
        text = text.replace(v, "***")
    return text


def _refusal(entry: dict | None, table: dict, key: str, need: tuple[str, ...]) -> str | None:
    """`refused: <reason>` when a state entry must not be replayed (DESIGN s6.1 row 2).

    store.check_entry covers the path fields; this adds what the replay itself
    reads: the entry must be the one its key names, carry the `need` fields the
    mechanism dereferences, and its companions must stay in their own roots."""
    if not entry:
        return None
    reason = _entry_reason(entry, table, key, need)
    return f"refused: {reason}" if reason else None


def _abs_path(value) -> bool:
    return isinstance(value, str) and bool(value) and Path(value).is_absolute() \
        and ".." not in Path(value).parts


def _entry_reason(entry: dict, table: dict, key: str, need: tuple[str, ...]) -> str | None:
    if not isinstance(entry, dict):
        return "entry is not an object"
    head, _, rest = key.partition(":")
    type_, _, name = rest.partition(":")
    if (entry.get("harness"), entry.get("type"), entry.get("name")) != \
            (head.partition("@")[0], type_, name):
        return f"entry does not match its key {key!r}"
    for f in need:
        if not isinstance(entry.get(f), str) or not entry.get(f):
            return f"missing {f}"
    if entry.get("backend") == "project-json" and "@" not in head:
        return "project .mcp.json entry under a user-scope key"
    if entry.get("backend") == "json" and (
            "@" in head or getattr(table.get(head), "backend", None) != "json"):
        return "json entry for a harness whose table row does not use the json backend"
    if entry.get("backend") == "claude-json" and entry.get("scope") in ("user", "local"):
        if entry["scope"] == "local":
            # `project` here is the claude local-scope cwd, not a --project scope
            proj = entry.get("project")
            if not _abs_path(proj) or Path(proj) == Path(Path(proj).anchor):
                return f"local-scope project is not a usable dir: {proj!r}"
            entry = {k: v for k, v in entry.items() if k != "project"}
    elif entry.get("backend") == "claude-json" and entry.get("scope") is not None:
        return f"unknown mcp scope {entry.get('scope')!r}"
    if entry.get("mechanism") == "flag" and not entry.get("connector"):
        if reason := _flag_reason(entry, table):
            return reason
    comps = entry.get("companions") or []
    if not isinstance(comps, list):
        return "companions is not a list"
    home = table[entry["harness"]].home if entry["harness"] in table else None
    for c in comps:
        src, dst = (c.get("to"), c.get("from")) if isinstance(c, dict) else (None, None)
        if not (_abs_path(src) and fs.contained(Path(src), fs.companion_dir())):
            return f"companion outside {fs.companion_dir()}: {src!r}"
        if not (home and _abs_path(dst) and fs.contained(Path(dst), home)):
            return f"companion origin outside the harness home: {dst!r}"
    return check_entry(entry, table, key=key)


def flag_target(h, type_: str, name: str) -> tuple[Path, tuple[str, ...]] | None:
    """(file, pointer) the table declares for this harness/type/name, or None.
    `<name>` in the declared pointer is the item name; the file is under the home."""
    spec = h.flags.get(type_)
    if not spec:
        return None
    rel, pointer = spec
    return h.home / rel, tuple(name if p == "<name>" else p for p in pointer)


def _flag_reason(entry: dict, table: dict) -> str | None:
    """A flag entry may only replay the exact file+pointer the table declares for
    its harness/type/name, so a tampered state file cannot aim the write elsewhere."""
    h = table.get(entry.get("harness"))
    target = flag_target(h, entry.get("type"), entry.get("name")) if h else None
    f = entry.get("flag")
    if target is None or not isinstance(f, dict):
        return "flag entry has no flag record for this harness/type"
    if not isinstance(f.get("file"), str) or Path(f["file"]) != target[0]:
        return f"flag file is not the declared one: {f.get('file')!r}"
    if not isinstance(f.get("pointer"), list) or tuple(f["pointer"]) != target[1]:
        return f"flag pointer is not the declared one: {f.get('pointer')!r}"
    if not isinstance(f.get("was"), bool):
        return "flag.was is not a boolean"
    return None


def _ok(out: Result, dry_run: bool, harness: str, type_: str, action: str, name: str,
        text: str, **extra) -> dict:
    """Record one success; a dry run records the plan instead, with human text
    `would <action>` (the real text describes what already happened)."""
    if dry_run:
        return out.row(harness, type_, name, f"would-{action}", "planned",
                       f"would {action}", **extra)
    return out.row(harness, type_, name, action, "ok", text, **extra)


def validate_name(name: str) -> None:
    """Refuse names that could address anything outside the harness dir (exit 2)."""
    parts = name.split(":")
    if (not name or Path(name).is_absolute() or "/" in name or "\\" in name
            or any(p in ("", ".", "..") or p.startswith("-") for p in parts)):
        die(f"invalid name {name!r}: use plain names, `a:b` for nesting", 2)


# Plugin ids reach `claude plugin ...`; on Windows that is claude.cmd under cmd.exe,
# where `&` or `%` in an argument would run commands (DESIGN s6.1 row 5).
PLUGIN_ID_RE = re.compile(r"[A-Za-z0-9._@:/-]+")


def valid_plugin_id(name: str) -> bool:
    return PLUGIN_ID_RE.fullmatch(name) is not None


def _valid_name(name: str) -> bool:
    try:
        validate_name(name)
    except CliError:
        return False
    return True


def resolve_item(base: Path, name: str) -> Path | None:
    """Find a resource on disk. `a:b` addresses a nested `a/b`.

    The item itself may be a symlink (it is moved as a link), but its parent
    must resolve inside `base`, so no name can reach outside the harness dir.
    """
    rel = name.replace(":", "/")
    root = base.resolve()
    for suffix in PROBE_SUFFIXES:
        p = base / (rel + suffix)
        if (p.exists() or p.is_symlink()) and p.parent.resolve().is_relative_to(root):
            return p
    return None


MCP_TOML_RE = re.compile(r"^\[mcp_servers\.([^.\]]+)\]\s*$", re.M)


def live_names(base: Path, type_: str) -> list[str]:
    """Names of live resources, with nesting addressed by a colon."""
    if not base.is_dir():
        return []
    if type_ == "skill":
        return sorted(p.name for p in base.iterdir()
                      if (p.is_dir() or p.is_symlink()) and not p.name.startswith("."))
    names = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if not d.startswith(".")]   # never descend into hidden dirs
        for n in (*files, *dirs):
            p = Path(root, n)
            if n.startswith("."):
                continue
            if p.suffix in (".md", ".toml", ".yaml", ".yml") or p.is_symlink() or (p.is_file() and not p.suffix):
                rel = p.relative_to(base).with_suffix("")
                names.append(rel.as_posix().replace("/", ":"))
    return sorted(set(names))


def live_mcp(home: Path, backend: str | None) -> list[str]:
    """Names of the MCP servers currently configured for one harness."""
    if backend == "claude-json":
        try:
            cfg = json.loads(fs.claude_json().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        # Local-scope servers are nested per project; without them the picker
        # silently hides everything added with `claude mcp add -s local`.
        names = set(cfg.get("mcpServers", {}))
        for pdata in cfg.get("projects", {}).values():
            if isinstance(pdata, dict):
                names.update(pdata.get("mcpServers") or {})
        return sorted(names)
    if backend == "project-json":        # home is <project>/.claude; repo text, so filter names
        try:
            names = read_project_mcp(home.parent / ".mcp.json")[1]["mcpServers"]
        except ProjectMcpError:
            return []
        return sorted(n for n in names if n.isprintable() and _valid_name(n))
    if backend == "json":                # home is ~/.copilot; user-edited text, so filter names
        try:
            names = read_project_mcp(home / "mcp-config.json")[1]["mcpServers"]
        except ProjectMcpError:
            return []
        return sorted(n for n in names if n.isprintable() and _valid_name(n))
    if backend == "toml":
        config = home / "config.toml"
        if not config.is_file():
            return []
        try:
            # Same text-level approach as the write path: no TOML parser needed.
            return sorted(set(MCP_TOML_RE.findall(config.read_text(encoding="utf-8"))))
        except OSError:
            return []
    return []


class DirView(NamedTuple):
    """One candidate dir as the harness that physically owns it sees it."""
    owner: str            # harness whose state entry / park dir this item belongs to
    live: Path            # the dir, spelled by the owner (never a symlink if avoidable)
    parked: Path          # sibling `<live>-disabled`
    sharers: list[str]    # every harness whose dir resolves to the same real path


def dir_view(table: dict, harness: str, type_: str, home: Path, sub: str) -> DirView:
    """Dedupe by real path: two harnesses whose dirs resolve to the same place share ONE
    park dir and ONE state entry, owned by the harness whose home really holds it
    (else the first non-symlink spelling, else the first in table order)."""
    mine = home / sub
    real = mine.resolve()
    found = [(harness, mine)]
    for hn, h in table.items():
        for s in h.dirs.get(type_, ()):
            d = (home if hn == harness else h.home) / s
            if (hn, d) != (harness, mine) and d.resolve() == real:
                found.append((hn, d))
    order = list(table)                 # table order, so the owner never depends on who asks
    found.sort(key=lambda f: order.index(f[0]) if f[0] in order else len(order))
    plain = [(hn, d) for hn, d in found if not d.is_symlink()]
    owner, live = next(((hn, d) for hn, d in plain
                        if hn in table and real.is_relative_to(table[hn].home.resolve())),
                       plain[0] if plain else found[0])
    return DirView(owner, live, live.parent / f"{live.name}-disabled",
                   sorted({hn for hn, _ in found}))


def sync_marker_note(out: Result, v: DirView) -> str | None:
    """The `.synced-from-*` warning for the real dir `v.live`, once per run however many
    harnesses view it (they are all named in it); None when clean or already shown."""
    if not (markers := fs.sync_markers(v.live)) or not out.first(("sync", v.live.resolve())):
        return None
    viewers = f" (viewed by {', '.join(v.sharers)})" if len(v.sharers) > 1 else ""
    return (f"{v.live}{viewers} carries {', '.join(markers)} -- a sync job may re-create "
            f"parked items; park in the source harness instead")


def _logged_move(state: dict, key: str, action: str, entry: dict | None, src: Path,
                 dest_dir: Path, dry_run: bool, out: Result) -> Path:
    """move() under a write-ahead state entry (G13). The refusals run first and touch
    nothing; a move that fails part-way is settled from what is on disk."""
    target = move(src, dest_dir, True)
    if dry_run:
        return target
    if entry is None:                    # an untracked parked item: nothing to record
        return move(src, dest_dir)
    _begin(state, key, action, entry)
    try:
        return move(src, dest_dir)
    except OSError:
        settle(state, key, out, inline=True)
        raise


def _begin(state: dict, key: str, action: str, entry: dict) -> None:
    """store.begin; if the state save itself fails, nothing was touched: drop it."""
    try:
        begin(state, key, action, entry)
    except OSError:
        end(state, key)
        raise


def _exists(p: Path) -> bool:
    return p.exists() or p.is_symlink()


def _enable_cmd(key: str, entry: dict) -> str:
    harness, digest, type_, name = parse_key(key)
    return " ".join(["agent-toggle", "enable", type_, shlex.quote(name), "--harness", harness]
                    + (["--project", shlex.quote(entry["project"])] if digest else []))


def settle(state: dict, key: str, out: Result | None = None, *,
           dry_run: bool = False, inline: bool = False) -> tuple[str, str]:
    """Resolve the write-ahead entry `key` from what is on disk: (verdict, message).

    `done`: the op landed -- record it; `undone`: it did not -- drop the record (and
    a half copy); `stuck`: kept, and the message names the exact fix. Never
    guesses: a flag's prior value is the `was` saved before the write. The entry
    is untrusted (state is a user file), so it passes the same refusals as a
    replay before anything is touched. dry_run (status / doctor) only reports;
    `inline` (the op just failed in this run, its row says why) skips the `undone` note."""
    p = state.get("pending", {}).get(key)
    action, entry = (p.get("action"), p.get("entry")) if isinstance(p, dict) else (None, None)
    mech = entry.get("mechanism") if isinstance(entry, dict) else None
    fix = f"fix: check it, then delete {json.dumps(key)} under \"pending\" in {fs.state_file()}"
    try:
        if action not in ("disable", "enable") or entry.get("connector") or not (
                mech in ("flag", "move")
                or (mech == "remove_backup" and entry.get("backend") == "project-json")):
            raise ValueError("not a pending disable/enable of a flag, a move or a "
                             "project .mcp.json entry")
        if parse_key(key)[1] is None:
            table = harnesses()
        elif isinstance(entry.get("project"), str):
            table = {"claude": project_view(Path(entry["project"]))}
        else:
            raise ValueError("project-scope key without a project")
        need = {"move": ("parked_at", "origin"), "remove_backup": ("backup", "project")}
        if why := _entry_reason(entry, table, key, need.get(mech, ())):
            raise ValueError(f"refused: {why}")
        if mech == "remove_backup" and Path(entry["backup"]) != _project_backup_path(
                parse_key(key)[1], entry["harness"], entry["name"]):
            raise ValueError("refused: backup is not the one declared for this server")
        own = fs.companion_dir() / key.replace(":", "_")    # what park_companions uses
        if any(not fs.contained(Path(c["to"]), own) for c in entry.get("companions") or []):
            raise ValueError(f"refused: a companion outside {own}")
        if action == "enable" and entry != state["disabled"].get(key):
            raise ValueError("refused: the pending enable is not the recorded entry")
    except CliError as e:
        return _report(out, dry_run, "stuck", f"pending {key}: {e.msg}; {fix}")
    except (ValueError, TypeError, KeyError, AttributeError) as e:
        return _report(out, dry_run, "stuck", f"pending {key}: {e}; {fix}")
    what = f"interrupted {action} of {entry['harness']} {entry['type']} {entry['name']}"
    if mech == "flag":
        f = entry["flag"]
        file, ptr, was = Path(f["file"]), tuple(f["pointer"]), f["was"]
        try:
            landed = read_flag(file, ptr) is (False if action == "disable" else was)
        except FlagError as e:
            return _report(out, dry_run, "stuck",
                           f"{what}: {e}; fix: make sure {'.'.join(ptr)} is "
                           f"{json.dumps(was)} in {file} (its value before the run)")
    elif mech == "remove_backup":         # project .mcp.json: is the server in the file?
        try:
            servers = read_project_mcp(Path(entry["project"]) / ".mcp.json")[1]["mcpServers"]
        except ProjectMcpError as e:
            return _report(out, dry_run, "stuck", f"{what}: {e}; {fix}")
        landed = (entry["name"] in servers) == (action == "enable")
    else:
        parked, origin = Path(entry["parked_at"]), Path(entry["origin"])
        src, dst = (origin, parked) if action == "disable" else (parked, origin)
        if _exists(src) and _exists(dst):     # only between fs._copy_move's two renames
            return _report(out, dry_run, "stuck",
                           f"{what}: both {src} and {dst} exist (the copy is complete); "
                           f"fix: check `diff -r {shlex.quote(str(src))} "
                           f"{shlex.quote(str(dst))}`, then keep one -- "
                           f"`rm -rf {shlex.quote(str(src))}` finishes the "
                           f"{action}, `rm -rf {shlex.quote(str(dst))}` undoes it")
        landed = _exists(dst)
    disabled = landed == (action == "disable")
    if dry_run:
        return _report(out, True, "done" if landed else "undone",
                       f"{what}: {'finished on disk' if landed else 'never reached disk'}; "
                       f"the next agent-toggle change records it as "
                       f"{'disabled' if disabled else 'enabled'}"
                       + (f"; to restore it now: `{_enable_cmd(key, entry)}`" if disabled
                          else "; nothing to fix"))
    if mech == "move":
        with contextlib.suppress(OSError):    # a stray dot-named copy, never the item
            fs.remove_leftover(fs.move_leftovers(src, dst)[1 if landed else 0])
    if mech == "remove_backup":
        if landed and action == "disable":
            state["disabled"][key] = entry
        elif action == "disable" and key not in state["disabled"]:    # never reached the
            with contextlib.suppress(OSError):                         # file: backup is moot
                Path(entry["backup"]).unlink(missing_ok=True)
        elif landed:
            state["disabled"].pop(key, None)
    elif landed and action == "disable":   # companions: the ones the killed run moved
        state["disabled"][key] = {**entry, "companions": [
            c for c in entry.get("companions") or [] if _exists(Path(c["to"]))]}
    elif landed:
        state["disabled"].pop(key, None)
        if mech == "move":
            stop = fs.parked_dir() if entry.get("project") else next(
                (d for d in parked.parents if d.name.endswith("-disabled")), None)
            if stop is not None:
                prune_empty(parked.parent, stop)
            # companions the killed run had not moved back yet
            restore_companions({"companions": [c for c in entry.get("companions", [])
                                               if _exists(Path(c["to"]))]}, out)
    end(state, key)
    log(action, entry["type"], entry["name"], "recovered",
        "landed" if landed else "rolled back", harness=entry["harness"],
        project=entry.get("project"), scope="project" if entry.get("project") else "user")
    return _report(None if inline and not landed else out, False,
                   "done" if landed else "undone",
                   f"recovered {what}: {'finished' if landed else 'rolled back'}, "
                   f"now {'disabled' if disabled else 'enabled'}")


def _report(out: Result | None, dry_run: bool, verdict: str, msg: str) -> tuple[str, str]:
    if out is not None:
        out.warn(msg)
    return verdict, msg


def recover(state: dict, out: Result, dry_run: bool = False) -> None:
    """Settle every write-ahead entry a killed run left (call under the lock)."""
    for key in list(state.get("pending", {})):
        settle(state, key, out, dry_run=dry_run)


def toggle_flag(action: str, type_: str, names: list[str], state: dict, harness: str,
                out: Result, dry_run: bool = False, *, batch: str = BATCH,
                optional: bool = False) -> tuple[int, list[str]]:
    """Flip one boolean in the harness's strict-JSON config (mechanism `flag`).

    Returns (fails, rest). With `optional` (a `move` type that ALSO has a flag),
    a name with no flag entry (disable) or no flag state entry (enable) is not
    handled here: it comes back in `rest` for the dir-move path. Otherwise every
    name is handled and a missing/unsafe flag is an error row. The flag shapes
    are ASSUMED (DESIGN s4/s11); JSONC is edited in place, JSON5 refused by flag_json."""
    table = harnesses()
    h = table[harness]
    rest: list[str] = []
    fails = 0

    def fail(name: str, msg: str) -> int:
        return fail_row(out, dry_run, harness, type_, action, name, msg, batch=batch)

    for name in names:
        fs.refresh_lock()
        key = f"{harness}:{type_}:{name}"
        file, pointer = flag_target(h, type_, name)
        entry = state["disabled"].get(key)
        if file.name not in h.editable:
            if optional:
                rest.append(name)
            else:
                fails += fail(name, f"{file.name} is not an editable {harness} file")
            continue
        if action == "disable":
            if entry:               # never overwrite another mechanism's record
                fails += fail(name, "already disabled (see `agent-toggle list`)")
                continue
            try:
                was = read_flag(file, pointer)
            except FlagError as e:
                if not optional:
                    fails += fail(name, str(e))
                elif isinstance(e, FlagMissing):
                    rest.append(name)               # no flag entry: plain dir move
                else:                               # unsafe config: keep the dir move, say why
                    out.warn(f"{e}; parking {name} by dir move instead")
                    rest.append(name)
                continue
            if not was:
                fails += fail(name, f"{'.'.join(pointer)} is already false in {file}")
            elif dry_run:
                _ok(out, dry_run, harness, type_, action, name, "")
            else:
                new = {
                    "mechanism": "flag", "harness": harness, "type": type_, "name": name,
                    "flag": {"file": str(file), "pointer": list(pointer), "was": was},
                    "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                }
                try:
                    _begin(state, key, "disable", new)
                except OSError as e:
                    fails += fail(name, f"state not saved, nothing changed: {e}")
                    continue
                try:
                    set_flag(file, pointer, False)
                except (FlagError, fs.WriteError) as e:
                    settle(state, key, out, inline=True)
                    fails += fail(name, str(e))
                    continue
                state["disabled"][key] = new
                end(state, key)
                _ok(out, dry_run, harness, type_, action, name,
                    f"disabled (set {'.'.join(pointer)} to false in {file})")
                log(action, type_, name, "ok", str(file), harness=harness, batch=batch)
            continue
        if optional and not (entry or {}).get("flag"):
            rest.append(name)
            continue
        # _flag_reason is NOT gated on the entry's own mechanism/connector fields
        # (they are untrusted): a tampered entry must refuse, never crash or replay.
        refused = _refusal(entry, table, key, ())
        if not refused and entry and (why := _flag_reason(entry, table)):
            refused = f"refused: {why}"
        if refused:
            fails += fail(name, refused)
        elif not entry:
            fails += fail(name, f"no flag state entry to restore (if an interrupted run left "
                                f"{'.'.join(pointer)} false in {file}, set it to true by hand)")
        elif dry_run:
            _ok(out, dry_run, harness, type_, action, name, "")
        else:
            try:
                _begin(state, key, "enable", entry)
            except OSError as e:
                fails += fail(name, f"state not saved, nothing changed: {e}")
                continue
            try:
                set_flag(file, pointer, entry["flag"]["was"])
            except (FlagError, fs.WriteError) as e:
                settle(state, key, out, inline=True)
                fails += fail(name, str(e))
                continue
            state["disabled"].pop(key, None)
            end(state, key)
            _ok(out, dry_run, harness, type_, action, name,
                f"enabled (restored {'.'.join(pointer)} in {file})")
            log(action, type_, name, "ok", str(file), harness=harness, batch=batch)
    return fails, rest


def toggle_dir_type(action: str, type_: str, names: list[str], state: dict,
                    harness: str, home: Path, out: Result | None = None,
                    dry_run: bool = False, *, batch: str = BATCH,
                    table: dict | None = None) -> int:
    """Park / restore skills, agents, commands. dry_run plans and writes nothing.

    `table` = {"claude": harnesses.project_view(dir)} runs in project scope: items
    park under fs.parked_dir()/<sha8>/<sub>-disabled, NEVER inside the project, keys
    are `claude@<sha8>:...`, and no user-scope dir is ever a view (no dir_view aliasing).
    """
    out = out or Result()
    table = table or harnesses()
    proj = table[harness].project
    pstr = str(proj) if proj else None
    fails = 0

    def fail(name: str, msg: str) -> int:
        return fail_row(out, dry_run, harness, type_, action, name, msg, batch=batch,
                        project=pstr)

    if type_ in table[harness].flags:            # openclaw skill: flag when an entry exists
        fails, names = toggle_flag(action, type_, names, state, harness, out, dry_run,
                                   batch=batch, optional=True)
    if proj:
        park = fs.parked_dir() / project_digest(proj)
        subs = table[harness].dirs[type_]
        views = [DirView(harness, home / sub, park / f"{sub}-disabled", [harness])
                 for sub in subs if (home / sub).resolve().is_relative_to(proj)]
        if not views:                            # e.g. .claude/skills -> ~/.claude/skills
            for name in names:
                fails += fail(name, f"{home / subs[0]} resolves outside the project {proj}")
            return fails
    else:
        views = [dir_view(table, harness, type_, home, sub) for sub in table[harness].dirs[type_]]

    if action == "disable" and names and not proj:
        for v in views:
            ohome = home if v.owner == harness else table[v.owner].home
            # one warning per real park dir, and only where the fix applies: the dir sits
            # inside a git work tree (else `git status` cannot be dirtied, nothing to fix)
            top = fs.git_toplevel(ohome)
            if top and v.parked.resolve().is_relative_to(top) \
                    and out.first(("ignore", v.parked.resolve())) \
                    and not gitignored(v.parked, top):
                out.warn(f"{v.parked} is NOT gitignored -- disabling will dirty `git status`. "
                         f"fix: echo '{v.parked.name}/' >> {top}/.gitignore")
            if msg := sync_marker_note(out, v):
                out.warn(msg)

    for name in names:
        fs.refresh_lock()
        if action == "disable":
            v, src = next(((v, p) for v in views if (p := resolve_item(v.live, name))),
                          (views[0], None))
            if src is None:
                fails += fail(name, "not found under " + " or ".join(str(v.live) for v in views))
                continue
            key = make_key(v.owner, type_, name, project=proj)
            others = [h for h in v.sharers if h != harness]        # relative to this request
            ohome = home if v.owner == harness else table[v.owner].home
            # Preserve nesting: commands/orch/batch.md parks as orch/batch.md,
            # so two different <group>/mcp.md cannot collide at the park root.
            rel = src.relative_to(v.live)
            if proj and not fs.contained(v.parked / rel, fs.parked_dir()):
                fails += fail(name, f"{fs.parked_dir()} is reached through a symlink -- refusing")
                continue
            if proj and not dry_run:
                fs.private_dir(fs.parked_dir())
            new = {
                "mechanism": "move", "harness": v.owner, "type": type_, "name": name,
                "parked_at": str(v.parked / rel), "origin": str(src),
                "companions": [], **({"project": pstr} if proj else {}),
                **({"shared_with": [h for h in v.sharers if h != v.owner]}
                   if len(v.sharers) > 1 else {}),
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
            try:
                # Across filesystems (a project vs ~/.agent-toggle) this is fs._copy_move:
                # ownership/xattrs may differ from a rename; see its crash guarantees.
                target = _logged_move(state, key, "disable", new, src,
                                      (v.parked / rel).parent, dry_run, out)
            except OSError as e:
                fails += fail(name, str(e))
                continue
            row = _ok(out, dry_run, harness, type_, action, name, _shared_text(
                      "disabled", v.owner, harness, others), parked_at=str(target),
                      **({"shared_with": others, "owner": v.owner} if others else {}))
            # project scope moves the item only: repo text must not steer other tracked
            # files out of a shared repo (companions are a user-scope heuristic)
            def ahead(rec: dict, new=new, key=key) -> None:   # write-ahead per companion
                new["companions"].append(rec)
                begin(state, key, "disable", new)
            companions = [] if proj else park_companions(
                target, ohome, key.replace(":", "_"), out, dry_run, src if dry_run else None,
                before_move=ahead)
            row["companions"] = companions
            if proj:
                row["project"] = pstr
                out.warn(f"{src} {'would be' if dry_run else 'is'} a tracked deletion in git "
                         f"status; restore with: "
                         f"agent-toggle enable {type_} {name} --project {proj}")
            if not dry_run:
                state["disabled"][key] = {**new, "companions": companions}
                end(state, key)
                log(action, type_, name, "ok", str(target), harness=harness, batch=batch,
                    project=pstr, scope="project" if proj else "user")
        else:
            # The entry lives under the owning harness; a request through an alias
            # (or a pre-alias entry under the requested harness) finds the same one.
            keys = dict.fromkeys([make_key(v.owner, type_, name, project=proj) for v in views]
                                 + [make_key(harness, type_, name, project=proj)])
            key = next((k for k in keys if k in state["disabled"]), next(iter(keys)))
            entry = state["disabled"].get(key)
            if refused := _refusal(entry, table, key, ("parked_at", "origin")):
                fails += fail(name, refused)
                continue
            if entry:
                src = Path(entry["parked_at"])
                v = next((v for v in views if src.is_relative_to(v.parked)), views[0])
            else:
                v, src = next(((v, p) for v in views if (p := resolve_item(v.parked, name))),
                              (views[0], None))
            if src is None or not (src.exists() or src.is_symlink()):
                fails += fail(name, "nothing parked to restore")
                continue
            others = [h for h in v.sharers if h != harness]
            dest_parent = (Path(entry["origin"]).parent if entry
                           else (v.live / src.relative_to(v.parked)).parent)
            try:
                target = _logged_move(state, key, "enable", entry, src, dest_parent, dry_run,
                                      out)
            except OSError as e:
                fails += fail(name, str(e))
                continue
            if not dry_run:       # a project's park dirs go too, up to parked_dir() (G12)
                prune_empty(src.parent, fs.parked_dir() if proj else v.parked)
            _ok(out, dry_run, harness, type_, action, name,
                _shared_text("enabled", v.owner, harness, others), restored_to=str(target),
                **({"shared_with": others, "owner": v.owner} if others else {}),
                **({"project": pstr} if proj else {}))
            if entry:
                restore_companions(entry, out, dry_run)
                if not dry_run:
                    state["disabled"].pop(key, None)
                    end(state, key)
            if not dry_run:
                log(action, type_, name, "ok", str(target), harness=harness, batch=batch,
                    project=pstr, scope="project" if proj else "user")
    return fails


def _shared_text(verb: str, owner: str, harness: str, others: list[str]) -> str:
    if not others:
        return verb
    where = f"parked under {owner}; " if owner != harness else ""
    return f"{verb} (shared dir: {where}also affects {', '.join(others)})"


def toggle_plugin(action: str, names: list[str], state: dict, harness: str,
                  out: Result | None = None, dry_run: bool = False, *,
                  batch: str = BATCH) -> int:
    """Claude Code has a native, fully reversible plugin disable -- drive it."""
    out = out or Result()
    if "plugin" in harnesses()[harness].flags:
        return toggle_flag(action, "plugin", names, state, harness, out, dry_run, batch=batch)[0]
    if harness != "claude":
        for name in names:
            fail_row(out, dry_run, harness, "plugin", action, name,
                  f"{harness} has no plugin CLI -- park it as a skill instead",
                  "unsupported", batch=batch)
        return len(names)
    fails = 0
    tick = checkpoints(state, dry_run)
    for name in names:
        tick()
        if not valid_plugin_id(name):
            fails += fail_row(out, dry_run, harness, "plugin", action, name,
                              "refused: plugin id has characters outside [A-Za-z0-9._@:/-]",
                              batch=batch)
            continue
        if dry_run:
            if claude_bin():
                _ok(out, dry_run, harness, "plugin", action, name, "")
            else:
                fails += fail_row(out, dry_run, harness, "plugin", action, name,
                               "claude CLI not found", batch=batch)
            continue
        ok, res = run_cli(claude_bin(), ["plugin", action, name])
        if not ok:
            fails += fail_row(out, dry_run, harness, "plugin", action, name,
                           res.splitlines()[-1] if res else "failed", batch=batch)
            continue
        key = f"{harness}:plugin:{name}"
        if action == "disable":
            state["disabled"][key] = {
                "mechanism": "native_cli", "harness": harness, "type": "plugin",
                "name": name, "native": True,
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
        else:
            state["disabled"].pop(key, None)
            # parked as the bare `foo`, enabled as `foo@mkt`: the same plugin when it is the
            # only listed id for that name (cost.inventory's rule); ambiguous stays separate.
            # ponytail: no write-ahead entry -- plugins have none (the CLI is the op), a kill
            # before the per-item save leaves the stale bare entry; `enable foo` clears it.
            bare = name.partition("@")[0]
            if "@" in name and f"{harness}:plugin:{bare}" in state["disabled"] \
                    and full_plugin_id(bare, [p["id"] for p in list_plugins(lambda m: None)]) == name:
                del state["disabled"][f"{harness}:plugin:{bare}"]
        _ok(out, dry_run, harness, "plugin", action, name, f"{action}d")
        log(action, "plugin", name, "ok", harness=harness, batch=batch)
    return fails


def toggle_mcp(action: str, names: list[str], state: dict,
               harness: str, home: Path, backend: str | None,
               out: Result | None = None, dry_run: bool = False, *,
               batch: str = BATCH) -> int:
    """Remove / re-add MCP servers. dry_run plans and writes nothing."""
    out = out or Result()
    if "mcp" in harnesses()[harness].flags:
        return toggle_flag(action, "mcp", names, state, harness, out, dry_run, batch=batch)[0]
    if backend is None:
        for name in names:
            fail_row(out, dry_run, harness, "mcp", action, name,
                  f"{harness} stores MCP outside a togglable config "
                  f"(sqlite / none) -- not supported", "unsupported", batch=batch)
        return len(names)

    def fail(name: str, msg: str) -> int:
        return fail_row(out, dry_run, harness, "mcp", action, name, msg, batch=batch)

    if backend not in ("claude-json", "toml"):     # fail closed: no id reaches an else branch
        for name in names:                         # (before any directory is created)
            fail(name, f"{harness} mcp backend {backend!r} is not handled here")
        return len(names)

    backup_dir = fs.backup_dir()
    if not dry_run:
        fs.private_dir(backup_dir)
    fails = 0

    table = harnesses()
    tick = checkpoints(state, dry_run)
    for name in names:
        tick()
        key = f"{harness}:mcp:{name}"
        entry = state["disabled"].get(key)
        need = () if (entry or {}).get("connector") else ("backup",)
        if action == "enable" and (refused := _refusal(entry, table, key, need)):
            fails += fail(name, refused)
            continue

        if backend == "claude-json" and name in claudeai_connector_names():
            ok, detail = toggle_claudeai_connector(action, name, dry_run)
            if not ok:
                fails += fail(name, str(detail))
                continue
            n = len(detail)
            if action == "disable":
                if not dry_run:
                    state["disabled"][key] = {
                        "mechanism": "flag", "harness": harness, "type": "mcp",
                        "name": name, "connector": True,
                        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    }
                _ok(out, dry_run, harness, "mcp", action, name,
                    f"disabled across {n} project(s) "
                    f"(claude.ai connector -- account stays connected, "
                    f"just parked per-project; new project dirs need a re-run)",
                    connector=True, projects=detail)
            else:
                if not dry_run:
                    state["disabled"].pop(key, None)
                _ok(out, dry_run, harness, "mcp", action, name,
                    f"restored across {n} project(s) "
                    f"(open a NEW session for it to load)",
                    connector=True, projects=detail)
            if not dry_run:
                log(action, "mcp", name, "ok", json.dumps(detail), harness=harness,
                    batch=batch)
            continue

        bp = backup_dir / f"{harness}__{name.replace('/', '_')}.json"
        scope, project = "user", None

        if action == "disable":
            if backend == "claude-json":
                try:
                    found = claude_mcp_config(name)
                except LookupError as e:
                    fails += fail(name, str(e))
                    continue
                if found is None:
                    hint = ("not in ~/.claude.json (user or local scope) -- "
                            "a repo's own .mcp.json needs --project <dir>")
                    if not dry_run:      # a dry run never shells out to claude
                        ok, cfg = run_cli(claude_bin(), ["mcp", "get", name])
                        if ok and "claude.ai config" in cfg:
                            hint = ("account-level claude.ai connector -- disable it "
                                    "at claude.ai (Settings -> Connectors)")
                    fails += fail(name, hint)
                    continue
                raw, scope, project = found
                if dry_run:
                    if not claude_bin():
                        fails += fail(name, "claude CLI not found")
                        continue
                else:
                    fs.atomic_write(bp, json.dumps(raw, indent=2, ensure_ascii=False))
                    ok, res = run_cli(claude_bin(), ["mcp", "remove", name, "-s", scope],
                                      cwd=project)
                    if not ok:
                        fails += fail(name, redact(res, raw)[:100])
                        continue
            else:  # toml
                config = home / "config.toml"
                # plan first, back up, THEN edit: the block is never only in memory
                block = codex_mcp_remove(config, name, True) if config.exists() else None
                if block is None:
                    fails += fail(name, f"no [mcp_servers.{name}] in {config}")
                    continue
                if not dry_run:
                    fs.atomic_write(bp, json.dumps({"toml": block}, ensure_ascii=False,
                                                   indent=2))
                    try:
                        codex_mcp_remove(config, name)
                    except fs.WriteError as e:      # config rolled back; drop the backup
                        bp.unlink(missing_ok=True)
                        fails += fail(name, str(e))
                        continue

            where = f" [{scope}{f': {project}' if project else ''}]"
            if not dry_run:
                state["disabled"][key] = {
                    "mechanism": "remove_backup", "harness": harness, "type": "mcp",
                    "name": name, "backend": backend, "backup": str(bp),
                    "scope": scope, "project": project,
                    "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                }
                # claude local scope logs its real scope + cwd; undo treats every
                # scope but `project` (a --project change) as user scope, which is right
                # for local: the replay finds the user-keyed entry and its own cwd.
                log(action, "mcp", name, "ok", str(bp), harness=harness, batch=batch,
                    project=project, scope=scope)
            _ok(out, dry_run, harness, "mcp", action, name,
                f"removed{where} (config saved to {bp})",
                scope=scope, project=project, backup=str(bp))
        else:
            entry = state["disabled"].get(key)
            path = Path(entry["backup"]) if entry else bp
            if not path.is_file():
                fails += fail(name, f"backup missing at {path}")
                continue
            try:
                payload = path.read_text(encoding="utf-8")
            except (OSError, ValueError) as e:       # unreadable / not UTF-8: this row only
                fails += fail(name, f"backup {path} is unreadable ({type(e).__name__}: {e})")
                continue
            if (entry or {}).get("backend", backend) == "toml":
                if not dry_run:
                    try:
                        block = json.loads(payload)["toml"]
                        if not isinstance(block, str):
                            raise TypeError("toml is not text")
                    except (ValueError, KeyError, TypeError) as e:
                        fails += fail(name, f"backup {path} is unusable ({type(e).__name__}: {e})")
                        continue
                    try:
                        codex_mcp_add(home / "config.toml", block)
                    except (fs.WriteError, OSError, ValueError) as e:   # the config, not the backup
                        fails += fail(name, str(e))
                        continue
            else:
                # Entries parked before scope tracking have neither field;
                # they were user-scope by construction, so that is the default.
                scope = (entry or {}).get("scope") or "user"
                project = (entry or {}).get("project")
                if project and not Path(project).is_dir():
                    fails += fail(name, f"project {project} is gone -- "
                                        f"re-add it by hand from {path}")
                    continue
                if dry_run:
                    if not claude_bin():
                        fails += fail(name, "claude CLI not found")
                        continue
                else:
                    ok, res = run_cli(claude_bin(),
                                      ["mcp", "add-json", "--scope", scope, name, payload],
                                      cwd=project)
                    if not ok:
                        fails += fail(name, redact(res, payload)[:120])
                        continue
            if not dry_run:
                state["disabled"].pop(key, None)
                log(action, "mcp", name, "ok", harness=harness, batch=batch,
                    project=project, scope=scope)
            _ok(out, dry_run, harness, "mcp", action, name,
                "restored (open a NEW session for it to load)", backup=str(path))
    return fails


def toggle_project_mcp(action: str, names: list[str], state: dict, harness: str,
                       out: Result, dry_run: bool, table: dict, *, batch: str = BATCH) -> int:
    """`--project <dir>` MCP servers: remove_backup on the repo's own <dir>/.mcp.json.

    A direct, verified JSON edit (fs.checked_write, rolled back on any failure) with
    NO claude CLI call, under a write-ahead `pending` entry (store.begin / settle).
    disable cuts only the entry's bytes and saves just that entry -- its raw value,
    its exact text and its neighbours' names, never the rest of the file (G15) -- in
    a private backup under fs.backup_dir() (never inside the project); enable puts
    those bytes back into the current file, so an unchanged file comes back byte for
    byte. An old backup holding the whole before/after text still restores as it
    did. State keys carry the project digest, so a project entry never meets the
    user-scope `claude:mcp:<name>` one (nor claude `local` scope).
    """
    project = table[harness].project
    pstr, file, digest = str(project), project / ".mcp.json", project_digest(project)
    fails = 0

    def fail(name: str, msg: str) -> int:
        return fail_row(out, dry_run, harness, "mcp", action, name, msg, batch=batch,
                        project=pstr)

    tick = checkpoints(state, dry_run)
    for name in names:
        tick()
        key = make_key(harness, "mcp", name, project=project)
        entry = state["disabled"].get(key)
        bp = _project_backup_path(digest, harness, name)
        if action == "disable":
            try:
                raw, before, after = project_mcp_remove(file, name)
            except ProjectMcpError as e:
                fails += fail(name, str(e))
                continue
            if entry:
                fails += fail(name, "already disabled in state -- enable it first")
                continue
            if not dry_run:
                fs.private_dir(fs.backup_dir())
                fs.atomic_write(bp, json.dumps({"project_json": project_mcp_backup(before, name)},
                                               ensure_ascii=False, indent=2))
                new = {
                    "mechanism": "remove_backup", "harness": harness, "type": "mcp",
                    "name": name, "backend": "project-json", "backup": str(bp),
                    "scope": "project", "project": pstr,
                    "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                }
                _begin(state, key, "disable", new)
                try:
                    write_project_mcp(file, after, before, name, raw)
                except fs.WriteError as e:   # settled from disk: rolled back -> backup dropped
                    settle(state, key, out, inline=True)
                    fails += fail(name, str(e))
                    continue
                state["disabled"][key] = new
                end(state, key)
                log(action, "mcp", name, "ok", str(bp), harness=harness, batch=batch,
                    project=pstr, scope="project")
            _ok(out, dry_run, harness, "mcp", action, name,
                f"removed from {file} (config saved to {bp})", project=pstr, backup=str(bp))
            out.warn(f"{file} {'would be' if dry_run else 'is'} a tracked change in git status; "
                     f"restore with: agent-toggle enable mcp {name} --project {project}")
            continue
        # enable
        if not entry:
            fails += fail(name, f"{name} is not disabled in project {pstr}")
            continue
        refused = _refusal(entry, table, key, ("backup",))
        if not refused and Path(entry["backup"]) != bp:
            refused = ("refused: backup belongs to another project"
                       if Path(entry["backup"]).name.split("__")[0] != digest
                       else "refused: backup is not the one declared for this server")
        if refused:
            fails += fail(name, refused)
            continue
        try:
            payload = json.loads(Path(entry["backup"]).read_text(encoding="utf-8"))["project_json"]
            if not (isinstance(payload["entry"], dict) and _project_backup_ok(payload, name)):
                raise TypeError("bad field")
        except OSError:
            fails += fail(name, f"backup missing at {entry['backup']}")
            continue
        except (ValueError, KeyError, TypeError):
            fails += fail(name, f"refused: backup {entry['backup']} is not a project .mcp.json backup")
            continue
        try:
            current, text = project_mcp_restore(file, name, payload)
        except ProjectMcpError as e:
            fails += fail(name, str(e))
            continue
        if not dry_run:
            _begin(state, key, "enable", entry)
            try:
                write_project_mcp(file, text, current, name, payload["entry"])
            except fs.WriteError as e:
                settle(state, key, out, inline=True)
                fails += fail(name, str(e))
                continue
            state["disabled"].pop(key, None)
            end(state, key)
            log(action, "mcp", name, "ok", harness=harness, batch=batch, project=pstr,
                scope="project")
        _ok(out, dry_run, harness, "mcp", action, name,
            "restored (open a NEW session for it to load)", project=pstr,
            backup=entry["backup"])
    return fails


def _project_backup_path(digest: str, harness: str, name: str) -> Path:
    return fs.backup_dir() / f"{digest}__{harness}__{name.replace('/', '_')}.json"


def _project_backup_ok(payload: dict, name: str) -> bool:
    """A project backup's fields have the right types: the per-entry form (G15) or
    the old whole-file `before` / `after` form, which still restores."""
    if "cut" in payload:
        return payload.get("name") == name and isinstance(payload["cut"], str) and all(
            f in payload and (payload[f] is None or isinstance(payload[f], str))
            for f in ("prev", "next"))
    return isinstance(payload["before"], str) and isinstance(payload["after"], str)


def toggle_json_mcp(action: str, names: list[str], state: dict, harness: str,
                    out: Result, dry_run: bool, table: dict, *, batch: str = BATCH) -> int:
    """User-scope strict-JSON MCP file (backend `json`, e.g. copilot mcp-config.json).

    Same direct, verified edit as toggle_project_mcp (fs.checked_write, rolled back on
    failure, NO claude CLI call), but the file comes from the harness TABLE
    (h.mcp.file, which must be in h.editable), never from the state entry. The backup
    `<harness>__<name>.json` (0600) holds the raw entry plus the exact before/after
    text; enable restores the pre-disable bytes when the file is unchanged since, else
    merges the entry in. A JSONC / symlinked / oversized file is refused untouched."""
    h = table[harness]
    file = h.mcp.file
    fails = 0

    def fail(name: str, msg: str) -> int:
        return fail_row(out, dry_run, harness, "mcp", action, name, msg, batch=batch)

    tick = checkpoints(state, dry_run)
    for name in names:
        tick()
        if h.backend != "json" or file.name not in h.editable:
            fails += fail(name, f"{file.name} is not an editable {harness} file")
            continue
        key = make_key(harness, "mcp", name)
        entry = state["disabled"].get(key)
        bp = fs.backup_dir() / f"{harness}__{name.replace('/', '_')}.json"
        if action == "disable":
            try:
                raw, before, after = project_mcp_remove(file, name)
            except ProjectMcpError as e:
                fails += fail(name, str(e))
                continue
            if entry:
                fails += fail(name, "already disabled in state -- enable it first")
                continue
            if not dry_run:
                fs.private_dir(fs.backup_dir())
                fs.atomic_write(bp, json.dumps(
                    {"json": {"entry": raw, "before": before, "after": after}},
                    ensure_ascii=False, indent=2))
                try:
                    write_project_mcp(file, after, before)
                except fs.WriteError as e:          # file rolled back; drop the backup
                    if "restore failed" not in str(e):   # else it holds the only good copy
                        bp.unlink(missing_ok=True)
                    fails += fail(name, str(e))
                    continue
                state["disabled"][key] = {
                    "mechanism": "remove_backup", "harness": harness, "type": "mcp",
                    "name": name, "backend": "json", "backup": str(bp),
                    "scope": "user", "project": None,
                    "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                }
                log(action, "mcp", name, "ok", str(bp), harness=harness, batch=batch,
                    project=None, scope="user")
            _ok(out, dry_run, harness, "mcp", action, name,
                f"removed from {file} (config saved to {bp})", scope="user", project=None,
                backup=str(bp))
            continue
        # enable
        if not entry:
            fails += fail(name, f"{name} is not disabled on {harness}")
            continue
        refused = _refusal(entry, table, key, ("backup",))
        if not refused and entry.get("backend") != "json":
            refused = "refused: entry is not a json-backend entry"
        if not refused and Path(entry["backup"]) != bp:
            refused = "refused: backup is not the one declared for this server"
        if refused:
            fails += fail(name, refused)
            continue
        try:
            payload = json.loads(bp.read_text(encoding="utf-8"))["json"]
            if not (isinstance(payload["entry"], dict) and isinstance(payload["before"], str)
                    and isinstance(payload["after"], str)):
                raise TypeError("bad field")
            # `before` is written back verbatim when the file is unchanged: it must be
            # exactly the strict-JSON config this server was removed from
            servers = json.loads(payload["before"], object_pairs_hook=_no_dup,
                                 parse_constant=_not_json, parse_float=_finite)["mcpServers"]
            if servers[name] != payload["entry"]:
                raise ValueError("before does not hold the entry")
        except OSError:
            fails += fail(name, f"backup missing at {bp}")
            continue
        except (ValueError, KeyError, TypeError, RecursionError):
            fails += fail(name, f"refused: backup {bp} is not a {harness} mcp backup")
            continue
        try:
            current, text = project_mcp_restore(file, name, payload)
            if not dry_run:
                write_project_mcp(file, text, current)
        except ProjectMcpError as e:
            msg = str(e)
            if "backup stays in state" not in msg:      # file / mcpServers key vanished
                msg += (f" -- restore {file.name} with an mcpServers object first; "
                        f"the backup stays in state")
            fails += fail(name, msg)
            continue
        except fs.WriteError as e:
            fails += fail(name, str(e))
            continue
        if not dry_run:
            state["disabled"].pop(key, None)
            log(action, "mcp", name, "ok", harness=harness, batch=batch, project=None,
                scope="user")
        _ok(out, dry_run, harness, "mcp", action, name,
            "restored (open a NEW session for it to load)", backup=str(bp))
    return fails
