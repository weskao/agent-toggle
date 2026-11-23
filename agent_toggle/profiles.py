"""Profiles (DESIGN s5.7): a portable snapshot of which items are live.

A profile holds ONLY {harness, type, name, live} per item -- never a path or a
secret -- so it can sit in a dotfiles repo. `apply` toggles just the items the
profile mentions: an item installed after the save is never touched, and an item
the profile names that this machine lacks is skipped, not failed.

Bindings (`bind` / `unbind` / `auto`) say which stored profile a project dir loads,
with one global fallback; `auto` applies the one that matches a dir.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from . import cost, fs, ops, store
from .harnesses import TYPES, harnesses, project_view
from .mechanisms import _valid_name, validate_name
from .output import Result, die

VERSION = 1
MAX_BYTES = 1 << 20                      # a real profile is a few KB
ITEM_KEYS = {"harness", "type", "name", "live"}
WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}     # not valid file names on Windows
ACTIONS = ("save", "apply", "diff", "list", "delete", "rename", "bind", "unbind", "auto")
NO_TARGET = ("list", "unbind", "auto")


def _is_path(arg: str) -> bool:
    return arg.endswith(".json") or "/" in arg or "\\" in arg


def _file_path(arg: str) -> Path:
    p = Path(arg).expanduser()
    if ".." in p.parts:
        die(f"invalid profile path {arg!r}: `..` is not allowed", 2)
    return p


def _stored_path(name: str) -> Path:
    validate_name(name)
    if ":" in name or name.split(".")[0].upper() in WIN_RESERVED or name.endswith("."):
        die(f"invalid profile name {name!r}", 2)
    try:
        name.encode("utf-8")                    # a lone surrogate is a legal NTFS file name
    except UnicodeEncodeError:
        die(f"invalid profile name {name!r}", 2)
    return fs.profiles_dir() / f"{name}.json"


def _load(arg: str) -> dict:
    p = _file_path(arg) if _is_path(arg) else _stored_path(arg)
    try:
        with open(p, "rb") as fh:
            data = fh.read(MAX_BYTES + 1)
    except OSError as e:
        die(f"cannot read profile {arg!r}: {e.strerror or e}", 2)
    if len(data) > MAX_BYTES:
        die(f"profile {arg!r} is larger than {MAX_BYTES} bytes", 2)
    try:
        return _validate(json.loads(data.decode("utf-8")))
    except (ValueError, RecursionError) as e:    # JSON, UTF-8 and absurd nesting
        die(f"profile {arg!r} is not valid JSON: {e}", 2)


def _validate(doc) -> dict:
    """Exit 2 on anything but a well-formed v1 profile (it may come from anywhere)."""
    table = harnesses()
    if not isinstance(doc, dict) or set(doc) - {"version", "saved_at", "items", "scope"}:
        die("profile must be an object with version, saved_at, items", 2)
    if doc.get("scope", "user") not in ("user", "project"):
        die(f"unknown profile scope {doc['scope']!r} (expected user or project)", 2)
    v = doc.get("version")
    if type(v) is not int or v != VERSION:
        die(f"unsupported profile version {v!r} (expected {VERSION})", 2)
    if not isinstance(doc.get("items"), list):
        die("profile items must be a list", 2)
    seen = set()
    for it in doc["items"]:
        if not isinstance(it, dict) or set(it) != ITEM_KEYS:
            die(f"profile item must have exactly {sorted(ITEM_KEYS)}", 2)
        if not isinstance(it["harness"], str) or it["harness"] not in table:
            die(f"profile names unknown harness {it['harness']!r}", 2)
        if not isinstance(it["type"], str) or it["type"] not in TYPES:
            die(f"profile names unknown type {it['type']!r}", 2)
        if not isinstance(it["name"], str):
            die("profile item name must be a string", 2)
        validate_name(it["name"])
        if not isinstance(it["live"], bool):
            die("profile item live must be true or false", 2)
        key = (it["harness"], it["type"], it["name"])
        if key in seen:
            die(f"profile lists {'/'.join(key)} twice", 2)
        seen.add(key)
    return doc


def scope(state: dict, project: Path | None) -> tuple[dict, dict]:
    """(state, harness table) of ONE scope: user scope, or `project` (a resolved
    --project dir; claude layout only). Feed both to `cost.inventory`."""
    return (store.scope_state(state, project),
            {"claude": project_view(project)} if project else harnesses())


def _inventory(out: Result, plugins: bool,
               project: Path | None = None) -> dict[tuple[str, str, str], bool]:
    """(harness, type, name) -> live now, in ONE scope: user scope, or (`project`, a
    resolved --project dir) that project's .claude only -- the two never mix.
    Reuses the cost inventory (one owner per shared dir)."""
    state, table = scope(store.load_state(write_back=False), project)
    return {(i.harness, i.type, i.name): i.enabled
            for i in cost.inventory(state, table, out.warn, plugins and not project)}


def project_dir(arg: str | None, only: str | None) -> Path | None:
    """The resolved --project dir (claude layout only, exit 4 otherwise), or None."""
    if arg is None:
        return None
    if only not in (None, "claude"):
        die(f"--project supports only the claude layout, not {only}", 4)
    return project_view(arg).project


def cmd_save(arg: str, out_file: str | None, only: str | None, out: Result,
             project: Path | None = None) -> None:
    if _is_path(arg):
        die(f"{arg!r} looks like a path: save <name> [--out <file>]", 2)
    path = _file_path(out_file) if out_file else _stored_path(arg)
    if out_file:
        validate_name(arg)
        if path.is_dir() or not path.parent.is_dir():
            die(f"cannot write {path}: not a file in an existing directory", 2)
    inv = _inventory(out, plugins=not only or only == "claude", project=project)
    items = []
    for (h, t, n), live in sorted(inv.items()):
        if only and h != only:
            continue
        if _valid_name(n):
            items.append({"harness": h, "type": t, "name": n, "live": live})
        else:                    # apply/diff would refuse the whole profile over this name
            out.warn(f"skipped {h} {t} {n!r}: not a name agent-toggle can toggle")
    doc = {"version": VERSION, "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "items": items, **({"scope": "project"} if project else {})}
    if not out_file:
        fs.private_dir(fs.profiles_dir())
    fs.atomic_write(path, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    out.say(f"saved {len(items)} item(s) -> {path}")
    out.row(None, None, arg, "save", "ok", f"{len(items)} items", show=False,
            path=str(path), items=len(items))


def load_scoped(arg: str, project: Path | None = None) -> dict:
    """The validated profile `arg`; exit 2 when its scope does not match `project`."""
    doc = _load(arg)
    if (doc.get("scope", "user") == "project") != (project is not None):
        die(f"profile {arg!r} is {doc.get('scope', 'user')}-scope: "
            + ("pass --project <dir>" if project is None else "drop --project"), 2)
    return doc


def cmd_apply(arg: str, only: str | None, dry_run: bool, out: Result,
              project: Path | None = None) -> None:
    doc = load_scoped(arg, project)
    inv = _inventory(out, plugins=not dry_run and (not only or only == "claude"),  # dry run: no CLI
                     project=project)
    plan, same, skipped = [], 0, 0
    for it in doc["items"]:
        h, t, n, live = it["harness"], it["type"], it["name"], it["live"]
        if only and h != only:
            continue
        if (h, t, n) not in inv:
            skipped += 1
            why = ("plugin state is not read in a dry run" if dry_run and t == "plugin"
                   else "not on this machine")
            out.row(h, t, n, "would-skip" if dry_run else "skip", "skipped", why)
        elif inv[h, t, n] == live:
            same += 1
        else:
            plan.append(ops.Op(h, t, "enable" if live else "disable", n,
                               str(project) if project else None))
    if plan:
        ops.apply_plan(plan, out, dry_run, batch=store.BATCH, headers=True)
    out.say(f"\n{len(plan)} {'to change' if dry_run else 'attempted'}, "
            f"{same} already as profiled, {skipped} skipped")


def stored() -> list[Path]:
    """The saved profile files, by name."""
    return sorted(fs.profiles_dir().glob("*.json")) if fs.profiles_dir().is_dir() else []


def cmd_list(out: Result) -> None:
    files = stored()
    if not files:
        out.say("no profiles saved")
    binds = bindings()
    for p in files:
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            detail = f"{len(doc['items'])} items, saved {str(doc.get('saved_at', '?'))[:10]}"
        except (OSError, ValueError, KeyError, TypeError):
            detail = "unreadable"
        dirs = sorted(d for d, n in binds["projects"].items() if n == p.stem)
        bound = {**({"global": True} if binds["global"] == p.stem else {}),
                 **({"bound": dirs} if dirs else {})}
        if bound:                           # an unbound profile's row and line stay as before
            detail += " <- " + ", ".join([*(["global"] if "global" in bound else []), *dirs])
        out.say(f"  {p.stem:<24} {detail}")
        out.row(None, None, p.stem, "list", "ok", detail, show=False, path=str(p), **bound)


def _existing(name: str) -> Path:
    """The stored profile `name`; exit 2 for a path or a name with no stored profile."""
    if _is_path(name):
        die(f"{name!r} looks like a path: delete and rename take a stored profile name", 2)
    p = _stored_path(name)
    if not p.is_file():
        die(f"no profile named {name!r} (see: profile list)", 2)
    return p


def cmd_delete(name: str, out: Result) -> None:
    p = _existing(name)
    try:
        p.unlink()
    except OSError as e:
        die(f"cannot delete profile {name!r}: {e.strerror or e}", 1)
    _rebind(name, None)
    out.say(f"deleted {name} ({p})")
    out.row(None, None, name, "delete", "ok", str(p), show=False, path=str(p))


def cmd_rename(old: str, new: str, out: Result) -> None:
    src = _existing(old)
    if _is_path(new):
        die(f"{new!r} looks like a path: rename <old> <new-name>", 2)
    dst = _stored_path(new)
    if dst.exists():
        die(f"profile {new!r} already exists; delete it first", 2)
    try:
        os.rename(src, dst)      # shortcut: a profile saved as `new` between the check and here is overwritten on POSIX
    except OSError as e:
        die(f"cannot rename profile {old!r}: {e.strerror or e}", 1)
    _rebind(old, new)
    out.say(f"renamed {old} -> {new} ({dst})")
    out.row(None, None, new, "rename", "ok", f"from {old}", show=False, path=str(dst), old=old)


# ---------------------------------------------------------------- bindings

def bindings() -> dict:
    """{"global": name | None, "projects": {resolved dir: name}}; a missing, corrupt or
    hand-edited file reads as no bindings, entry by entry."""
    try:
        doc = json.loads(fs.bindings_file().read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        doc = {}
    doc = doc if isinstance(doc, dict) else {}
    projects = doc.get("projects")
    return {"global": doc["global"] if isinstance(doc.get("global"), str) else None,
            "projects": {d: n for d, n in (projects if isinstance(projects, dict) else {}).items()
                         if isinstance(n, str)}}


def _write_bindings(binds: dict) -> None:
    fs.private_dir(fs.state_dir())
    fs.atomic_write(fs.bindings_file(),
                    json.dumps({"version": VERSION, **binds}, indent=2, ensure_ascii=False) + "\n")


def _rebind(old: str, new: str | None) -> None:
    """Follow a rename (`new`) or drop a deleted profile's bindings (None)."""
    binds = bindings()
    kept = {"global": new if binds["global"] == old else binds["global"],
            "projects": {d: new if n == old else n for d, n in binds["projects"].items()
                         if n != old or new}}
    if kept != binds:
        _write_bindings(kept)


