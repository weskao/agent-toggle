"""doctor: a health check (DESIGN s6 "Harness drift").

Compares each installed harness's live layout with its table row, then
cross-checks state.json against disk. The check takes no lock, never writes state,
log, backups or modes, and never shells out (no claude CLI). Every problem row names
the command that fixes it. A row whose fix needs no judgment also yields a `Fix`;
`offer_fixes` asks y/n for each (on a terminal only) and runs it under the lock.
"""
from __future__ import annotations

import contextlib
import filecmp
import json
import os
import shutil
from pathlib import Path
from typing import Callable, NamedTuple

from . import fs, ops, store
from .backends.flag_json import jsonc_loads
from .backends.mcp_json import claude_mcp_config
from .harnesses import Harness, harnesses
from .mechanisms import _refusal, dir_view, legacy_parks, settle
from .output import CliError, Result
from .store import load_state

STYLE = {"ok": "green", "error": "red", "warn": "yellow",
         "absent": "dim", "note": "dim", "unverified": "dim"}
TAG = {"ok": "v", "absent": "-", "note": "i", "unverified": "?", "warn": "!", "error": "x"}
NEED = {"move": ("parked_at", "origin"), "remove_backup": ("backup",)}


class Fix(NamedTuple):
    """A fix doctor can run itself: the row it clears, the y/n question, the action
    (raises on failure)."""
    row: dict
    question: str
    run: Callable[[Result], None]


def _row(out: Result, harness, type_, name, status: str, detail: str, **extra) -> dict:
    row = out.row(harness, type_, name, "doctor", status, detail, show=False, **extra)
    what = " ".join(x for x in (harness, type_, name) if x)
    out.say(f"  {TAG[status]} {what}: {detail}", warn=status == "warn", style=STYLE[status])
    return row


def _locked(fn: Callable[[], object]) -> Callable[[Result], None]:
    """`fn` under the run lock, so a file fix never races a live disable/enable."""
    def run(out: Result) -> None:
        with fs.lock():
            fn()
    return run


def _enable(hname: str, type_: str, name: str, project: str | None,
            mkdir: Path | None = None) -> Callable[[Result], None]:
    """The fix `agent-toggle enable ...` would apply (after recreating `mkdir`)."""
    def run(out: Result) -> None:
        if mkdir:
            mkdir.mkdir(parents=True, exist_ok=True)
        if ops.apply_plan([ops.Op(hname, type_, "enable", name, project)], out,
                          batch=store.BATCH):
            raise CliError("enable failed (see above)")
    return run


def _settle(key: str) -> Callable[[Result], None]:
    """Finish or roll back a killed run's op now, as the next change would."""
    def run(out: Result) -> None:
        with fs.lock():
            state = load_state()
            verdict = settle(state, key, out)[0]
            store.save_state(state)
        if verdict == "stuck":
            raise CliError("still stuck (see above)")
    return run


def _remove(p: Path) -> None:
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p)
    else:
        p.unlink()


def _move_back(p: Path, live: Path) -> None:
    if live.exists() or live.is_symlink():          # appeared since the check: never clobber
        raise FileExistsError(f"{live} exists now")
    live.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(p), str(live))


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
            jsonc_loads(text)
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
    if kind == "jsonc" and jsonc_ok:     # JSONC is the harness's own format, not corruption
        _row(out, hname, type_, name, "note", f"{file} has comments or trailing commas (JSONC); "
             f"agent-toggle edits its flags in place and keeps them, doctor does not check its keys")
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
        # a legacy sibling park dir still counts: cmd_doctor reports it
        cands = [p for s, v in zip(subs, views) for c in (h.home / s, v.live)
                 for p in (c, fs.legacy_park(c))] + [v.parked for v in views]
        if not any(p.exists() or p.is_symlink() for p in cands):
            _row(out, h.name, type_, None, "absent",
                 f"no {' / '.join(subs)} dir under {h.home} (and nothing parked)")
        if others := sorted({x for v in views for x in v.sharers} - {h.name}):
            _row(out, h.name, type_, None, "note", f"dir shared with {', '.join(others)} "
                 f"(disable here also affects them)")
    if h.mcp:
        spec, lead = h.mcp, "layout changed: "
        if spec.backend == "toml":
            try:
                data = fs.toml_parse(spec.file.read_text(encoding="utf-8"))
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


