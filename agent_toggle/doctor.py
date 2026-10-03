"""doctor: a read-only health check (DESIGN s6 "Harness drift").

Compares each installed harness's live layout with its table row, then
cross-checks state.json against disk. It takes no lock, never writes state, log,
backups or modes, and never shells out (no claude CLI). Every problem row names
the command that fixes it; nothing is deleted for you.
"""
from __future__ import annotations

import filecmp
import json
import re
from pathlib import Path

from . import fs, store
from .harnesses import Harness, harnesses
from .mechanisms import _refusal, dir_view
from .output import CliError, Result
from .store import load_state

STYLE = {"ok": "green", "error": "red", "warn": "yellow",
         "absent": "dim", "note": "dim", "unverified": "dim"}
TAG = {"ok": "v", "absent": "-", "note": "i", "unverified": "?", "warn": "!", "error": "x"}
NEED = {"move": ("parked_at", "origin"), "remove_backup": ("backup",)}
# strings (kept) | comments | trailing commas: what makes strict JSON into JSONC
_JSONC = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/|,(?=\s*[}\]])', re.S)


def _row(out: Result, harness, type_, name, status: str, detail: str, **extra) -> None:
    out.row(harness, type_, name, "doctor", status, detail, show=False, **extra)
    what = " ".join(x for x in (harness, type_, name) if x)
    out.say(f"  {TAG[status]} {what}: {detail}", warn=status == "warn", style=STYLE[status])


def _read_json(file: Path) -> tuple[object, str, str]:
    """(data, kind, why); kind is ok | missing | jsonc | broken."""
    try:
        text = file.read_bytes().decode("utf-8").removeprefix("\ufeff")
    except FileNotFoundError:
        return None, "missing", "is missing"
    except (OSError, UnicodeDecodeError) as e:
        return None, "broken", f"cannot be read ({e})"
    try:
        return json.loads(text), "ok", ""
    except ValueError as e:
        try:                                    # parses once comments/commas are gone: JSONC
            json.loads(_JSONC.sub(lambda m: m[0] if m[0][0] == '"' else "", text))
            return None, "jsonc", ""
        except ValueError:
            return None, "broken", f"is not valid JSON ({e})"


def _walk(data: object, path) -> tuple[bool, object]:
    for k in path:
        if not isinstance(data, dict) or k not in data:
            return False, None
        data = data[k]
    return True, data


def _file_row(out: Result, hname, type_, name, file: Path, kind: str, why: str, lead: str,
              jsonc_ok: bool = True) -> None:
    """The row for a JSON file that did not load as strict JSON."""
    if kind == "jsonc" and jsonc_ok:     # JSONC/JSON5 is the harness's own format, not corruption
        _row(out, hname, type_, name, "note", f"unsupported format: {file} has comments or trailing "
             f"commas (JSONC/JSON5); agent-toggle will not edit it, doctor cannot check it")
    else:
        _row(out, hname, type_, name, "error", f"{lead}{file} " + (why or "is not valid JSON"))


def _json_at(out: Result, hname, type_, name, file: Path, path, lead: str, jsonc_ok: bool = True,
             absent_ok: bool = False):
    """The value at `path` in a strict-JSON `file`, or None after emitting the row.
    `absent_ok` (layout checks): a missing file / key is `absent`, not an error --
    a harness that never configured it is fine; a recorded state entry is not."""
    data, kind, why = _read_json(file)
    if kind == "missing" and absent_ok:
        _row(out, hname, type_, name, "absent", f"{file} does not exist (nothing to check)")
        return None
    if kind != "ok":
        _file_row(out, hname, type_, name, file, kind, why, lead, jsonc_ok)
        return None
    found, value = _walk(data, path)
    if found:
        return value
    _row(out, hname, type_, name, "absent" if absent_ok else "error",
         f"{'.'.join(path)} not in {file} (nothing to check)" if absent_ok
         else f"{lead}{'.'.join(path)} not found in {file}")
    return None