def _dir(arg: str | None) -> Path:
    """The resolved dir a binding is keyed by (default: the cwd); exit 2 unless it exists."""
    p = Path(arg).expanduser() if arg else Path.cwd()
    if not p.is_dir():
        die(f"not a directory: {arg}", 2)
    return p.resolve()


def bound_profile(where: Path) -> tuple[str | None, str]:
    """(profile name, what bound it) for `where`: the nearest bound dir at or above it,
    else the global binding, else (None, "")."""
    binds = bindings()
    by_dir = {os.path.normcase(d): (n, d) for d, n in binds["projects"].items()}  # Windows: any case
    for d in (where, *where.parents):
        if os.path.normcase(str(d)) in by_dir:
            return by_dir[os.path.normcase(str(d))]
    return binds["global"], "global" if binds["global"] else ""


def cmd_bind(name: str, where: str | None, everywhere: bool, out: Result) -> None:
    _existing(name)
    load_scoped(name)                       # exit 2 for a project-scope or broken profile
    binds = bindings()
    key = "global" if everywhere else str(_dir(where))
    if everywhere:
        binds["global"] = name
    else:
        binds["projects"][key] = name
    _write_bindings(binds)
    out.say(f"bound {name} -> {key}")
    out.row(None, None, name, "bind", "ok", key, show=False)


