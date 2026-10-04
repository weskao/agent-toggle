"""Profiles (DESIGN s5.7): a portable snapshot of which items are live.

A profile holds ONLY {harness, type, name, live} per item -- never a path or a
secret -- so it can sit in a dotfiles repo. `apply` toggles just the items the
profile mentions: an item installed after the save is never touched, and an item
the profile names that this machine lacks is skipped, not failed.
"""
from __future__ import annotations

import json
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
ACTIONS = ("save", "apply", "diff", "list")


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
    for p in files:
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            detail = f"{len(doc['items'])} items, saved {str(doc.get('saved_at', '?'))[:10]}"
        except (OSError, ValueError, KeyError, TypeError):
            detail = "unreadable"
        out.say(f"  {p.stem:<24} {detail}")
        out.row(None, None, p.stem, "list", "ok", detail, show=False, path=str(p))


def cmd_profile(args, out: Result) -> None:
    action, target = args.action, args.target
    if action not in ACTIONS:
        die(f"unknown profile action {action!r} (expected: {', '.join(ACTIONS)})", 2)
    if (action == "list") == (target is not None):
        die("profile list takes no argument" if action == "list"
            else f"profile {action} needs a <name|file>", 2)
    if args.out and action != "save":
        die("--out only applies to `profile save`", 2)
    if args.dry_run and action not in ("apply", "diff"):
        die("--dry-run only applies to `profile apply` / `diff`", 2)
    if args.project is not None and action == "list":
        die("--project does not apply to `profile list`", 2)
    project = project_dir(args.project, args.harness)
    if action == "save":
        cmd_save(target, args.out, args.harness, out, project)
    elif action == "list":
        cmd_list(out)
    else:                                  # diff is apply, planned only
        cmd_apply(target, args.harness, action == "diff" or args.dry_run, out, project)