def _check_layout(out: Result, h: Harness, table: dict) -> None:
    start = len(out.rows)
    for type_, subs in h.dirs.items():
        views = [dir_view(table, h.name, type_, h.home, s) for s in subs]
        cands = [p for s, v in zip(subs, views) for c in (h.home / s, v.live)
                 for p in (c, c.with_name(c.name + "-disabled"))]
        if not any(p.exists() or p.is_symlink() for p in cands):
            _row(out, h.name, type_, None, "absent",
                 f"no {' / '.join(subs)} dir (or *-disabled park dir) under {h.home}")
        if others := sorted({x for v in views for x in v.sharers} - {h.name}):
            _row(out, h.name, type_, None, "note", f"dir shared with {', '.join(others)} "
                 f"(disable here also affects them)")
    if h.mcp:
        spec, lead = h.mcp, "layout changed: "
        if spec.backend == "toml":
            toml = fs._tomllib()
            if toml is None:
                state = "unverified" if spec.file.is_file() else "absent"
                _row(out, h.name, "mcp", None, state, f"{spec.file}: no tomllib, not parsed"
                     if state == "unverified" else f"{spec.file} does not exist (nothing to check)")
            else:
                try:
                    data = toml.loads(spec.file.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    _row(out, h.name, "mcp", None, "absent",
                         f"{spec.file} does not exist (nothing to check)")
                except (OSError, ValueError) as e:
                    _row(out, h.name, "mcp", None, "error", f"{lead}{spec.file} does not parse ({e})")
                else:
                    if not _walk(data, spec.key_path)[0]:
                        _row(out, h.name, "mcp", None, "absent",
                             f"[{'.'.join(spec.key_path)}] not in {spec.file} (nothing to check)")
        else:
            _json_at(out, h.name, "mcp", None, spec.file, spec.key_path, lead, jsonc_ok=False,
                     absent_ok=True)
    loaded: dict[str, tuple] = {}        # one read + one row per flag file, not per flag type
    for type_, (rel, pointer) in h.flags.items():
        prefix = pointer[:pointer.index("<name>")] if "<name>" in pointer else pointer[:-1]
        file = h.home / rel
        if rel not in loaded:
            loaded[rel] = _read_json(file)
            if loaded[rel][1] == "missing":
                _row(out, h.name, type_, None, "absent", f"{file} does not exist (nothing to check)")
            elif loaded[rel][1] != "ok":
                _file_row(out, h.name, type_, None, file, *loaded[rel][1:], "layout changed: ")
        data, kind, _ = loaded[rel]
        if kind == "ok" and not _walk(data, prefix)[0]:
            _row(out, h.name, type_, None, "absent",
                 f"{'.'.join(prefix)} not in {file} (nothing to check)")
    if not any(r["status"] == "error" for r in out.rows[start:]):
        _row(out, h.name, None, None, "ok", "layout matches the table row")


def enable_cmd(key: str, e: dict) -> str:
    """The `agent-toggle enable ...` line that targets this state entry."""
    hname, digest, type_, name = store.parse_key(key)
    return (f"agent-toggle enable {type_} {name}"
            + (f" --harness {hname}" if hname != "claude" else "")
            + (f" --project {e.get('project')}" if digest else ""))


def missing_parked(key: str, e: dict) -> str | None:
    """'parked item missing ...; fix: ...' when the entry's `parked_at` is gone, else
    None. Read-only; shared by doctor (an error row) and status (a warning)."""
    parked = e.get("parked_at") if isinstance(e, dict) else None
    if not isinstance(parked, str) or Path(parked).exists() or Path(parked).is_symlink():
        return None
    try:
        enable = enable_cmd(key, e)
    except ValueError:                          # malformed key: doctor reports it on its own
        return None
    return (f"parked item missing: {parked}; fix: put it back there and run `{enable}`, "
            f"or remove the entry from {fs.state_file()}")


def _check_entry(out: Result, key: str, e: dict, table: dict) -> None:
    sf = fs.state_file()
    try:
        hname, digest, type_, name = store.parse_key(key)
    except ValueError as ex:
        _row(out, None, None, None, "error", f"{ex}; fix: remove it from {sf} by hand")
        return
    ident = (hname, type_, name)
    mech = e.get("mechanism") if isinstance(e, dict) else None
    mech = mech if isinstance(mech, str) else None
    if not e or not isinstance(e, dict):         # _refusal treats an empty entry as "nothing"
        why = "entry is empty" if isinstance(e, dict) else "entry is not an object"
    else:
        why = _refusal(e, table, key, NEED.get(mech, ()))
    if why:
        _row(out, *ident, "error", f"{why}; entry fails the tamper checks, fix: inspect {sf} "
             f"and remove the entry by hand if it is not yours")
        return
    enable = enable_cmd(key, e)
    project = e.get("project") if digest else None
    if project and not Path(project).is_dir():
        _row(out, *ident, "error", f"project dir gone: {project}; fix: recreate it, then "
             f"`agent-toggle enable --all --project {project}` (items stay parked under "
             f"{fs.parked_dir() / digest})")
        return
    flag = e.get("flag") if mech == "flag" and not e.get("connector") else None
    if mech == "move":
        if gone := missing_parked(key, e):
            _row(out, *ident, "error", gone)
        elif not Path(e["origin"]).parent.is_dir():
            _row(out, *ident, "error", f"origin dir gone: {Path(e['origin']).parent}; "
                 f"fix: recreate it, then `{enable}`")
    elif mech == "remove_backup" and not Path(e["backup"]).is_file():
        _row(out, *ident, "error", f"backup missing: {e['backup']}; the saved config is lost -- "
             f"re-add the server by hand, then remove the entry from {sf}")
    elif flag:
        value = _json_at(out, *ident, Path(flag["file"]), flag["pointer"], "")
        if isinstance(value, bool) and value == flag["was"]:
            _row(out, *ident, "error", f"{'.'.join(flag['pointer'])} is {str(value).lower()} in "
                 f"{flag['file']} but the item is recorded as disabled (changed outside this "
                 f"tool); fix: `{enable}` clears the entry")


def _untracked(root: Path, tracked: set[str]) -> list[Path]:
    """Items under a park dir that no state entry points at (descending into groups)."""
    found = []
    try:
        children = sorted(root.iterdir())
    except OSError:
        return found
    for c in children:
        if c.name.startswith(".") or str(c) in tracked:       # .DS_Store and friends
            continue
        if c.is_dir() and not c.is_symlink() and any(t.startswith(str(c) + "/") or
                                                       t.startswith(str(c) + "\\") for t in tracked):
            found += _untracked(c, tracked)
        else:
            found.append(c)
    return found


def _same(a: Path, b: Path) -> bool:
    """Same bytes in both: a dir is compared file by file, links followed, dotfiles ignored."""
    if a.is_dir() and b.is_dir():
        def files(root: Path) -> set[Path]:
            return {p.relative_to(root) for p in root.rglob("*")
                    if p.is_file() and not p.name.startswith(".")}
        fa = files(a)
        return fa == files(b) and all(filecmp.cmp(a / f, b / f, shallow=False) for f in fa)
    return a.is_file() and b.is_file() and filecmp.cmp(a, b, shallow=False)


def _orphan_row(out: Result, harness: str, type_: str, p: Path, live: Path, rel: Path) -> None:
    """One untracked parked item; the fix depends on whether a live copy exists and matches."""
    head = f"{p} is parked but has no state entry (parked outside this tool); fix: "
    if live.exists() or live.is_symlink():
        if _same(p, live):
            kind, fix = "identical", "the live copy is identical; delete the parked copy"
        else:
            kind, fix = "differs", (f"a different live copy exists at {live}; compare, keep "
                                    f"the one you want live, delete the parked copy")
    else:
        name = (rel if p.is_dir() else rel.with_suffix("")).as_posix().replace("/", ":")
        kind, fix = "parked-only", (f"to keep it disabled, move it back to {live}, then "
                                    f"`agent-toggle disable {type_} {name}` to record it; "
                                    f"to restore it, just move it back")
    _row(out, harness, type_, p.name, "warn", head + fix, orphan=kind)


def _check_orphans(out: Result, state: dict, table: dict, only: str | None) -> None:
    entries = state["disabled"].values()
    tracked = {e["parked_at"] for e in entries
               if isinstance(e, dict) and isinstance(e.get("parked_at"), str)}
    seen: set[Path] = set()
    for h in table.values():
        if (only and h.name != only) or not h.home.is_dir():
            continue
        for type_, subs in h.dirs.items():
            for sub in subs:
                view = dir_view(table, h.name, type_, h.home, sub)
                parked = view.parked
                if parked in seen:
                    continue
                seen.add(parked)
                for p in _untracked(parked, tracked):
                    _orphan_row(out, h.name, type_, p, view.live / p.relative_to(parked),
                                p.relative_to(parked))
    if only:
        return
    digests = set()
    for k in state["disabled"]:
        try:
            digests.add(store.parse_key(k)[1])
        except ValueError:
            pass
    root = fs.parked_dir()
    for d in sorted(root.iterdir()) if root.is_dir() else ():
        if d.name.startswith("."):
            continue
        if d.name not in digests:
            if d.is_dir() and not d.is_symlink() and not any(
                    p.is_symlink() or not p.is_dir() for p in d.rglob("*")):
                continue                # only empty dirs: what a project enable leaves behind
            _row(out, None, None, d.name, "warn", f"{d} holds parked project items with no state "
                 f"entry; fix: move what you want back by hand, then delete the dir")
            continue
        for sub in sorted(d.iterdir()) if d.is_dir() else ():
            for p in _untracked(sub, tracked):
                _row(out, None, None, p.name, "warn", f"{p} is parked but has no state entry; "
                     f"fix: move it back by hand")
    used = {Path(e["backup"]).name for e in entries
            if isinstance(e, dict) and isinstance(e.get("backup"), str)}
    bdir = fs.backup_dir()
    for b in sorted(bdir.glob("*.json")) if bdir.is_dir() else ():
        if b.name not in used:    # enable keeps the backup, so this is a note, not a fault
            _row(out, None, None, b.name, "note", f"{b} has no state entry (kept after enable); "
                 f"delete it yourself once the server is confirmed restored")


def _check_modes(out: Result) -> None:
    for p in (fs.state_file(), *fs.backup_dir().glob("*.json")):
        if fs.too_open(p, 0o077):
            _row(out, None, None, p.name, "warn", f"{p} is looser than 0600; fix: chmod 600 {p}")
    if fs.too_open(fs.state_dir()):
        _row(out, None, None, None, "warn", f"{fs.state_dir()} is group/world readable; "
             f"fix: chmod 700 {fs.state_dir()}")


def cmd_doctor(harness: str | None, out: Result) -> None:
    table = harnesses()
    installed = [h for n, h in table.items() if (not harness or n == harness) and h.home.is_dir()]
    if harness and not installed:
        _row(out, harness, None, None, "note", f"not installed ({table[harness].home} does not exist)")
    for h in installed:
        _check_layout(out, h, table)
    try:
        state = load_state(write_back=False, check_entries=False)    # bad entries get rows
    except CliError as e:
        _row(out, None, None, None, "error", f"{e.msg}; fix: repair or move aside {fs.state_file()}")
    else:
        for key, e in state["disabled"].items():
            if harness and not (isinstance(e, dict) and harness in (e.get("harness"),
                                                                    *(e.get("shared_with") or ()))):
                continue
            _check_entry(out, key, e, table)
        _check_orphans(out, state, table, harness)
    if not harness:
        _check_modes(out)
    errors = sum(r["status"] == "error" for r in out.rows)
    out.say(f"\ndoctor: {errors} problem(s), {len(out.warnings)} warning(s)")
