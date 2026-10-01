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
from .companions import move, park_companions, restore_companions
from .fs import gitignored, prune_empty
from .harnesses import PROBE_SUFFIXES, SUBDIRS
from .output import Result
from .store import log


def _fail(out: Result, dry_run: bool, harness: str, type_: str, action: str, name: str,
          msg: str, status: str = "error") -> int:
    """Record one failed item (returns 1, the fail count to add)."""
    out.row(harness, type_, name, f"would-{action}" if dry_run else action, status, msg)
    if not dry_run:
        log(action, type_, name, status, msg)
    return 1


def _ok(out: Result, dry_run: bool, harness: str, type_: str, action: str, name: str,
        text: str, **extra) -> dict:
    """Record one success; a dry run records the plan instead, with human text
    `would <action>` (the real text describes what already happened)."""
    if dry_run:
        return out.row(harness, type_, name, f"would-{action}", "planned",
                       f"would {action}", **extra)
    return out.row(harness, type_, name, action, "ok", text, **extra)


def resolve_item(base: Path, name: str) -> Path | None:
    """Find a resource on disk. `a:b` addresses a nested `a/b`."""
    rel = name.replace(":", "/")
    for suffix in PROBE_SUFFIXES:
        p = base / (rel + suffix)
        if p.exists() or p.is_symlink():
            return p
    return None


def toggle_dir_type(action: str, type_: str, names: list[str], state: dict,
                    harness: str, home: Path, out: Result | None = None,
                    dry_run: bool = False) -> int:
    """Park / restore skills, agents, commands. dry_run plans and writes nothing."""
    out = out or Result()
    live = home / SUBDIRS[type_]
    parked = home / f"{SUBDIRS[type_]}-disabled"
    fails = 0

    if action == "disable" and not gitignored(parked, home):
        out.warn(f"{parked} is NOT gitignored -- disabling will dirty `git status`. "
                 f"fix: echo '{parked.name}/' >> {home}/.gitignore")

    for name in names:
        fs.refresh_lock()
        key = f"{harness}:{type_}:{name}"
        if action == "disable":
            src = resolve_item(live, name)
            if src is None:
                fails += _fail(out, dry_run, harness, type_, action, name,
                               f"not found under {live}")
                continue
            # Preserve nesting: commands/orch/batch.md parks as orch/batch.md,
            # so two different <group>/mcp.md cannot collide at the park root.
            rel = src.relative_to(live)
            try:
                target = move(src, (parked / rel).parent, dry_run)
            except (OSError, FileNotFoundError, FileExistsError, NotADirectoryError) as e:
                fails += _fail(out, dry_run, harness, type_, action, name, str(e))
                continue
            row = _ok(out, dry_run, harness, type_, action, name, "disabled",
                      parked_at=str(target))
            companions = park_companions(target, home, key.replace(":", "_"), out,
                                         dry_run, src if dry_run else None)
            row["companions"] = companions
            if not dry_run:
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
                fails += _fail(out, dry_run, harness, type_, action, name,
                               "nothing parked to restore")
                continue
            dest_parent = (Path(entry["origin"]).parent if entry
                           else (live / src.relative_to(parked)).parent)
            try:
                target = move(src, dest_parent, dry_run)
            except (OSError, FileExistsError, NotADirectoryError) as e:
                fails += _fail(out, dry_run, harness, type_, action, name, str(e))
                continue
            if not dry_run:
                prune_empty(src.parent, parked)
            _ok(out, dry_run, harness, type_, action, name, "enabled",
                restored_to=str(target))
            if entry:
                restore_companions(entry, out, dry_run)
                if not dry_run:
                    state["disabled"].pop(key, None)
            if not dry_run:
                log(action, type_, name, "ok", harness)
    return fails


def toggle_plugin(action: str, names: list[str], state: dict, harness: str,
                  out: Result | None = None, dry_run: bool = False) -> int:
    """Claude Code has a native, fully reversible plugin disable -- drive it."""
    out = out or Result()
    if harness != "claude":
        for name in names:
            _fail(out, dry_run, harness, "plugin", action, name,
                  f"{harness} has no plugin CLI -- park it as a skill instead",
                  "unsupported")
        return len(names)
    fails = 0
    for name in names:
        fs.refresh_lock()
        if dry_run:
            if claude_bin():
                _ok(out, dry_run, harness, "plugin", action, name, "")
            else:
                fails += _fail(out, dry_run, harness, "plugin", action, name,
                               "claude CLI not found")
            continue
        ok, res = run_cli(claude_bin(), ["plugin", action, name])
        if not ok:
            fails += _fail(out, dry_run, harness, "plugin", action, name,
                           res.splitlines()[-1] if res else "failed")
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
        _ok(out, dry_run, harness, "plugin", action, name, f"{action}d")
        log(action, "plugin", name, "ok")
    return fails


def toggle_mcp(action: str, names: list[str], state: dict,
               harness: str, home: Path, backend: str | None,
               out: Result | None = None, dry_run: bool = False) -> int:
    """Remove / re-add MCP servers. dry_run plans and writes nothing."""
    out = out or Result()
    if backend is None:
        for name in names:
            _fail(out, dry_run, harness, "mcp", action, name,
                  f"{harness} stores MCP outside a togglable config "
                  f"(sqlite / none) -- not supported", "unsupported")
        return len(names)

    backup_dir = fs.backup_dir()
    if not dry_run:
        fs.private_dir(backup_dir)
    fails = 0

    def fail(name: str, msg: str) -> int:
        return _fail(out, dry_run, harness, "mcp", action, name, msg)

    for name in names:
        fs.refresh_lock()
        key = f"{harness}:mcp:{name}"

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
                log(action, "mcp", name, "ok", json.dumps(detail))
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
                            "project scope lives in the repo's own .mcp.json")
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
                        fails += fail(name, res[:100])
                        continue
            else:  # toml
                config = home / "config.toml"
                block = codex_mcp_remove(config, name, dry_run) if config.exists() else None
                if block is None:
                    fails += fail(name, f"no [mcp_servers.{name}] in {config}")
                    continue
                if not dry_run:
                    fs.atomic_write(bp, json.dumps({"toml": block}, ensure_ascii=False,
                                                   indent=2))

            where = f" [{scope}{f': {project}' if project else ''}]"
            if not dry_run:
                state["disabled"][key] = {
                    "mechanism": "remove_backup", "harness": harness, "type": "mcp",
                    "name": name, "backend": backend, "backup": str(bp),
                    "scope": scope, "project": project,
                    "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                }
                log(action, "mcp", name, "ok", str(bp))
            _ok(out, dry_run, harness, "mcp", action, name,
                f"removed{where} (config saved to {bp})",
                scope=scope, project=project, backup=str(bp))
        else:
            entry = state["disabled"].get(key)
            path = Path(entry["backup"]) if entry else bp
            if not path.is_file():
                fails += fail(name, f"backup missing at {path}")
                continue
            payload = path.read_text()
            if (entry or {}).get("backend", backend) == "toml":
                if not dry_run:
                    codex_mcp_add(home / "config.toml", json.loads(payload)["toml"])
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
                        fails += fail(name, res[:120])
                        continue
            if not dry_run:
                state["disabled"].pop(key, None)
                log(action, "mcp", name, "ok")
            _ok(out, dry_run, harness, "mcp", action, name,
                "restored (open a NEW session for it to load)", backup=str(path))
    return fails
