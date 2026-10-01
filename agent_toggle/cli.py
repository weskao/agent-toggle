"""Temporarily disable / re-enable AI-agent resources across harnesses.

Skills, agents, commands, rules, plugins and MCP servers can all be parked and put
back. Nothing is ever deleted.

Supported harnesses: claude (Claude Code), codex, grok, openclaw.
Not every harness has every resource type; unsupported pairs fail loudly
instead of silently doing nothing.

State lives in ONE place ($HOME/.agent-toggle/), never as sidecar files next
to the targets -- a user's `git status` must not change because of our
bookkeeping.

Usage:
    agent_toggle.py ui                         # interactive picker (curses)
    agent_toggle.py disable <type> <name>...   [--harness H] [--dry-run]
    agent_toggle.py enable  <type> <name>...   [--harness H] [--dry-run]
    agent_toggle.py list [<type>]              # what is currently disabled
    agent_toggle.py status                     # health check
    agent_toggle.py migrate                    # import old ~/.claude-toggle state
    agent_toggle.py install-shims [--dry-run]  # write the skill shim into each harness

    <type> = skill | agent | command | rule | plugin | mcp
    --json prints exactly one JSON document; exit codes: 0 ok, 1 partial
    failure, 2 usage error, 3 locked, 4 unsupported pair / harness missing.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import sys
import traceback
from pathlib import Path

from . import __version__, fs, store
from .backends.plugin_cli import claude_bin
from .fs import gitignored
from .harnesses import TYPES, harness_of, harnesses
from .mechanisms import toggle_dir_type, toggle_mcp, toggle_plugin
from .output import CliError, Result, die
from .store import load_state, save_state


def cmd_list(type_filter: str | None, state: dict, out: Result,
             harness: str | None = None) -> None:
    items = [v for v in state["disabled"].values()
             if (not type_filter or v["type"] == type_filter)
             and (not harness or v.get("harness") == harness)]
    if not items:
        out.say("nothing disabled")
        return
    for v in sorted(items, key=lambda x: (x.get("harness", ""), x["type"], x["name"])):
        extra = f"  +{len(v['companions'])} files" if v.get("companions") else ""
        out.say(f"  {v.get('harness', '?'):<9} {v['type']:<8} {v['name']:<36} "
                f"since {v['at'][:10]}{extra}")
        out.row(v.get("harness"), v["type"], v["name"], "list", "disabled",
                f"since {v['at'][:10]}", show=False, at=v["at"],
                mechanism=v.get("mechanism"), companions=len(v.get("companions") or []))
    out.say(f"\n{len(items)} disabled")


def cmd_status(state: dict, out: Result, only: str | None = None) -> None:
    legacy_state_dir = fs.legacy_state_dir()
    claude = claude_bin()
    legacy = None
    out.say(f"state   {fs.state_file()}  ({len(state['disabled'])} disabled)")
    out.say(f"log     {fs.log_file()}")
    loose = [p for p in (fs.state_file(), *fs.backup_dir().glob("*.json"))
             if fs.too_open(p, 0o077)]
    for p in loose:
        out.say(f"WARNING {p} is looser than 0600 -- fix: chmod 600 {p}", warn=True)
    if fs.too_open(fs.state_dir()):
        out.say(f"WARNING {fs.state_dir()} is group/world readable -- backups may hold "
                f"auth headers; fix: chmod 700 {fs.state_dir()}", warn=True)
    out.say(f"claude  {claude or 'NOT FOUND -- plugin/mcp actions will fail'}")
    if legacy_state_dir.exists():
        done = any(k.startswith("claude:") for k in state["disabled"])
        legacy = "imported" if done else "present"
        out.say(f"legacy  {legacy_state_dir} present -- "
                + ("already imported; safe to delete once verified" if done
                   else "run `migrate` to import it"))
    out.row(None, None, None, "status", "ok", "", show=False,
            state_file=str(fs.state_file()), log_file=str(fs.log_file()),
            disabled=len(state["disabled"]), claude_cli=claude, legacy=legacy)
    for hname, h in harnesses().items():
        if only and hname != only:
            continue
        home, backend = h.home, h.backend
        if not home.is_dir():
            out.say(f"{hname:<9} {home}  (not installed)")
            out.row(hname, None, None, "status", "not-installed", "", show=False,
                    home=str(home))
            continue
        bits = [t for t in h.types
                if t not in h.dirs or any((home / s).is_dir() for s in h.dirs[t])]
        out.say(f"{hname:<9} {home}  types: {','.join(bits)}  mcp: {backend or '-'}")
        info: dict = {}
        for t in bits:
            if t not in h.dirs:
                continue
            tracked = {e["parked_at"] for e in state["disabled"].values()
                       if e.get("harness") == hname and e.get("type") == t}
            items: list[Path] = []
            untracked: list[Path] = []
            twins: list[str] = []
            ign = True
            for sub in h.dirs[t]:
                parked = home / f"{sub}-disabled"
                # Skills are directories; agents and commands are files that may
                # sit one level down. Counting rglob("*") for skills would report
                # every file inside every skill.
                if not parked.is_dir():
                    found = []
                elif t == "skill":
                    found = list(parked.iterdir())
                else:
                    found = [p for p in parked.rglob("*") if p.is_file()]
                items += found
                ign = ign and gitignored(parked, home)
                u, tw = parked_drift(found, parked, home / sub, tracked)
                untracked += u
                twins += tw
            out.say(f"          {t:<8} {len(items):>3} parked  "
                    f"[{'gitignored' if ign else 'NOT gitignored'}]")
            if untracked:
                out.say(f"                   ! {len(untracked)} untracked (parked outside this tool)"
                        + (f", {len(twins)} also live: {', '.join(twins)}"
                           " -- stale copies; `disable` of these names will refuse" if twins else ""),
                        warn=True)
            info[t] = {"parked": len(items), "gitignored": ign,
                       "untracked": len(untracked), "live_twins": twins}
        out.row(hname, None, None, "status", "installed", "", show=False,
                home=str(home), types=bits, mcp=backend, parked=info)


def parked_drift(items: list[Path], parked: Path, live: Path,
                 tracked: set[str]) -> tuple[list[Path], list[str]]:
    """Parked items with no state entry, and the names among them that also exist live."""
    untracked = [p for p in items if str(p) not in tracked]
    twins = sorted(str(p.relative_to(parked)) for p in untracked
                   if (live / p.relative_to(parked)).exists() or (live / p.relative_to(parked)).is_symlink())
    return untracked, twins


def apply_changes(changes: list, state: dict, out: Result | None = None) -> int:
    """Run the staged picker changes, batched per harness/type/direction."""
    out = out or Result()
    batches: dict[tuple[str, str, str], list[str]] = {}
    for row in changes:
        action = "enable" if row.staged else "disable"
        batches.setdefault((row.harness, row.type, action), []).append(row.name)

    table = harnesses()
    fails = 0
    for (harness, type_, action), names in sorted(batches.items()):
        h = table[harness]
        out.say(f"\n{action} {type_} on {harness}:")
        if type_ in h.dirs:
            fails += toggle_dir_type(action, type_, names, state, harness, h.home, out)
        elif type_ == "mcp":
            fails += toggle_mcp(action, names, state, harness, h.home, h.backend, out)
    return fails


def cmd_ui(state: dict, out: Result) -> None:
    try:
        from .ui import picker as ui
    except ImportError as e:                 # no curses build (rare)
        die(f"interactive UI unavailable: {e}")
    changes = ui.pick(state, harnesses())
    if changes is None:
        out.say("cancelled -- nothing changed")
        return
    if not changes:
        out.say("no changes")
        return
    with fs.lock():
        state = load_state()          # re-read: the picker's copy may be stale
        try:
            apply_changes(changes, state, out)
        finally:
            save_state(state)         # keep what already moved even if a later item crashed
    if any(r.type == "mcp" for r in changes):
        out.say("\nMCP changed -- open a NEW session for it to take effect.")


def cmd_toggle(args: argparse.Namespace, out: Result) -> None:
    action, type_, names, harness = args.command, args.type, args.names, args.harness
    h = harness_of(harness)
    home, supported = h.home, h.types
    if not home.is_dir():
        die(f"{harness} is not installed ({home} does not exist)", 4)
    if type_ not in supported:
        die(f"{harness} has no {type_} support (it has: {', '.join(supported)})", 4)

    def run(state: dict) -> None:
        if type_ in h.dirs:
            toggle_dir_type(action, type_, names, state, harness, home, out, args.dry_run)
        elif type_ == "plugin":
            toggle_plugin(action, names, state, harness, out, args.dry_run)
        else:
            toggle_mcp(action, names, state, harness, home, h.backend, out, args.dry_run)

    if args.dry_run:                # no lock, no state write, no log: plan only
        run(load_state(write_back=False))
        return
    with fs.lock():
        state = load_state()
        try:
            run(state)
        finally:
            save_state(state)       # keep what already moved even if a later item crashed


def cmd_migrate(out: Result) -> None:
    buf = io.StringIO()             # store.migrate prints; fold it into the result
    with fs.lock():
        state = load_state()
        with contextlib.redirect_stdout(buf):
            store.migrate(state)
        save_state(state)
    lines = buf.getvalue().splitlines()
    out.say(buf.getvalue().rstrip("\n"))
    out.row(None, None, None, "migrate", "ok", "; ".join(lines), show=False)


SHIM_TEMPLATE = Path(__file__).with_name("shims") / "claude.md.tmpl"


def cmd_install_shims(args: argparse.Namespace, out: Result) -> None:
    """Write <home>/skills/agent-toggle/SKILL.md into every installed harness that
    supports skills; keep park dirs out of an existing harness-home .gitignore."""
    template = SHIM_TEMPLATE.read_text(encoding="utf-8")
    root = Path(__file__).resolve().parent.parent
    if (root / "agent_toggle.py").is_file():
        # Harness dirs often sync across machines: write `~`-relative under $HOME.
        try:
            shown = "~/" + root.relative_to(fs.home()).as_posix()
        except ValueError:
            shown = str(root)
        text = template.replace("__AGENT_TOGGLE_ROOT__", shown)
    else:                                 # installed package: no checkout to point at
        text = "".join(ln for ln in template.splitlines(keepends=True)
                       if "__AGENT_TOGGLE_ROOT__" not in ln)
    table = harnesses()
    park = sorted({f"{sub}-disabled" for h in table.values()
                   for subs in h.dirs.values() for sub in subs})
    verb = "would install" if args.dry_run else "installed"
    installed = 0
    for hname, h in table.items():
        home = h.home
        if args.harness and hname != args.harness:
            continue
        if "skill" not in h.dirs or not home.is_dir():
            out.row(hname, None, None, "install-shims", "skipped",
                    "not installed" if not home.is_dir() else "no skill support",
                    show=False, home=str(home))
            continue
        dest = home / h.dirs["skill"][0] / "agent-toggle" / "SKILL.md"
        if not args.dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
        out.row(hname, None, None, "install-shims", "planned" if args.dry_run else "ok",
                str(dest), show=False, home=str(home))
        out.say(f"  {verb}  {dest}")
        installed += 1
        # A tracked park dir turns every disable into deletion noise in
        # `git status`; only touch a .gitignore that already exists.
        ignore = home / ".gitignore"
        if ignore.is_file():
            body = ignore.read_text(encoding="utf-8")
            missing = [f"{d}/" for d in park if f"{d}/" not in body.splitlines()]
            if missing and not args.dry_run:
                with ignore.open("a", encoding="utf-8") as fh:
                    fh.write(("" if body.endswith("\n") or not body else "\n")
                             + "".join(f"{m}\n" for m in missing))
            if missing:
                out.say(f"  gitignore  {ignore}: {' '.join(missing)}")
    if not installed:
        die("no harness found", 4)
    out.say(f"{installed} harness(es) {'planned' if args.dry_run else 'installed'}")


COMMANDS = ("ui", "pick", "status", "list", "migrate", "disable", "enable", "install-shims")
_GLOBAL_FLAGS = ("--json", "-v", "--verbose")


def normalize_argv(argv: list[str]) -> list[str]:
    """Accept `--status` for `status` and bare `help` / `version` for `--help` / `--version`.

    Only the command position is rewritten (after any leading global flags), so a
    resource that happens to be named `list` or `help` is never touched.
    """
    argv = list(argv)
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--harness":
            i += 2
        elif a in _GLOBAL_FLAGS or a.startswith("--harness="):
            i += 1
        else:
            if a.startswith("--") and a[2:] in COMMANDS:
                argv[i] = a[2:]
            elif a in ("help", "version"):
                argv[i] = "--" + a
            break
    return argv


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        die(message, 2)


def build_parser() -> argparse.ArgumentParser:
    # SUPPRESS: a flag given before the subcommand must survive the subparser's
    # own defaults, so neither level sets a default; main() fills them in.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--harness", choices=list(harnesses()), default=argparse.SUPPRESS,
                        help="target harness (default: claude)")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="print exactly one JSON document instead of text")
    common.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS,
                        help="print a traceback on unexpected errors")
    common.add_argument("--version", action="version", default=argparse.SUPPRESS,
                        version=f"agent-toggle {__version__}")
    p = _Parser(prog="agent-toggle", parents=[common],
                description="Temporarily disable / re-enable AI-agent resources.",
                epilog="exit codes: 0 ok, 1 partial failure, 2 usage error, "
                       "3 locked, 4 unsupported pair / harness not installed")
    sub = p.add_subparsers(dest="command", required=True, metavar="command",
                           parser_class=_Parser)
    sub.add_parser("ui", aliases=["pick"], parents=[common], help="interactive picker")
    sub.add_parser("status", parents=[common], help="health check")
    ls = sub.add_parser("list", parents=[common], help="what is currently disabled")
    ls.add_argument("type", nargs="?", choices=TYPES)
    sub.add_parser("migrate", parents=[common], help="import an older ~/.claude-toggle state")
    sp = sub.add_parser("install-shims", parents=[common],
                        help="write the skill shim into every installed harness")
    sp.add_argument("--dry-run", action="store_true", help="show the plan; change nothing")
    for name, verb in (("disable", "park"), ("enable", "restore")):
        sp = sub.add_parser(name, parents=[common], help=f"{verb} one or more items")
        sp.add_argument("type", choices=TYPES)
        sp.add_argument("names", nargs="+", metavar="name")
        sp.add_argument("--dry-run", action="store_true",
                        help="show the plan; change nothing")
    return p


def main(argv: list[str] | None = None) -> int:
    """Run one command and return its exit code (the process exit code)."""
    argv = normalize_argv(sys.argv[1:] if argv is None else argv)
    out = Result(next((a for a in argv if a in COMMANDS), ""), "--json" in argv)
    try:
        args = build_parser().parse_args(argv)
        out.command = "ui" if args.command == "pick" else args.command
        out.json_mode = getattr(args, "json", False)
        args.harness = getattr(args, "harness", None)
        cmd = out.command
        if cmd == "ui":
            if out.json_mode:
                die("ui is interactive; --json is not supported", 2)
            cmd_ui(load_state(write_back=False), out)
        elif cmd == "status":
            cmd_status(load_state(write_back=False), out, args.harness)
        elif cmd == "list":
            cmd_list(args.type, load_state(write_back=False), out, args.harness)
        elif cmd == "migrate":
            cmd_migrate(out)
        elif cmd == "install-shims":
            cmd_install_shims(args, out)
        else:
            args.harness = args.harness or "claude"
            cmd_toggle(args, out)
        rc = out.exit_code()
    except fs.Locked as e:
        out.error(str(e))
        rc = 3
    except CliError as e:
        out.error(e.msg)
        rc = e.code
    except SystemExit as e:             # argparse --help / --version
        return e.code or 0
    except Exception as e:              # never a bare traceback; --json stays one document
        out.error(f"{type(e).__name__}: {e}")
        if os.environ.get("AGENT_TOGGLE_DEBUG") == "1" or {"-v", "--verbose"} & set(argv):
            traceback.print_exc()
        rc = 1
    out.render()
    return rc
