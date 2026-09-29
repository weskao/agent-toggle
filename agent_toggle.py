#!/usr/bin/env python3
"""Temporarily disable / re-enable AI-agent resources across harnesses.

Skills, agents, commands, plugins and MCP servers can all be parked and put
back. Nothing is ever deleted.

Supported harnesses: claude (Claude Code), codex, grok, openclaw.
Not every harness has every resource type; unsupported pairs fail loudly
instead of silently doing nothing.

State lives in ONE place ($HOME/.agent-toggle/), never as sidecar files next
to the targets -- a user's `git status` must not change because of our
bookkeeping.

Usage:
    agent_toggle.py ui                         # interactive picker (curses)
    agent_toggle.py disable <type> <name>...   [--harness H]
    agent_toggle.py enable  <type> <name>...   [--harness H]
    agent_toggle.py list [<type>]              # what is currently disabled
    agent_toggle.py status                     # health check
    agent_toggle.py migrate                    # import old ~/.claude-toggle state

    <type> = skill | agent | command | plugin | mcp
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
STATE_DIR = HOME / ".agent-toggle"
STATE_FILE = STATE_DIR / "state.json"
LOG_FILE = STATE_DIR / "log.jsonl"
BACKUP_DIR = STATE_DIR / "mcp-backups"
COMPANION_DIR = STATE_DIR / "companions"
LEGACY_STATE_DIR = HOME / ".claude-toggle"

TYPES = ("skill", "agent", "command", "plugin", "mcp")

# type -> subdirectory name. The on-disk SHAPE (directory vs .md file) is
# probed, not declared: harnesses disagree and the probe is both shorter and
# more correct than a per-harness shape table.
SUBDIRS = {"skill": "skills", "agent": "agents", "command": "commands"}
PROBE_SUFFIXES = ("", ".md", ".toml", ".yaml", ".yml")

# harness -> (home dir, supported types, mcp backend)
HARNESSES: dict[str, tuple[Path, tuple[str, ...], str | None]] = {
    "claude":   (HOME / ".claude",   ("skill", "agent", "command", "plugin", "mcp"), "claude-json"),
    "codex":    (HOME / ".codex",    ("skill", "agent", "command", "plugin", "mcp"), "toml"),
    "grok":     (HOME / ".grok",     ("skill",), None),
    "openclaw": (HOME / ".openclaw", ("skill", "agent"), None),
}

TEXT_SUFFIXES = {".md", ".sh", ".py", ".js", ".mjs", ".cjs", ".ts", ".json",
                 ".yaml", ".yml", ".toml", ".txt", ".zsh", ".bash"}
# Directories never worth walking when deciding if a companion is shared.
PRUNE = {".git", "node_modules", "cache", "__pycache__", "dist", "build",
         "skills-disabled", "agents-disabled", "commands-disabled", "venv"}


# ---------------------------------------------------------------- utilities

def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"version": 2, "disabled": {}}
    try:
        return json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError) as e:
        die(f"state file unreadable ({STATE_FILE}): {e}")


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    tmp.replace(STATE_FILE)  # atomic: never leave a half-written state file


def log(action: str, type_: str, name: str, result: str, detail: str = "") -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "action": action,
           "type": type_, "name": name, "result": result}
    if detail:
        rec["detail"] = detail
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def harness_of(name: str) -> tuple[Path, tuple[str, ...], str | None]:
    if name not in HARNESSES:
        die(f"unknown harness {name!r} (expected: {', '.join(HARNESSES)})")
    return HARNESSES[name]


def claude_bin() -> str | None:
    """Find the claude CLI. It is often absent from a hook/agent PATH."""
    found = shutil.which("claude")
    if found:
        return found
    for cand in (HOME / ".local/bin/claude", HOME / ".claude/local/claude"):
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def run_cli(binary: str | None, args: list[str], cwd: str | None = None) -> tuple[bool, str]:
    """Run a harness CLI. `cwd` matters: `claude mcp -s local` is per-project."""
    if not binary:
        return False, "CLI not found on PATH"
    try:
        p = subprocess.run([binary, *args], capture_output=True, text=True,
                           timeout=120, cwd=cwd)
    except subprocess.TimeoutExpired:
        return False, "CLI timed out after 120s"
    except OSError as e:                     # cwd gone, binary unexecutable
        return False, f"cannot run CLI{f' in {cwd}' if cwd else ''}: {e}"
    return p.returncode == 0, (p.stdout + p.stderr).strip()


# ------------------------------------------------------------ the safe move

def safe_move(src: Path, dest_dir: Path) -> Path:
    """Move src INTO dest_dir, refusing every way this can silently corrupt data.

    The bug this exists to prevent: `mv X dest/` when `dest` does not exist
    RENAMES X to dest. The first item becomes the directory and its contents
    end up scattered at the parked-dir root. shutil.move() behaves the same.
    So: create the directory, then prove it is a directory, before touching src.
    """
    if not (src.exists() or src.is_symlink()):
        raise FileNotFoundError(f"{src} does not exist")

    # Order matters: mkdir(exist_ok=True) raises FileExistsError when the path
    # exists as a FILE, so the is_dir() guard has to come first to give a
    # useful error instead of a confusing one.
    if dest_dir.exists() and not dest_dir.is_dir():
        raise NotADirectoryError(
            f"{dest_dir} exists but is not a directory -- refusing to move "
            f"{src.name} (it would be renamed INTO that path and destroyed)"
        )
    dest_dir.mkdir(parents=True, exist_ok=True)
    if not dest_dir.is_dir():                       # lost a race, or odd fs
        raise NotADirectoryError(f"{dest_dir} is not a directory after mkdir")

    target = dest_dir / src.name
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"{target} already exists -- refusing to overwrite")

    shutil.move(str(src), str(target))
    return target


def prune_empty(start: Path, stop: Path) -> None:
    """Drop directories left empty by a move, up to but excluding `stop`.

    Restoring commands/orch/batch.md otherwise leaves an empty `orch/` behind
    in the park dir, which accumulates and inflates the parked counts.
    """
    cur = start
    while cur != stop and stop in cur.parents:
        try:
            cur.rmdir()          # refuses non-empty, which is the guard
        except OSError:
            return
        cur = cur.parent


def gitignored(path: Path, repo: Path) -> bool:
    """True if git ignores `path`. Unknown (no git / not a repo) counts as False."""
    try:
        p = subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", str(path)],
                           capture_output=True, timeout=10)
        return p.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


# ------------------------------------------------------------- companions

REF_RE = re.compile(
    r"""[`'"(\[]?\s*((?:\$HOME|~|\$\{?[A-Za-z_][A-Za-z0-9_]*\}?)?[\w./@-]*"""
    r"""\.(?:sh|py|mjs|cjs|js|ts|json|ya?ml|toml|txt|md))"""
)


def iter_text_files(root: Path):
    if root.is_file():
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in PRUNE and not d.startswith(".")]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix.lower() in TEXT_SUFFIXES:
                yield p


def read_text(p: Path) -> str:
    try:
        if p.stat().st_size > 2_000_000:
            return ""
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def find_companions(item: Path, home: Path) -> list[Path]:
    """Files under `home` that `item` references and that live outside `item`.

    Deliberately a heuristic over file text: a runtime-built path cannot be
    found by reading source. Everything it finds is REPORTED before anything
    moves, so a wrong guess is visible rather than silent.
    """
    # Resolve everything into one space: ~/.claude is often a symlink, and on
    # macOS /var itself is one, so an unresolved comparison silently drops
    # every companion as "outside the harness".
    home, item = home.resolve(), item.resolve()
    found: set[Path] = set()
    item_dir = item if item.is_dir() else item.parent
    for src in iter_text_files(item):
        for raw in REF_RE.findall(read_text(src)):
            token = raw.strip("`'\"([ ")
            # Anchor on the module's HOME, not the environment's: the harness
            # being operated on need not be the one this process runs under.
            if token.startswith("~"):
                token = str(HOME) + token[1:]
            token = token.replace("${HOME}", str(HOME)).replace("$HOME", str(HOME))
            expanded = os.path.expandvars(token)
            if "$" in expanded:          # unresolved variable -- cannot verify
                continue
            cands = ([Path(expanded)] if expanded.startswith("/")
                     else [home / expanded, item_dir / expanded])
            for c in cands:
                try:
                    c = c.resolve()
                except OSError:
                    continue
                if not c.is_file() or not c.is_relative_to(home):
                    continue
                if c == item or c.is_relative_to(item_dir):
                    continue             # part of the item itself
                found.add(c)
    return sorted(found)


def other_users(target: Path, item: Path, home: Path) -> list[Path]:
    """Live files outside `item` that also reference `target`."""
    home, item, target = home.resolve(), item.resolve(), target.resolve()
    needle = target.name
    rel = str(target.relative_to(home))
    users: list[Path] = []
    item_dir = item if item.is_dir() else item.parent
    for src in iter_text_files(home):
        if src == target or src.is_relative_to(item_dir):
            continue
        text = read_text(src)
        if needle in text or rel in text:
            users.append(src)
            if len(users) >= 3:          # enough to prove "shared"
                break
    return users


def park_companions(item: Path, home: Path, key: str) -> list[dict]:
    """Move companions used ONLY by this item; report the shared ones.

    Moving a shared helper (tg-send.sh is referenced by five different things)
    would silently break every other user, so sharing is a veto, not a warning.
    """
    home = home.resolve()
    moved: list[dict] = []
    for comp in find_companions(item, home):
        users = other_users(comp, item, home)
        if users:
            names = ", ".join(u.name for u in users[:2])
            print(f"    - kept {comp.relative_to(home)} (also used by {names})")
            continue
        dest = COMPANION_DIR / key / comp.relative_to(home).parent
        try:
            target = safe_move(comp, dest)
        except (OSError, FileExistsError, NotADirectoryError) as e:
            print(f"    ! companion {comp.name}: {e}")
            continue
        moved.append({"from": str(comp), "to": str(target)})
        print(f"    + parked {comp.relative_to(home)} (exclusive)")
    return moved


def restore_companions(entry: dict) -> None:
    for c in entry.get("companions", []):
        src, dst = Path(c["to"]), Path(c["from"])
        if not src.exists():
            print(f"    ! companion missing: {src}")
            continue
        try:
            safe_move(src, dst.parent)
        except (OSError, FileExistsError, NotADirectoryError) as e:
            print(f"    ! companion {dst.name}: {e}")
        else:
            print(f"    + restored {dst.name}")


# --------------------------------------------------------------- operations

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
                "harness": harness, "type": type_, "name": name,
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
                "harness": harness, "type": "plugin", "name": name, "native": True,
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
        else:
            state["disabled"].pop(key, None)
        print(f"  v plugin {name} {action}d")
        log(action, "plugin", name, "ok")
    return fails


# ---------------------------------------------------------------- mcp: json

def claude_mcp_config(name: str) -> tuple[dict, str, str | None] | None:
    """Find a server's RAW config in ~/.claude.json -> (config, scope, project).

    `claude mcp get` prints a human summary that silently drops auth fields
    (headers, headersHelper), so a backup built from it restores a server that
    then fails with 401. The raw entry is the only lossless source.

    Two places hold one: user scope at the top level, and local scope nested
    under projects/<dir>. The project path travels with the backup because
    `claude mcp remove -s local` only sees the project it is run in -- restore
    it from the wrong directory and the server reappears in the wrong project.

    Raises LookupError when one name is local-scope in several projects and
    the cwd does not pick a winner.
    """
    try:
        cfg = json.loads((HOME / ".claude.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None

    raw = cfg.get("mcpServers", {}).get(name)
    if raw is not None:
        return raw, "user", None

    hits = [(proj, pdata["mcpServers"][name])
            for proj, pdata in cfg.get("projects", {}).items()
            if isinstance(pdata, dict) and name in (pdata.get("mcpServers") or {})]
    if not hits:
        return None
    cwd = str(Path.cwd())
    for proj, raw in hits:
        if proj == cwd:
            return raw, "local", proj
    if len(hits) > 1:
        raise LookupError(
            f"local-scope in {len(hits)} projects "
            f"({', '.join(p for p, _ in hits)}) -- cd into the one you mean")
    return hits[0][1], "local", hits[0][0]


def claudeai_connector_names() -> set[str]:
    """Names claude.ai connectors are known under (account-level, no local
    mcpServers entry -- .claudeAiMcpEverConnected is the only place they are
    listed)."""
    try:
        cfg = json.loads((HOME / ".claude.json").read_text())
    except (OSError, json.JSONDecodeError):
        return set()
    return set(cfg.get("claudeAiMcpEverConnected", []) or [])


def toggle_claudeai_connector(action: str, name: str) -> tuple[bool, list | str]:
    """Fan a claude.ai connector on/off across every EXISTING project entry
    in ~/.claude.json's disabledMcpServers list -- the only place /mcp writes
    that state; no `claude mcp` CLI verb reaches it.

    ponytail: covers projects that exist now; a project dir opened for the
    first time later starts without the entry until this is re-run for it.
    """
    path = HOME / ".claude.json"
    try:
        cfg = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return False, f"~/.claude.json unreadable: {e}"

    touched = []
    for proj, pdata in cfg.get("projects", {}).items():
        if not isinstance(pdata, dict):
            continue
        cur = pdata.get("disabledMcpServers") or []
        has = name in cur
        if action == "disable" and not has:
            pdata["disabledMcpServers"] = [*cur, name]
            touched.append(proj)
        elif action == "enable" and has:
            pdata["disabledMcpServers"] = [n for n in cur if n != name]
            touched.append(proj)

    if not touched:
        return True, []
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    tmp.replace(path)
    return True, touched


# ---------------------------------------------------------------- mcp: toml

def toml_block(text: str, name: str) -> tuple[int, int] | None:
    """Line span of `[mcp_servers.<name>]` and its sub-tables, or None.

    Returned as a slice so the backup is the VERBATIM source text. Python's
    stdlib reads TOML (tomllib) but cannot write it, and hand-rolling a
    serializer would mangle comments and formatting -- a text slice is both
    lossless and far less code.
    """
    lines = text.splitlines(keepends=True)
    head = f"[mcp_servers.{name}]"
    sub = f"[mcp_servers.{name}."
    start = end = None
    for i, line in enumerate(lines):
        s = line.strip()
        if s == head:
            start = i
            end = len(lines)
        elif start is not None and s.startswith("["):
            if not s.startswith(sub):
                end = i
                break
    return None if start is None else (start, end)


def codex_mcp_remove(config: Path, name: str) -> str | None:
    text = config.read_text()
    span = toml_block(text, name)
    if span is None:
        return None
    lines = text.splitlines(keepends=True)
    block = "".join(lines[span[0]:span[1]])
    config.write_text("".join(lines[:span[0]] + lines[span[1]:]))
    return block


def codex_mcp_add(config: Path, block: str) -> None:
    text = config.read_text() if config.exists() else ""
    if text and not text.endswith("\n"):
        text += "\n"
    config.write_text(text + ("\n" if text else "") + block.rstrip("\n") + "\n")


def toggle_mcp(action: str, names: list[str], state: dict,
               harness: str, home: Path, backend: str | None) -> int:
    if backend is None:
        print(f"  x mcp: {harness} stores MCP outside a togglable config "
              f"(sqlite / none) -- not supported")
        return len(names)

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
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
                    "harness": harness, "type": "mcp", "name": name,
                    "connector": True,
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

        bp = BACKUP_DIR / f"{harness}__{name.replace('/', '_')}.json"
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
                bp.write_text(json.dumps(raw, indent=2, ensure_ascii=False))
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
                bp.write_text(json.dumps({"toml": block}, ensure_ascii=False, indent=2))

            state["disabled"][key] = {
                "harness": harness, "type": "mcp", "name": name,
                "backend": backend, "backup": str(bp),
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


# -------------------------------------------------------------------- views

def cmd_list(type_filter: str | None, state: dict) -> int:
    items = [v for v in state["disabled"].values()
             if not type_filter or v["type"] == type_filter]
    if not items:
        print("nothing disabled")
        return 0
    for v in sorted(items, key=lambda x: (x.get("harness", ""), x["type"], x["name"])):
        extra = f"  +{len(v['companions'])} files" if v.get("companions") else ""
        print(f"  {v.get('harness', '?'):<9} {v['type']:<8} {v['name']:<36} "
              f"since {v['at'][:10]}{extra}")
    print(f"\n{len(items)} disabled")
    return 0


def cmd_status(state: dict) -> int:
    print(f"state   {STATE_FILE}  ({len(state['disabled'])} disabled)")
    print(f"log     {LOG_FILE}")
    print(f"claude  {claude_bin() or 'NOT FOUND -- plugin/mcp actions will fail'}")
    if LEGACY_STATE_DIR.exists():
        done = any(k.startswith("claude:") for k in state["disabled"])
        print(f"legacy  {LEGACY_STATE_DIR} present -- "
              + ("already imported; safe to delete once verified" if done
                 else "run `migrate` to import it"))
    for hname, (home, types, backend) in HARNESSES.items():
        if not home.is_dir():
            print(f"{hname:<9} {home}  (not installed)")
            continue
        bits = [t for t in types if t not in SUBDIRS or (home / SUBDIRS[t]).is_dir()]
        print(f"{hname:<9} {home}  types: {','.join(bits)}  mcp: {backend or '-'}")
        for t in bits:
            if t not in SUBDIRS:
                continue
            parked = home / f"{SUBDIRS[t]}-disabled"
            # Skills are directories; agents and commands are files that may
            # sit one level down. Counting rglob("*") for skills would report
            # every file inside every skill.
            if not parked.is_dir():
                items = []
            elif t == "skill":
                items = list(parked.iterdir())
            else:
                items = [p for p in parked.rglob("*") if p.is_file()]
            ign = "gitignored" if gitignored(parked, home) else "NOT gitignored"
            print(f"          {t:<8} {len(items):>3} parked  [{ign}]")
            tracked = {e["parked_at"] for e in state["disabled"].values()
                       if e.get("harness") == hname and e.get("type") == t}
            untracked, twins = parked_drift(items, parked, home / SUBDIRS[t], tracked)
            if untracked:
                print(f"                   ! {len(untracked)} untracked (parked outside this tool)"
                      + (f", {len(twins)} also live: {', '.join(twins)}"
                         " -- stale copies; `disable` of these names will refuse" if twins else ""))
    return 0


def parked_drift(items: list[Path], parked: Path, live: Path,
                 tracked: set[str]) -> tuple[list[Path], list[str]]:
    """Parked items with no state entry, and the names among them that also exist live."""
    untracked = [p for p in items if str(p) not in tracked]
    twins = sorted(str(p.relative_to(parked)) for p in untracked
                   if (live / p.relative_to(parked)).exists() or (live / p.relative_to(parked)).is_symlink())
    return untracked, twins


def cmd_migrate(state: dict) -> int:
    """Import ~/.claude-toggle state so existing rollbacks keep working."""
    old_file = LEGACY_STATE_DIR / "state.json"
    if not old_file.is_file():
        print(f"nothing to migrate ({old_file} not found)")
        return 0
    try:
        old = json.loads(old_file.read_text())
    except (OSError, json.JSONDecodeError) as e:
        die(f"legacy state unreadable: {e}")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    added = 0
    for okey, entry in old.get("disabled", {}).items():
        new = dict(entry, harness="claude")
        if entry["type"] == "mcp" and entry.get("backup"):
            src = Path(entry["backup"])
            dst = BACKUP_DIR / f"claude__{entry['name'].replace('/', '_')}.json"
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
    print(f"migrated {added} entries from {LEGACY_STATE_DIR}")
    print("legacy directory left untouched -- delete it yourself once happy")
    return 0


# --------------------------------------------------------------------- main

def apply_changes(changes: list, state: dict) -> int:
    """Run the staged picker changes, batched per harness/type/direction."""
    batches: dict[tuple[str, str, str], list[str]] = {}
    for row in changes:
        action = "enable" if row.staged else "disable"
        batches.setdefault((row.harness, row.type, action), []).append(row.name)

    fails = 0
    for (harness, type_, action), names in sorted(batches.items()):
        home, supported, backend = HARNESSES[harness]
        print(f"\n{action} {type_} on {harness}:")
        if type_ in SUBDIRS:
            fails += toggle_dir_type(action, type_, names, state, harness, home)
        elif type_ == "mcp":
            fails += toggle_mcp(action, names, state, harness, home, backend)
    return fails


def cmd_ui(state: dict) -> int:
    try:
        import ui
    except ImportError as e:                 # no curses build (rare)
        die(f"interactive UI unavailable: {e}")
    changes = ui.pick(state, HARNESSES, SUBDIRS)
    if changes is None:
        print("cancelled -- nothing changed")
        return 0
    if not changes:
        print("no changes")
        return 0
    fails = apply_changes(changes, state)
    save_state(state)
    if any(r.type == "mcp" for r in changes):
        print("\nMCP changed -- open a NEW session for it to take effect.")
    return 1 if fails else 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1

    harness = "claude"
    if "--harness" in argv:
        i = argv.index("--harness")
        if i + 1 >= len(argv):
            die("--harness needs a value")
        harness = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]

    cmd, rest = argv[0], argv[1:]
    state = load_state()

    if cmd in ("ui", "pick"):
        return cmd_ui(state)
    if cmd == "status":
        return cmd_status(state)
    if cmd == "migrate":
        rc = cmd_migrate(state)
        save_state(state)
        return rc
    if cmd == "list":
        tf = rest[0] if rest else None
        if tf and tf not in TYPES:
            die(f"unknown type {tf!r} (expected: {', '.join(TYPES)})")
        return cmd_list(tf, state)
    if cmd not in ("disable", "enable"):
        die(f"unknown command {cmd!r} (expected: disable, enable, list, status, migrate)")
    if len(rest) < 2:
        die(f"usage: agent_toggle.py {cmd} <{'|'.join(TYPES)}> <name>... [--harness H]")

    type_, names = rest[0], rest[1:]
    if type_ not in TYPES:
        die(f"unknown type {type_!r} (expected: {', '.join(TYPES)})")

    home, supported, backend = harness_of(harness)
    if not home.is_dir():
        die(f"{harness} is not installed ({home} does not exist)")
    if type_ not in supported:
        die(f"{harness} has no {type_} support (it has: {', '.join(supported)})")

    if type_ in SUBDIRS:
        fails = toggle_dir_type(cmd, type_, names, state, harness, home)
    elif type_ == "plugin":
        fails = toggle_plugin(cmd, names, state, harness)
    else:
        fails = toggle_mcp(cmd, names, state, harness, home, backend)

    save_state(state)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