def _check_entry(out: Result, key: str, e: dict, table: dict, fixes: list[Fix]) -> None:
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
        elif not (parent := Path(e["origin"]).parent).is_dir():
            row = _row(out, *ident, "error", f"origin dir gone: {parent}; "
                       f"fix: recreate it, then `{enable}`")
            fixes.append(Fix(row, f"Recreate {parent} and run `{enable}`?",
                             _enable(hname, type_, name, project, mkdir=parent)))
    elif mech == "remove_backup" and not Path(e["backup"]).is_file():
        _row(out, *ident, "error", f"backup missing: {e['backup']}; the saved config is lost -- "
             f"re-add the server by hand, then remove the entry from {sf}")
    elif flag:
        value = _json_at(out, *ident, Path(flag["file"]), flag["pointer"], "")
        if isinstance(value, bool) and value == flag["was"]:
            row = _row(out, *ident, "error", f"{'.'.join(flag['pointer'])} is "
                       f"{str(value).lower()} in {flag['file']} but the item is recorded as "
                       f"disabled (changed outside this tool); fix: `{enable}` clears the entry")
            fixes.append(Fix(row, f"Run `{enable}` to clear the stale entry?",
                             _enable(hname, type_, name, project)))


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


def _orphan_row(out: Result, harness: str, type_: str, p: Path, live: Path, rel: Path,
                fixes: list[Fix]) -> None:
    """One untracked parked item; the fix depends on whether a live copy exists and matches."""
    head = f"{p} is parked but has no state entry (parked outside this tool); fix: "
    ask = None
    if live.exists() or live.is_symlink():
        if _same(p, live):
            kind, fix = "identical", "the live copy is identical; delete the parked copy"
            ask = (f"Delete the parked copy {p} (the live one is identical)?",
                   _locked(lambda: _remove(p)))
        else:
            kind, fix = "differs", (f"a different live copy exists at {live}; compare, keep "
                                    f"the one you want live, delete the parked copy")
    else:
        name = (rel if p.is_dir() else rel.with_suffix("")).as_posix().replace("/", ":")
        kind, fix = "parked-only", (f"to keep it disabled, move it back to {live}, then "
                                    f"`agent-toggle disable {type_} {name}` to record it; "
                                    f"to restore it, just move it back")
        ask = f"Restore it: move {p} back to {live}?", _locked(lambda: _move_back(p, live))
    row = _row(out, harness, type_, p.name, "warn", head + fix, orphan=kind)
    if ask:
        fixes.append(Fix(row, *ask))


def _server_live(table: dict, stem: str) -> bool:
    """True if backup `<harness>__<name>` names a server its harness config holds again
    (a project backup, `<sha8>__...`, or a name with `/` never matches: no fix)."""
    hname, _, name = stem.partition("__")
    h = table.get(hname)
    if not (h and h.mcp and name):
        return False
    if h.mcp.backend == "claude-json":
        try:
            return claude_mcp_config(name) is not None
        except LookupError:                     # local scope in several projects: live
            return True
    try:
        text = h.mcp.file.read_text(encoding="utf-8").removeprefix("\ufeff")
        data = fs.toml_parse(text) if h.mcp.backend == "toml" else json.loads(text)
    except (OSError, ValueError):
        return False
    servers = _walk(data, h.mcp.key_path)[1]
    return isinstance(servers, dict) and name in servers


