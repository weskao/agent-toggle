"""The toggle strategies: park/restore a file or dir, drive the plugin CLI,
remove/re-add an MCP server."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import NamedTuple

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
from .harnesses import PROBE_SUFFIXES, harnesses
from .output import Result, die
from .store import BATCH, check_entry, log


def fail_row(out: Result, dry_run: bool, harness: str, type_: str, action: str, name: str,
          msg: str, status: str = "error", *, batch: str = BATCH) -> int:
    """Record one failed item (returns 1, the fail count to add)."""
    out.row(harness, type_, name, f"would-{action}" if dry_run else action, status, msg)
    if not dry_run:
        log(action, type_, name, status, msg, harness=harness, batch=batch)
    return 1


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
    if entry.get("backend") == "claude-json" and entry.get("scope") in ("user", "local"):
        if entry["scope"] == "local":
            # `project` here is the claude local-scope cwd, not a --project scope
            proj = entry.get("project")
            if not _abs_path(proj) or Path(proj) == Path(Path(proj).anchor):
                return f"local-scope project is not a usable dir: {proj!r}"
            entry = {k: v for k, v in entry.items() if k != "project"}
    elif entry.get("backend") == "claude-json" and entry.get("scope") is not None:
        return f"unknown mcp scope {entry.get('scope')!r}"
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
    for p in sorted(base.rglob("*")):
        if p.name.startswith("."):
            continue
        if p.suffix in (".md", ".toml", ".yaml", ".yml") or p.is_symlink() or (p.is_file() and not p.suffix):
            rel = p.relative_to(base).with_suffix("")
            names.append(str(rel).replace("/", ":"))
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


def toggle_dir_type(action: str, type_: str, names: list[str], state: dict,
                    harness: str, home: Path, out: Result | None = None,
                    dry_run: bool = False, *, batch: str = BATCH) -> int:
    """Park / restore skills, agents, commands. dry_run plans and writes nothing."""
    out = out or Result()
    table = harnesses()
    views = [dir_view(table, harness, type_, home, sub) for sub in table[harness].dirs[type_]]
    fails = 0

    if action == "disable":
        for v in views:
            ohome = home if v.owner == harness else table[v.owner].home
            if not gitignored(v.parked, ohome):
                out.warn(f"{v.parked} is NOT gitignored -- disabling will dirty `git status`. "
                         f"fix: echo '{v.parked.name}/' >> {ohome}/.gitignore")
            if markers := fs.sync_markers(v.live):
                out.warn(f"{v.live} carries {', '.join(markers)} -- a sync job may re-create "
                         f"parked items; park in the source harness instead")

    for name in names:
        fs.refresh_lock()
        if action == "disable":
            v, src = next(((v, p) for v in views if (p := resolve_item(v.live, name))),
                          (views[0], None))
            if src is None:
                fails += fail_row(out, dry_run, harness, type_, action, name,
                               "not found under " + " or ".join(str(v.live) for v in views),
                               batch=batch)
                continue
            key = f"{v.owner}:{type_}:{name}"
            others = [h for h in v.sharers if h != harness]        # relative to this request
            ohome = home if v.owner == harness else table[v.owner].home
            # Preserve nesting: commands/orch/batch.md parks as orch/batch.md,
            # so two different <group>/mcp.md cannot collide at the park root.
            rel = src.relative_to(v.live)
            try:
                target = move(src, (v.parked / rel).parent, dry_run)
            except (OSError, FileNotFoundError, FileExistsError, NotADirectoryError) as e:
                fails += fail_row(out, dry_run, harness, type_, action, name, str(e), batch=batch)
                continue
            row = _ok(out, dry_run, harness, type_, action, name, _shared_text(
                      "disabled", v.owner, harness, others), parked_at=str(target),
                      **({"shared_with": others, "owner": v.owner} if others else {}))
            companions = park_companions(target, ohome, key.replace(":", "_"), out,
                                         dry_run, src if dry_run else None)
            row["companions"] = companions
            if not dry_run:
                state["disabled"][key] = {
                    "mechanism": "move", "harness": v.owner, "type": type_, "name": name,
                    "parked_at": str(target), "origin": str(src),
                    "companions": companions,
                    **({"shared_with": [h for h in v.sharers if h != v.owner]}
                       if len(v.sharers) > 1 else {}),
                    "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                }
                log(action, type_, name, "ok", str(target), harness=harness, batch=batch)
        else:
            # The entry lives under the owning harness; a request through an alias
            # (or a pre-alias entry under the requested harness) finds the same one.
            keys = dict.fromkeys([f"{v.owner}:{type_}:{name}" for v in views]
                                 + [f"{harness}:{type_}:{name}"])
            key = next((k for k in keys if k in state["disabled"]), next(iter(keys)))
            entry = state["disabled"].get(key)
            if refused := _refusal(entry, table, key, ("parked_at", "origin")):
                fails += fail_row(out, dry_run, harness, type_, action, name, refused, batch=batch)
                continue
            if entry:
                src = Path(entry["parked_at"])
                v = next((v for v in views if src.is_relative_to(v.parked)), views[0])
            else:
                v, src = next(((v, p) for v in views if (p := resolve_item(v.parked, name))),
                              (views[0], None))
            if src is None or not (src.exists() or src.is_symlink()):
                fails += fail_row(out, dry_run, harness, type_, action, name,
                               "nothing parked to restore", batch=batch)
                continue
            others = [h for h in v.sharers if h != harness]
            dest_parent = (Path(entry["origin"]).parent if entry
                           else (v.live / src.relative_to(v.parked)).parent)
            try:
                target = move(src, dest_parent, dry_run)
            except (OSError, FileExistsError, NotADirectoryError) as e:
                fails += fail_row(out, dry_run, harness, type_, action, name, str(e), batch=batch)
                continue
            if not dry_run:
                prune_empty(src.parent, v.parked)
            _ok(out, dry_run, harness, type_, action, name,
                _shared_text("enabled", v.owner, harness, others), restored_to=str(target),
                **({"shared_with": others, "owner": v.owner} if others else {}))
            if entry:
                restore_companions(entry, out, dry_run)
                if not dry_run:
                    state["disabled"].pop(key, None)
            if not dry_run:
                log(action, type_, name, "ok", str(target), harness=harness, batch=batch)
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
    if harness != "claude":
        for name in names:
            fail_row(out, dry_run, harness, "plugin", action, name,
                  f"{harness} has no plugin CLI -- park it as a skill instead",
                  "unsupported", batch=batch)
        return len(names)
    fails = 0
    for name in names:
        fs.refresh_lock()
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
        _ok(out, dry_run, harness, "plugin", action, name, f"{action}d")
        log(action, "plugin", name, "ok", harness=harness, batch=batch)
    return fails


def toggle_mcp(action: str, names: list[str], state: dict,
               harness: str, home: Path, backend: str | None,
               out: Result | None = None, dry_run: bool = False, *,
               batch: str = BATCH) -> int:
    """Remove / re-add MCP servers. dry_run plans and writes nothing."""
    out = out or Result()
    if backend is None:
        for name in names:
            fail_row(out, dry_run, harness, "mcp", action, name,
                  f"{harness} stores MCP outside a togglable config "
                  f"(sqlite / none) -- not supported", "unsupported", batch=batch)
        return len(names)

    backup_dir = fs.backup_dir()
    if not dry_run:
        fs.private_dir(backup_dir)
    fails = 0

    def fail(name: str, msg: str) -> int:
        return fail_row(out, dry_run, harness, "mcp", action, name, msg, batch=batch)

    table = harnesses()
    for name in names:
        fs.refresh_lock()
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
                log(action, "mcp", name, "ok", str(bp), harness=harness, batch=batch)
            _ok(out, dry_run, harness, "mcp", action, name,
                f"removed{where} (config saved to {bp})",
                scope=scope, project=project, backup=str(bp))
        else:
            entry = state["disabled"].get(key)
            path = Path(entry["backup"]) if entry else bp
            if not path.is_file():
                fails += fail(name, f"backup missing at {path}")
                continue
            payload = path.read_text(encoding="utf-8")
            if (entry or {}).get("backend", backend) == "toml":
                if not dry_run:
                    try:
                        codex_mcp_add(home / "config.toml", json.loads(payload)["toml"])
                    except fs.WriteError as e:
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
                        fails += fail(name, res[:120])
                        continue
            if not dry_run:
                state["disabled"].pop(key, None)
                log(action, "mcp", name, "ok", harness=harness, batch=batch)
            _ok(out, dry_run, harness, "mcp", action, name,
                "restored (open a NEW session for it to load)", backup=str(path))
    return fails