def cmd_unbind(where: str | None, everywhere: bool, out: Result) -> None:
    binds = bindings()
    if everywhere:
        key, name, binds["global"] = "global", binds["global"], None
    else:
        here = str(_dir(where))
        key = next((d for d in binds["projects"]
                    if os.path.normcase(d) == os.path.normcase(here)), here)
        name = binds["projects"].pop(key, None)
    if name is None:
        die(f"no profile is bound to {key} (see: profile list)", 2)
    _write_bindings(binds)
    out.say(f"unbound {name} from {key}")
    out.row(None, None, name, "unbind", "ok", key, show=False)


def cmd_auto(where: str | None, only: str | None, dry_run: bool, out: Result) -> None:
    """Apply the profile bound to `where` (else the global one). Nothing bound changes
    nothing and exits 0: a session-start hook runs this in every project."""
    name, source = bound_profile(_dir(where))
    if name is None:
        out.say("no profile is bound here and no global profile is set")
        return
    if not (_valid_name(name) and _stored_path(name).is_file()):
        why = f"profile {name!r} (bound to {source}) is not saved; see: profile list"
        out.say(why, warn=True)
        out.row(None, None, name, "auto", "skipped", why, show=False, source=source)
        return
    out.say(f"profile {name} (bound to {source})")
    out.row(None, None, name, "auto", "ok", source, show=False, source=source)
    cmd_apply(name, only, dry_run, out)


