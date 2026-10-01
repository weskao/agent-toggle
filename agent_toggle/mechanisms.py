"""The toggle strategies: park/restore a file or dir, drive the plugin CLI,
remove/re-add an MCP server."""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import fs
from .backends.mcp_json import (
    claude_mcp_config,
    claudeai_connector_names,
    toggle_claudeai_connector,
)
from .backends.mcp_toml import codex_mcp_add, codex_mcp_remove
from .backends.plugin_cli import claude_bin, run_cli
from .companions import park_companions, restore_companions
from .fs import gitignored, prune_empty, safe_move
from .harnesses import PROBE_SUFFIXES, SUBDIRS
from .store import log


def resolve_item(base: Path, name: str) -> Path | None:
    """Find a resource on disk. `a:b` addresses a nested `a/b`."""
    rel = name.replace(":", "/")
    for suffix in PROBE_SUFFIXES:
        p = base / (rel + suffix)
        if p.exists() or p.is_symlink():
            return p
    return None


def toggle_dir_type(action: str, type_: str, names: list[str], state: dict,
                    harness: str, home: Path) -> int:
    live = home / SUBDIRS[type_]
    parked = home / f"{SUBDIRS[type_]}-disabled"
    fails = 0

    if action == "disable" and not gitignored(parked, home):
        print(f"WARNING  {parked} is NOT gitignored -- disabling will dirty `git status`.")
        print(f"         fix: echo '{parked.name}/' >> {home}/.gitignore")

    for name in names:
        key = f"{harness}:{type_}:{name}"
        if action == "disable":
            src = resolve_item(live, name)
            if src is None:
                print(f"  x {type_} {name}: not found under {live}")
                log(action, type_, name, "error", "not found")
                fails += 1
                continue
            # Preserve nesting: commands/orch/batch.md parks as orch/batch.md,
            # so two different <group>/mcp.md cannot collide at the park root.
            rel = src.relative_to(live)
            try:
                target = safe_move(src, (parked / rel).parent)
            except (OSError, FileNotFoundError, FileExistsError, NotADirectoryError) as e:
                print(f"  x {type_} {name}: {e}")
                log(action, type_, name, "error", str(e))
                fails += 1
                continue
            print(f"  v {type_} {name} disabled")
            companions = park_companions(target, home, key.replace(":", "_"))
            state["disabled"][key] = {
                "mechanism": "move", "harness": harness, "type": type_, "name": name,
                "parked_at": str(target), "origin": str(src),
                "companions": companions,
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
            log(action, type_, name, "ok", harness)
        else:
            entry = state["disabled"].get(key)
            src = Path(entry["parked_at"]) if entry else resolve_item(parked, name)
            if src is None or not (src.exists() or src.is_symlink()):
                print(f"  x {type_} {name}: nothing parked to restore")
                fails += 1
                continue
            dest_parent = (Path(entry["origin"]).parent if entry
                           else (live / src.relative_to(parked)).parent)
            try:
                safe_move(src, dest_parent)
            except (OSError, FileExistsError, NotADirectoryError) as e:
                print(f"  x {type_} {name}: {e}")
                log(action, type_, name, "error", str(e))
                fails += 1
                continue
            prune_empty(src.parent, parked)
            print(f"  v {type_} {name} enabled")
            if entry:
                restore_companions(entry)
                state["disabled"].pop(key, None)
            log(action, type_, name, "ok", harness)
    return fails


def toggle_plugin(action: str, names: list[str], state: dict, harness: str) -> int:
    """Claude Code has a native, fully reversible plugin disable -- drive it."""
    if harness != "claude":
        print(f"  x plugin: {harness} has no plugin CLI -- park it as a skill instead")
        return len(names)
    fails = 0
    for name in names:
        ok, out = run_cli(claude_bin(), ["plugin", action, name])
        if not ok:
            print(f"  x plugin {name}: {out.splitlines()[-1] if out else 'failed'}")
            log(action, "plugin", name, "error", out[:200])
            fails += 1
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
        print(f"  v plugin {name} {action}d")
        log(action, "plugin", name, "ok")
    return fails


def toggle_mcp(action: str, names: list[str], state: dict,
               harness: str, home: Path, backend: str | None) -> int:
    if backend is None:
        print(f"  x mcp: {harness} stores MCP outside a togglable config "
              f"(sqlite / none) -- not supported")
        return len(names)

    backup_dir = fs.backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)
    fails = 0
    for name in names:
        key = f"{harness}:mcp:{name}"

        if backend == "claude-json" and name in claudeai_connector_names():
            ok, detail = toggle_claudeai_connector(action, name)
            if not ok:
                print(f"  x mcp {name}: {detail}")
                log(action, "mcp", name, "error", str(detail))
                fails += 1
                continue
            n = len(detail)
            if action == "disable":
                state["disabled"][key] = {
                    "mechanism": "flag", "harness": harness, "type": "mcp",
                    "name": name, "connector": True,
                    "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                }
                print(f"  v mcp {name} disabled across {n} project(s) "
                      f"(claude.ai connector -- account stays connected, "
                      f"just parked per-project; new project dirs need a re-run)")
            else:
                state["disabled"].pop(key, None)
                print(f"  v mcp {name} restored across {n} project(s) "
                      f"(open a NEW session for it to load)")
            log(action, "mcp", name, "ok", json.dumps(detail))
            continue

        bp = backup_dir / f"{harness}__{name.replace('/', '_')}.json"
        scope, project = "user", None

        if action == "disable":
            if backend == "claude-json":
                try:
                    found = claude_mcp_config(name)
                except LookupError as e:
                    print(f"  x mcp {name}: {e}")
                    log(action, "mcp", name, "error", str(e))
                    fails += 1
                    continue
                if found is None:
                    ok, cfg = run_cli(claude_bin(), ["mcp", "get", name])
                    hint = ("account-level claude.ai connector -- disable it at "
                            "claude.ai (Settings -> Connectors)"
                            if ok and "claude.ai config" in cfg
                            else "not in ~/.claude.json (user or local scope) -- "
                                 "project scope lives in the repo's own .mcp.json")
                    print(f"  x mcp {name}: {hint}")
                    log(action, "mcp", name, "error", hint)
                    fails += 1
                    continue
                raw, scope, project = found
                fs.atomic_write(bp, json.dumps(raw, indent=2, ensure_ascii=False))
                ok, out = run_cli(claude_bin(), ["mcp", "remove", name, "-s", scope],
                                  cwd=project)
                if not ok:
                    print(f"  x mcp {name}: {out[:100]}")
                    log(action, "mcp", name, "error", out[:200])
                    fails += 1
                    continue
            else:  # toml
                config = home / "config.toml"
                block = codex_mcp_remove(config, name) if config.exists() else None
                if block is None:
                    print(f"  x mcp {name}: no [mcp_servers.{name}] in {config}")
                    fails += 1
                    continue
                fs.atomic_write(bp, json.dumps({"toml": block}, ensure_ascii=False, indent=2))

            state["disabled"][key] = {
                "mechanism": "remove_backup", "harness": harness, "type": "mcp",
                "name": name, "backend": backend, "backup": str(bp),
                "scope": scope, "project": project,
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
            where = f" [{scope}{f': {project}' if project else ''}]"
            print(f"  v mcp {name} removed{where} (config saved to {bp})")
            log(action, "mcp", name, "ok", str(bp))
        else:
            entry = state["disabled"].get(key)
            path = Path(entry["backup"]) if entry else bp
            if not path.is_file():
                print(f"  x mcp {name}: backup missing at {path}")
                fails += 1
                continue
            payload = path.read_text()
            if (entry or {}).get("backend", backend) == "toml":
                codex_mcp_add(home / "config.toml", json.loads(payload)["toml"])
            else:
                # Entries parked before scope tracking have neither field;
                # they were user-scope by construction, so that is the default.
                scope = (entry or {}).get("scope") or "user"
                project = (entry or {}).get("project")
                if project and not Path(project).is_dir():
                    print(f"  x mcp {name}: project {project} is gone -- "
                          f"re-add it by hand from {path}")
                    fails += 1
                    continue
                ok, out = run_cli(claude_bin(),
                                  ["mcp", "add-json", "--scope", scope, name, payload],
                                  cwd=project)
                if not ok:
                    print(f"  x mcp {name}: {out[:120]}")
                    log(action, "mcp", name, "error", out[:200])
                    fails += 1
                    continue
            state["disabled"].pop(key, None)
            print(f"  v mcp {name} restored (open a NEW session for it to load)")
            log(action, "mcp", name, "ok")
    return fails
