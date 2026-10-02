"""State file, action log, and the legacy ~/.claude-toggle import."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path

from . import fs
from .output import die

VERSION = 3


def mechanism_of(entry: dict) -> str | None:
    """How an entry was parked, derived from the fields mechanisms.py writes."""
    if entry.get("parked_at"):
        return "move"
    if entry.get("backup"):
        return "remove_backup"
    if entry.get("native"):
        return "native_cli"
    if entry.get("connector"):           # claude.ai connector: fanned over ~/.claude.json projects
        return "connector"
    if entry.get("flag"):                # one {file, pointer, was} config flag
        return "flag"
    return None


# ------------------------------------------------------------ state keys

def project_digest(project: Path | str) -> str:
    return hashlib.sha1(str(Path(project).resolve()).encode("utf-8")).hexdigest()[:8]


def make_key(harness: str, type_: str, name: str, project: Path | str | None = None) -> str:
    """The one state-key scheme: `harness:type:name` for user scope,
    `harness@<sha1(resolved project dir)[:8]>:type:name` for project scope."""
    if project is not None:
        harness = f"{harness}@{project_digest(project)}"
    return f"{harness}:{type_}:{name}"


def parse_key(key: str) -> tuple[str, str | None, str, str]:
    """(harness, project_hash or None, type, name); a name may hold `:`."""
    parts = key.split(":", 2)
    if len(parts) != 3 or not all(parts):
        raise ValueError(f"malformed state key {key!r}")
    head, type_, name = parts
    harness, at, digest = head.partition("@")
    if not harness or (at and not re.fullmatch(r"[0-9a-f]{8}", digest)):
        raise ValueError(f"malformed state key {key!r}")
    return harness, digest or None, type_, name


# ------------------------------------------------- entry integrity (s6.1 row 2)

PATH_FIELDS = ("origin", "parked_at", "backup", "project")


def check_entry(entry: dict, table: dict, key: str | None = None) -> str | None:
    """Why a (possibly tampered) state entry must not be replayed, or None.

    `project` comes from the same untrusted entry, so it is refused when it is
    a filesystem root, $HOME or an ancestor of it, and (given the state key)
    must hash to the key's project digest. Project dirs are `.<home name>`
    (`.claude`, `.opencode`); a project entry's origin must sit in one of them and
    its parked_at under fs.parked_dir()/<sha8> (symlinked parents refused)."""
    if not isinstance(entry, dict):
        return "entry is not an object"
    hname = entry.get("harness")
    h = table.get(hname) if isinstance(hname, str) else None
    if h is None:
        return f"unknown harness {entry.get('harness')!r}"
    flag = entry.get("flag")
    fields = {f: entry.get(f) for f in PATH_FIELDS}
    if flag is not None:
        fields["flag"] = flag.get("file") if isinstance(flag, dict) else flag
    for field, value in fields.items():
        if value is None:
            continue
        if not isinstance(value, str) or not value:
            return f"{field} is not a path"
        if ".." in Path(value).parts:
            return f"{field} holds '..': {value}"
        if not Path(value).is_absolute():    # would resolve against the cwd
            return f"{field} is not absolute: {value}"

    project = Path(fields["project"]) if fields["project"] else None
    if project is not None:
        rp, home = project.resolve(), fs.home().resolve()
        if rp == Path(rp.anchor) or home.is_relative_to(rp):
            return f"project is a root or holds $HOME: {project}"
    if key is not None:
        try:
            digest = parse_key(key)[1]
        except ValueError as e:
            return str(e)
        if digest != (project_digest(project) if project else None):
            return f"project does not match the state key {key!r}"
    origin, parked = fields["origin"], fields["parked_at"]
    if project is not None:
        # Project scope: items come from <project>/.<home name>/<declared dir> and park
        # ONLY under parked_dir()/<sha8>, never inside the project (a repo is shared).
        base = project / ("." + h.home.name.lstrip("."))
        declared = [base / sub for subs in h.dirs.values() for sub in subs
                    if not Path(sub).is_absolute()]
        origin_ok = bool(origin) and fs.contained(Path(origin), *declared) \
            and fs.contained(Path(origin), project)
        parks = [fs.parked_dir() / project_digest(project)]
    else:
        declared = [h.home / sub for subs in h.dirs.values() for sub in subs]
        origin_ok = bool(origin) and fs.contained(Path(origin), h.home, *declared)
        # user scope parks only in the `*-disabled` siblings; parked_dir() is project-only
        parks = [d.with_name(d.name + "-disabled") for d in declared]
    if origin and not origin_ok:
        return f"origin outside the {h.name} {'project' if project else 'home'}: {origin}"
    if parked and not fs.contained(Path(parked), *parks):
        return f"parked_at outside {' / '.join(map(str, parks))}: {parked}"
    ffile = fields.get("flag")
    if ffile and not ((fs.contained(Path(ffile), base) and fs.contained(Path(ffile), project))
                      if project is not None else
                      (fs.contained(Path(ffile), h.home, *declared)
                       or Path(ffile) in {fs.claude_json(), *([h.mcp.file] if h.mcp else [])})):
        return f"flag file outside the {h.name} home/project: {ffile}"
    backup = fields["backup"]
    if backup and not fs.contained(Path(backup), fs.backup_dir()):
        return f"backup outside {fs.backup_dir()}: {backup}"
    return None


def scope_state(state: dict, project: Path | str | None = None) -> dict:
    """`state` holding only one scope: user entries (project=None) or one project's."""
    want = project_digest(project) if project is not None else None
    keep = {}
    for k, e in state["disabled"].items():
        try:
            if parse_key(k)[1] == want:
                keep[k] = e
        except ValueError:
            pass
    return {**state, "disabled": keep}