def cmd_profile(args, out: Result) -> None:
    action, target = args.action, args.target
    if action not in ACTIONS:
        die(f"unknown profile action {action!r} (expected: {', '.join(ACTIONS)})", 2)
    if (action in NO_TARGET) == (target is not None):
        die(f"profile {action} takes no argument" if action in NO_TARGET
            else f"profile {action} needs a <name|file>", 2)
    if (action == "rename") != (args.new_name is not None):
        die("profile rename needs <old> <new>" if action == "rename"
            else f"profile {action} takes one <name|file>", 2)
    if args.out and action != "save":
        die("--out only applies to `profile save`", 2)
    if args.dry_run and action not in ("apply", "diff", "auto"):
        die("--dry-run only applies to `profile apply` / `diff` / `auto`", 2)
    if args.project is not None and action not in ("save", "apply", "diff"):
        die(f"--project does not apply to `profile {action}`", 2)
    binding = action in ("bind", "unbind", "auto")
    if (args.dir is not None or args.everywhere) and not binding:
        die("--dir and --global only apply to `profile bind` / `unbind` / `auto`", 2)
    if args.everywhere and (args.dir is not None or action == "auto"):
        die("--global does not go with --dir" if args.dir is not None
            else "--global does not apply to `profile auto`", 2)
    if binding:
        if action == "bind":
            cmd_bind(target, args.dir, args.everywhere, out)
        elif action == "unbind":
            cmd_unbind(args.dir, args.everywhere, out)
        else:
            cmd_auto(args.dir, args.harness, args.dry_run, out)
        return
    project = project_dir(args.project, args.harness)
    if action == "save":
        cmd_save(target, args.out, args.harness, out, project)
    elif action == "list":
        cmd_list(out)
    elif action == "delete":
        cmd_delete(target, out)
    elif action == "rename":
        cmd_rename(target, args.new_name, out)
    else:                                  # diff is apply, planned only
        cmd_apply(target, args.harness, action == "diff" or args.dry_run, out, project)
