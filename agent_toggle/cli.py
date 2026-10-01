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

import sys
from pathlib import Path

from . import fs, store
from .backends.plugin_cli import claude_bin
from .fs import gitignored
from .harnesses import SUBDIRS, TYPES, harness_of, harnesses
from .mechanisms import toggle_dir_type, toggle_mcp, toggle_plugin
from .output import die
from .store import load_state, save_state


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
    legacy_state_dir = fs.legacy_state_dir()
    print(f"state   {fs.state_file()}  ({len(state['disabled'])} disabled)")
    print(f"log     {fs.log_file()}")
    print(f"claude  {claude_bin() or 'NOT FOUND -- plugin/mcp actions will fail'}")
    if legacy_state_dir.exists():
        done = any(k.startswith("claude:") for k in state["disabled"])
        print(f"legacy  {legacy_state_dir} present -- "
              + ("already imported; safe to delete once verified" if done
                 else "run `migrate` to import it"))
    for hname, (home, types, backend) in harnesses().items():
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


def apply_changes(changes: list, state: dict) -> int:
    """Run the staged picker changes, batched per harness/type/direction."""
    batches: dict[tuple[str, str, str], list[str]] = {}
    for row in changes:
        action = "enable" if row.staged else "disable"
        batches.setdefault((row.harness, row.type, action), []).append(row.name)

    table = harnesses()
    fails = 0
    for (harness, type_, action), names in sorted(batches.items()):
        home, supported, backend = table[harness]
        print(f"\n{action} {type_} on {harness}:")
        if type_ in SUBDIRS:
            fails += toggle_dir_type(action, type_, names, state, harness, home)
        elif type_ == "mcp":
            fails += toggle_mcp(action, names, state, harness, home, backend)
    return fails


def cmd_ui(state: dict) -> int:
    try:
        from .ui import picker as ui
    except ImportError as e:                 # no curses build (rare)
        die(f"interactive UI unavailable: {e}")
    changes = ui.pick(state, harnesses(), SUBDIRS)
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


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
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
        rc = store.migrate(state)
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