def _check_orphans(out: Result, state: dict, table: dict, only: str | None,
                   fixes: list[Fix]) -> None:
    entries = state["disabled"].values()
    pending = [p["entry"] for p in state.get("pending", {}).values()
               if isinstance(p, dict) and isinstance(p.get("entry"), dict)]
    tracked = {e["parked_at"] for e in (*entries, *pending)
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
                                p.relative_to(parked), fixes)
    if only:
        return
    digests = set()
    for k in (*state["disabled"], *state.get("pending", {})):
        try:
            digests.add(store.parse_key(k)[1])
        except ValueError:
            pass
    root = fs.parked_dir()
    for d in sorted(root.iterdir()) if root.is_dir() else ():
        if d.name.startswith(".") or d == fs.parked_dir() / "user":   # user scope: see above
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
            row = _row(out, None, None, b.name, "note", f"{b} has no state entry (kept after "
                       f"enable); delete it yourself once the server is confirmed restored")
            if _server_live(table, b.stem):
                fixes.append(Fix(row, f"Delete {b}? (server {b.stem.partition('__')[2]} is "
                                      f"configured again)", _locked(b.unlink)))


def _recorded_companions(e) -> list[tuple[str, str]]:
    """(origin, parked) pairs of one state entry; malformed records are `_check_entry`'s."""
    comps = e.get("companions") if isinstance(e, dict) else None
    return [(c["from"], c["to"]) for c in comps if isinstance(c, dict)
            and isinstance(c.get("from"), str) and isinstance(c.get("to"), str)] \
        if isinstance(comps, list) else []


def _check_companions(out: Result, state: dict, only: str | None) -> None:
    """Companion files (companions.py): every recorded one is parked and not also live,
    and nothing under the companion dir lacks an entry. `pending` (write-ahead) entries
    are the settle rows' business: not checked, but their files are not orphans either."""
    tracked: set[str] = set()
    for key, e in state["disabled"].items():
        pairs = _recorded_companions(e)
        tracked.update(to for _, to in pairs)
        if key in state.get("pending", {}):      # a killed enable leaves the key in both
            continue
        if only and not (isinstance(e, dict) and only in (e.get("harness"), *(e.get("shared_with") or ()))):
            continue
        try:
            ident = store.parse_key(key)
            enable = enable_cmd(key, e)
        except ValueError:                       # malformed key: _check_entry reports it
            continue
        ident = (ident[0], ident[2], ident[3])
        for origin, to in pairs:
            parked, live = Path(to).exists(), Path(origin).exists() or Path(origin).is_symlink()
            if not parked:
                _row(out, *ident, "error", f"companion parked file missing: {to}; fix: put it back "
                     f"there and run `{enable}`, or move your copy to {origin} and remove the "
                     f"entry from {fs.state_file()}")
            elif live:
                _row(out, *ident, "error", f"companion is both live ({origin}) and parked ({to}); "
                     f"fix: compare them, delete the copy you do not want, then run `{enable}`")
    for p in state.get("pending", {}).values():
        e = p.get("entry") if isinstance(p, dict) else None
        tracked.update(to for _, to in _recorded_companions(e))
    root = fs.companion_dir()
    if not root.is_dir():
        return
    for top in sorted(root.iterdir()):
        if (only and not top.name.startswith(f"{only}_")) or top.name.startswith("."):
            continue
        for dirpath, _, files in os.walk(top):
            for f in sorted(files):
                p = Path(dirpath) / f
                if not f.startswith(".") and str(p) not in tracked:
                    _row(out, None, None, f, "warn", f"{p} is a parked companion with no state "
                         f"entry; fix: move it back under its harness home by hand (the path "
                         f"below {top} mirrors the one under that home), or delete it")


def _check_modes(out: Result, fixes: list[Fix]) -> None:
    for p in (fs.state_file(), *fs.backup_dir().glob("*.json")):
        if fs.too_open(p, 0o077):
            row = _row(out, None, None, p.name, "warn", f"{p} is looser than 0600; "
                       f"fix: chmod 600 {p}")
            fixes.append(Fix(row, f"chmod 600 {p}?", lambda out, p=p: _chmod(p, 0o600)))
    if fs.too_open(sd := fs.state_dir()):
        row = _row(out, None, None, None, "warn", f"{sd} is group/world readable; "
                   f"fix: chmod 700 {sd}")
        fixes.append(Fix(row, f"chmod 700 {sd}?", lambda out: os.chmod(sd, 0o700)))


def _chmod(p: Path, mode: int) -> None:
    with contextlib.suppress(FileNotFoundError):    # an earlier yes deleted it
        os.chmod(p, mode)


