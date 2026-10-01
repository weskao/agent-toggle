"""State file, action log, and the legacy ~/.claude-toggle import."""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from . import fs
from .output import die


def load_state() -> dict:
    state_file = fs.state_file()
    if not state_file.exists():
        return {"version": 2, "disabled": {}}
    try:
        return json.loads(state_file.read_text())
    except (json.JSONDecodeError, OSError) as e:
        die(f"state file unreadable ({state_file}): {e}")


def save_state(state: dict) -> None:
    fs.state_dir().mkdir(parents=True, exist_ok=True)
    state_file = fs.state_file()
    tmp = state_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    tmp.replace(state_file)  # atomic: never leave a half-written state file


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
            new["backup"] = str(dst)
            new["backend"] = "claude-json"
        elif entry.get("parked_at"):
            new.setdefault("origin", entry["parked_at"]
                           .replace("-disabled/", "/", 1))
        key = f"claude:{okey}"
        if key not in state["disabled"]:
            state["disabled"][key] = new
            added += 1
    print(f"migrated {added} entries from {legacy_state_dir}")
    print("legacy directory left untouched -- delete it yourself once happy")
    return 0