def upgrade(state: dict) -> bool:
    """Forward-only in-place upgrade to schema v3. True if anything changed."""
    if state.get("version", 0) > VERSION:
        die(f"state is schema v{state['version']}, newer than this agent-toggle "
            f"(v{VERSION}) -- upgrade agent-toggle")
    changed = state.get("version") != VERSION
    state["version"] = VERSION
    for entry in state.setdefault("disabled", {}).values():
        if "mechanism" not in entry:
            mech = mechanism_of(entry) or "flag"
            entry["mechanism"] = "flag" if mech == "connector" else mech   # stored enum
            changed = True
    return changed


def load_state(write_back: bool = True) -> dict:
    """Read state, upgrading it to v3. Read-only commands pass write_back=False
    so they never write outside the lock; the upgrade then stays in memory."""
    state_file = fs.state_file()
    if not state_file.exists():
        return {"version": VERSION, "disabled": {}}
    if write_back:                       # read-only runs never chmod; status only warns
        fs.tighten(state_file)
        for bp in fs.backup_dir().glob("*.json"):
            fs.tighten(bp)
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as e:
        die(f"state file unreadable ({state_file}): {e}")
    if not isinstance(state, dict):
        die(f"state file malformed ({state_file}): top level must be an object")
    version = state.get("version", 0)
    if not isinstance(version, int) or isinstance(version, bool):
        die(f"state file malformed ({state_file}): version must be an integer")
    if not isinstance(state.get("disabled", {}), dict):
        die(f"state file malformed ({state_file}): disabled must be an object")
    if upgrade(state) and write_back:
        save_state(state)
    return state


def save_state(state: dict) -> None:
    fs.private_dir(fs.state_dir())
    fs.atomic_write(fs.state_file(), json.dumps(state, indent=2, ensure_ascii=False))


# The log row schema, defined once; log() writes every field, in this order.
LOG_FIELDS = ("ts", "harness", "type", "name", "action", "result", "batch",
              "project", "scope", "detail")
# One id per process: every row a single CLI run writes shares it.
BATCH = f"{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}"


def log(action: str, type_: str, name: str, result: str, detail: str = "", *,
        harness: str | None = None, batch: str = BATCH, project: str | None = None,
        scope: str = "user") -> None:
    """Append one row; every row carries the full schema (LOG_FIELDS order)."""
    fs.private_dir(fs.state_dir())
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "harness": harness,
           "type": type_, "name": name, "action": action, "result": result,
           "batch": batch, "project": project, "scope": scope, "detail": detail}
    fd = os.open(fs.log_file(), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def migrate(state: dict) -> int:
    """Import ~/.claude-toggle state so existing rollbacks keep working."""
    legacy_state_dir, backup_dir = fs.legacy_state_dir(), fs.backup_dir()
    old_file = legacy_state_dir / "state.json"
    if not old_file.is_file():
        print(f"nothing to migrate ({old_file} not found)")
        return 0
    try:
        old = json.loads(old_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        die(f"legacy state unreadable: {e}")

    fs.private_dir(backup_dir)
    added = 0
    for okey, entry in old.get("disabled", {}).items():
        new = dict(entry, harness="claude")
        if entry["type"] == "mcp" and entry.get("backup"):
            src = Path(entry["backup"])
            dst = backup_dir / f"claude__{entry['name'].replace('/', '_')}.json"
            if src.is_file() and not dst.exists():
                shutil.copy2(src, dst)     # copy, not move: legacy stays usable
                fs.tighten(dst)
            new["backup"] = str(dst)
            new["backend"] = "claude-json"
        elif entry.get("parked_at"):
            new.setdefault("origin", entry["parked_at"]
                           .replace("-disabled/", "/", 1))
        new["mechanism"] = mechanism_of(new) or "flag"
        key = f"claude:{okey}"
        if key not in state["disabled"]:
            state["disabled"][key] = new
            added += 1
    print(f"migrated {added} entries from {legacy_state_dir}")
    print("legacy directory left untouched -- delete it yourself once happy")
    return 0