def cmd_doctor(harness: str | None, out: Result) -> list[Fix]:
    """Report; returns the fixes it can run itself (see `offer_fixes`)."""
    fixes: list[Fix] = []
    table = harnesses()
    installed = [h for n, h in table.items() if (not harness or n == harness) and h.home.is_dir()]
    if harness and not installed:
        _row(out, harness, None, None, "note", f"not installed ({table[harness].home} does not exist)")
    for h in installed:
        _check_layout(out, h, table)
    for old, v, type_ in legacy_parks(table, harness):     # once per dir, shared or not
        _row(out, v.owner, type_, None, "warn", f"{old} is a legacy park dir (items now park "
             f"under {v.parked}); fix: run: agent-toggle migrate")
    try:
        state = load_state(write_back=False, check_entries=False)    # bad entries get rows
    except CliError as e:
        _row(out, None, None, None, "error", f"{e.msg}; fix: repair or move aside {fs.state_file()}")
    else:
        for key, e in state["disabled"].items():
            if harness and not (isinstance(e, dict) and harness in (e.get("harness"),
                                                                    *(e.get("shared_with") or ()))):
                continue
            _check_entry(out, key, e, table, fixes)
        live = bool(state.get("pending")) and fs.lock_held()
        if live:
            _row(out, None, None, None, "note", "another agent-toggle run is in progress (it "
                 "holds the lock); its in-flight op is not checked")
        for key, p in {} if live else state.get("pending", {}).items():   # a killed run's op
            if harness and not (isinstance(p, dict) and isinstance(p.get("entry"), dict)
                                and p["entry"].get("harness") == harness):
                continue
            verdict, msg = settle(state, key, dry_run=True)
            row = _row(out, None, None, key, "error" if verdict == "stuck" else "warn", msg,
                       pending=verdict)
            if verdict != "stuck":
                fixes.append(Fix(row, f"Settle the interrupted op {key} now ({verdict})?",
                                 _settle(key)))
        _check_orphans(out, state, table, harness, fixes)
        _check_companions(out, state, harness)
    if not harness:
        _check_modes(out, fixes)
    errors = sum(r["status"] == "error" for r in out.rows)
    out.say(f"\ndoctor: {errors} problem(s), {len(out.warnings)} warning(s)"
            + (f"; {len(fixes)} fixable here (asked y/n on a terminal)" if fixes else ""))
    return fixes


def offer_quietly(out: Result, harness: str | None = None) -> None:
    """The check without its report; only the problems it can fix, each asked y/n
    (install-shims on a terminal). Unfixable ones are left to `doctor`."""
    if fixes := cmd_doctor(harness, Result("doctor", json_mode=True)):
        out.say(f"\ndoctor found {len(fixes)} problem(s) it can fix:")
        offer_fixes(fixes, out, show_row=True)


def ask_yes(question: str) -> bool:
    """y/n on the terminal; EOF is no."""
    try:
        return input(f"{question} (y/n) ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def offer_fixes(fixes: list[Fix], out: Result, ask: Callable[[str], bool] = ask_yes,
                show_row: bool = False) -> None:
    """Ask y/n per fix and run each yes. A fixed row turns `fixed` (no longer counts
    for the exit code); a failed one adds an `error` row. `show_row` prints the
    problem first, for callers that did not show the report."""
    done = 0
    for f in fixes:
        if show_row:
            out.say(f"  {TAG[f.row['status']]} {f.row['detail']}", style=STYLE[f.row["status"]])
        if not ask(f.question):
            continue
        try:
            f.run(out)
        except Exception as e:  # noqa: BLE001 - one failed fix must not stop the rest
            _row(out, None, None, None, "error", f"fix failed ({f.question}): "
                 f"{getattr(e, 'msg', None) or f'{type(e).__name__}: {e}'}")
            continue
        f.row["status"] = "fixed"
        out.say(f"  {TAG['ok']} fixed", style=STYLE["ok"])
        done += 1
    out.say(f"\ndoctor: fixed {done} of {len(fixes)}" + ("; re-run doctor to confirm" if done else ""))
