"""State file, action log, and the legacy ~/.claude-toggle import."""
from __future__ import annotations

import json
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
    if entry.get("connector"):           # claude.ai connector: per-project config flag
        return "flag"
    return None


def upgrade(state: dict) -> bool:
    """Forward-only in-place upgrade to schema v3. True if anything changed."""
    if state.get("version", 0) > VERSION:
        die(f"state is schema v{state['version']}, newer than this agent-toggle "
            f"(v{VERSION}) -- upgrade agent-toggle")
    changed = state.get("version") != VERSION
    state["version"] = VERSION
    for entry in state.setdefault("disabled", {}).values():
        if "mechanism" not in entry:
            entry["mechanism"] = mechanism_of(entry) or "flag"
            changed = True
    return changed


def load_state(write_back: bool = True) -> dict:
    """Read state, upgrading it to v3. Read-only commands pass write_back=False
    so they never write outside the lock; the upgrade then stays in memory."""
    state_file = fs.state_file()
    if not state_file.exists():
        return {"version": VERSION, "disabled": {}}
    fs.tighten(state_file)
    for bp in fs.backup_dir().glob("*.json"):
        fs.tighten(bp)
    try:
        state = json.loads(state_file.read_text())
    except (json.JSONDecodeError, OSError) as e:
        die(f"state file unreadable ({state_file}): {e}")
    if upgrade(state) and write_back:
        save_state(state)
    return state


def save_state(state: dict) -> None:
    fs.state_dir().mkdir(parents=True, exist_ok=True)
    fs.atomic_write(fs.state_file(), json.dumps(state, indent=2, ensure_ascii=False))


def log(action: str, type_: str, name: str, result: str, detail: str = "") -> None:
    fs.state_dir().mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "action": action,
           "type": type_, "name": name, "result": result}
    if detail:
        rec["detail"] = detail
    with fs.log_file().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def migrate(state: dict) -> int:
    """Import ~/.claude-toggle state so existing rollbacks keep working."""
    legacy_state_dir, backup_dir = fs.legacy_state_dir(), fs.backup_dir()
    old_file = legacy_state_dir / "state.json"
    if not old_file.is_file():
        print(f"nothing to migrate ({old_file} not found)")
        return 0
    try:
        old = json.loads(old_file.read_text())
    except (OSError, json.JSONDecodeError) as e:
        die(f"legacy state unreadable: {e}")

    backup_dir.mkdir(parents=True, exist_ok=True)
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
