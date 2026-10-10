"""Temporarily disable / re-enable AI-agent resources across harnesses.

Skills, agents, commands, rules, plugins and MCP servers can all be parked and put
back. Nothing is ever deleted.

Supported harnesses: claude (Claude Code), codex, grok, opencode, openclaw, copilot,
vibe, devin, agy. Not every harness has every resource type; unsupported pairs fail
loudly instead of silently doing nothing.

State lives in ONE place ($HOME/.agent-toggle/), never as sidecar files next
to the targets -- a user's `git status` must not change because of our
bookkeeping.

Usage:
    agent_toggle.py ui [--dry-run] [--project D]   # interactive picker (curses, else numbered menu)
    agent_toggle.py cost [--type T] [--project D]  # startup token estimates, biggest first
    agent_toggle.py disable <type> <name>...   [--harness H] [--dry-run]
    agent_toggle.py enable  <type> <name>...   [--harness H] [--dry-run]
    agent_toggle.py enable --all [--harness H] [--dry-run]   # restore everything
    agent_toggle.py undo [--dry-run]           # reverse the last logged batch
    agent_toggle.py disable|enable <type> <name>... --project <dir>   # a repo's .claude/ + .mcp.json
    agent_toggle.py enable --all --project <dir> | profile save|apply|diff ... --project <dir>
    agent_toggle.py list [<type>] [--project D] # what is currently disabled
    agent_toggle.py status                     # health check
    agent_toggle.py doctor [--harness H]       # drift + state check (y/n fixes on a TTY); exit 1 on problems
    agent_toggle.py migrate                    # import old state, move legacy park dirs
    agent_toggle.py profile save|apply|diff|list [name|file] [--out F] [--dry-run]
    agent_toggle.py install-shims [--dry-run] [--gitignore | --no-gitignore]
                                               # skill shim per harness; stale .gitignore lines
    agent_toggle.py config [--json]            # settings menu (curses, else numbered list)
    agent_toggle.py config test|sync-ci        # Telegram test message / GitHub CI secrets
    agent_toggle.py help [command]             # styled help (also --help, <command> --help)

    <type> = skill | agent | command | rule | plugin | mcp
    --json prints exactly one JSON document; exit codes: 0 ok, 1 partial
    failure, 2 usage error, 3 locked, 4 unsupported pair / harness missing.
    Settings: $HOME/.agent-toggle/config.json (see `config`); env overrides
    AGENT_TOGGLE_UPDATE_CHECK / _COLOR / _LANG / _DEFAULT_HARNESS.
    Update check: on by default, stderr only; AGENT_TOGGLE_UPDATE_CHECK=0 turns it off.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import shutil
import sys
import traceback
from pathlib import Path

from . import (
    __version__,
    config,
    cost,
    doctor,
    fs,
    i18n,
    ops,
    profiles,
    settings,
    store,
    undo,
    update_prompt,
)
from . import help as helptext
from .backends.plugin_cli import claude_bin
from .harnesses import TYPES, harness_of, harnesses, project_view
from .mechanisms import (
    dir_view,
    fail_row,
    legacy_parks,
    migrate_parks,
    settle,
    sync_marker_note,
    validate_name,
)
from .output import COLOR_MODES, CliError, Result, die, scan_color, use_color
from .store import load_state, save_state
from .ui import logo
from .ui import theme as ui_theme


def project_of(key: str, entry: dict) -> str | None:
    """The --project dir of a project-scope entry (`harness@<sha8>:...`), else None.
    (A claude local-scope mcp entry's `project` is its cwd, not a --project scope.)"""
    try:
        scoped = store.parse_key(key)[1]
    except ValueError:
        return None
    return str(entry.get("project")) if scoped else None


def _list_of(v: dict, key: str) -> list:
    """A state entry's list field; a hand-edited non-list counts as empty."""
    x = v.get(key)
    return x if isinstance(x, list) else []


def _all_hidden(hidden: frozenset[str], owner, shared) -> bool:
    """True when every harness an item belongs to is off in settings (ui/model.py prefs rule);
    an item shared with an enabled harness stays visible."""
    return hidden.issuperset((owner, *shared))


def _say_hidden(out: Result, msg: str) -> None:
    """The dim "hidden by settings" note; --json carries it in the envelope's `warnings`."""
    out.say(msg, warn=True, style="dim")


def cmd_list(type_filter: str | None, state: dict, out: Result,
             harness: str | None = None, project: str | None = None,
             hidden: frozenset[str] = frozenset()) -> None:
    if project is not None:                  # one project's entries only
        project = str(project_view(project).project)       # exit 4 like disable --project
        state = store.scope_state(state, project)
    matched = [(project_of(k, v), v) for k, v in state["disabled"].items()
               if (not type_filter or v.get("type") == type_filter)
               and (not harness or harness in (v.get("harness"), *_list_of(v, "shared_with")))]
    items = [(p, v) for p, v in matched if not _all_hidden(hidden, v.get("harness"), _list_of(v, "shared_with"))]
    note = f"{len(matched) - len(items)} hidden by settings (agent-toggle config)" if len(matched) > len(items) else ""
    if not items:
        _say_hidden(out, note) if note else out.say("nothing disabled")
        return
    for proj, v in sorted(items, key=lambda x: (str(x[1].get("harness", "")), str(x[1].get("type")),
                                                 str(x[1].get("name")), x[0] or "")):
        shared = ", ".join(map(str, _list_of(v, "shared_with")))
        extra = f"  +{len(_list_of(v, 'companions'))} files" if _list_of(v, "companions") else ""
        extra += f"  shared with {shared}" if shared else ""
        extra += f"  project {proj}" if proj else ""
        at = str(v.get("at", "?"))
        who = out.paint(f"{v.get('harness', '?')!s:<9}", "cyan")
        out.say(f"  {who} {v.get('type')!s:<8} {v.get('name')!s:<36} "
                + out.paint(f"since {at[:10]}{extra}", "dim"))
        out.row(v.get("harness"), v.get("type"), v.get("name"), "list", "disabled",
                f"since {at[:10]}", show=False, at=v.get("at"),
                mechanism=v.get("mechanism"), companions=len(_list_of(v, "companions")),
                shared_with=_list_of(v, "shared_with"), **({"project": proj} if proj else {}))
    out.say(f"\n{len(items)} disabled")
    if note:
        _say_hidden(out, note)


def cmd_status(state: dict, out: Result, only: str | None = None,
               hidden: frozenset[str] = frozenset()) -> None:
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
    if state.get("pending") and fs.lock_held():
        out.say("WARNING another agent-toggle run is in progress (it holds the lock)", warn=True)
    else:
        for key in state.get("pending", {}):      # an op a killed run left in flight
            out.say(f"WARNING {settle(state, key, dry_run=True)[1]}", warn=True)
    if legacy_state_dir.exists():
        done = any(k.startswith("claude:") for k in state["disabled"])
        legacy = "imported" if done else "present"
        out.say(f"legacy  {legacy_state_dir} present -- "
                + ("already imported; safe to delete once verified" if done
                   else "run `migrate` to import it"))
    table = harnesses()
    old_parks = [str(old) for old, _, _ in legacy_parks(table, only)]
    for old in old_parks:
        out.say(f"WARNING {old} is a legacy park dir -- run: agent-toggle migrate", warn=True)
    out.row(None, None, None, "status", "ok", "", show=False,
            state_file=str(fs.state_file()), log_file=str(fs.log_file()),
            disabled=len(state["disabled"]), claude_cli=claude, legacy=legacy,
            legacy_parks=old_parks)
    # every user park dir sits under parked_dir(): one check, not one per dir
    ign = fs.park_unignored() is None
    for hname, h in table.items():
        if (only and hname != only) or hname in hidden:
            continue
        home, backend = h.home, h.backend
        if not home.is_dir():
            out.say(f"{out.paint(f'{hname:<9}', 'cyan')} {home}  "
                    + out.paint("(not installed)", "dim"))
            out.row(hname, None, None, "status", "not-installed", "", show=False,
                    home=str(home))
            continue
        bits = [t for t in h.types
                if t not in h.dirs or any((home / s).is_dir() for s in h.dirs[t])]
        shown = ",".join(bits) if h.types else "(found, nothing supported yet)"
        out.say(f"{out.paint(f'{hname:<9}', 'cyan')} {home}  types: {shown}  mcp: {backend or '-'}")
        info: dict = {}
        for t in bits:
            if t not in h.dirs:
                continue
            # any harness's entry: a shared dir's items are tracked under its owner
            tracked = {e["parked_at"] for e in state["disabled"].values()
                       if e.get("type") == t and e.get("parked_at")}
            items: list[Path] = []
            untracked: list[Path] = []
            twins: list[str] = []
            shared: set[str] = set()
            for sub in h.dirs[t]:
                view = dir_view(table, hname, t, home, sub)
                parked = view.parked
                shared.update(set(view.sharers) - {hname})
                if msg := sync_marker_note(out, view):
                    out.say(f"                   ! {msg}", warn=True)
                # Skills are directories; agents and commands are files that may
                # sit one level down. Counting rglob("*") for skills would report
                # every file inside every skill.
                if not parked.is_dir():
                    found = []
                elif t == "skill":
                    found = list(parked.iterdir())
                else:
                    found = [p for p in parked.rglob("*") if p.is_file()]
                found = [p for p in found if not p.name.startswith(".")]   # .DS_Store, as doctor
                items += found
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
            if shared:
                out.say(f"                   shared dir with: {', '.join(sorted(shared))} "
                        f"(disable here also affects them)")
            info[t] = {"parked": len(items), "gitignored": ign, "untracked": len(untracked),
                       "live_twins": twins, "shared_with": sorted(shared)}
        out.row(hname, None, None, "status", "installed", "", show=False,
                home=str(home), types=bits, mcp=backend, parked=info)
    if n := len(hidden & set(table)):
        _say_hidden(out, f"{n} harness(es) hidden by settings (agent-toggle config)")
    projects: dict[tuple, int] = {}
    for k, e in state["disabled"].items():
        if (proj := project_of(k, e)) is not None and not _all_hidden(hidden, e.get("harness"), _list_of(e, "shared_with")):
            projects[e.get("harness"), proj] = projects.get((e.get("harness"), proj), 0) + 1
    for (hname, proj), n in sorted(projects.items(), key=str):
        out.say(f"project {proj}  ({n} parked under {fs.parked_dir()})")
        out.row(hname, None, None, "status", "project", "", show=False, project=proj, parked=n)
    # the inverse of `untracked`: a state entry whose parked item is gone (DESIGN s6)
    for k, e in state["disabled"].items():
        if (only and only not in (e.get("harness"), *_list_of(e, "shared_with"))
                or _all_hidden(hidden, e.get("harness"), _list_of(e, "shared_with"))):
            continue
        if gone := doctor.missing_parked(k, e):
            out.say(f"WARNING {e.get('harness')} {e.get('type')} {e.get('name')}: {gone}", warn=True)
            out.row(e.get("harness"), e.get("type"), e.get("name"), "status", "stale", gone,
                    show=False, parked_at=e.get("parked_at"))


def parked_drift(items: list[Path], parked: Path, live: Path,
                 tracked: set[str]) -> tuple[list[Path], list[str]]:
    """Parked items with no state entry, and the names among them that also exist live."""
    untracked = [p for p in items if str(p) not in tracked]
    twins = sorted(str(p.relative_to(parked)) for p in untracked
                   if (live / p.relative_to(parked)).exists() or (live / p.relative_to(parked)).is_symlink())
    return untracked, twins


def cmd_cost(state: dict, out: Result, harness: str | None = None,
             type_: str | None = None, project: str | None = None,
             hidden: frozenset[str] = frozenset()) -> None:
    """Estimated startup tokens per item, biggest first. Read-only. With `project`, that
    project's .claude and .mcp.json only (no plugins), else user scope."""
    proj = profiles.project_dir(project, harness)       # exit 4 like disable --project
    scoped, table = profiles.scope(state, proj)
    shown = [i for i in cost.inventory(scoped, table, out.warn, plugins=proj is None)
             if (not harness or harness in (i.harness, *i.shared_with))
             and (not type_ or i.type == type_)]
    items = [i for i in shown if not _all_hidden(hidden, i.harness, i.shared_with)]
    items.sort(key=lambda i: (-i.tokens, -i.would_save, i.harness, i.type, i.name))
    if proj:
        out.say(f"project {proj}")
    for i in items:
        what = (f"~{i.tokens:>6} tok  {i.basis}" if i.enabled
                else f"~{0:>6} tok  parked, would save ~{i.would_save} tok  ({i.basis})")
        extra = f"  shared with {', '.join(i.shared_with)}" if i.shared_with else ""
        tail = what + extra
        out.say(f"  {out.paint(f'{i.harness:<9}', 'cyan')} {i.type:<8} {i.name:<36} "
                + (tail if i.enabled else out.paint(tail, "dim")))
        out.row(i.harness, i.type, i.name, "cost", "ok", i.basis, show=False,
                enabled=i.enabled, tokens=i.tokens, would_save=i.would_save,
                chars=i.chars, shared_with=list(i.shared_with), mod=i.mod)
    live = sum(i.tokens for i in items)
    saved = sum(i.would_save for i in items)
    out.say(f"\n{len(items)} item(s): ~{live} tok loaded at startup; "
            f"~{saved} tok already saved by parked items  "
            f"(chars/{cost.CHARS_PER_TOKEN} estimate, +-25%)")
    if len(shown) > len(items):
        _say_hidden(out, f"{len(shown) - len(items)} hidden by settings (agent-toggle config)")
    out.row(harness, type_, None, "cost", "ok", "total", show=False, items=len(items),
            total_tokens=live, saved_tokens=saved, formula=cost.FORMULA,
            **({"project": str(proj)} if proj else {}))


def cmd_ui(state: dict, out: Result, dry_run: bool = False, project: str | None = None,
           harness: str | None = None) -> None:
    proj = profiles.project_dir(project, harness)       # exit 4 like disable --project
    scoped, table = profiles.scope(state, proj)
    try:
        from .ui import picker as ui
    except ImportError:                      # no curses (Windows without windows-curses)
        from .ui import menu as ui
    changes = ui.pick(scoped, table, plugins=not dry_run and proj is None,
                      color=use_color(sys.stdout, out.color), project=proj, dry_run=dry_run,
                      harness=harness)
    if changes is None:
        out.say("cancelled -- nothing changed")
        return
    if not changes:
        out.say("no changes")
        return
    plan = sorted((ops.Op(r.harness, r.type, "enable" if r.staged else "disable", r.name,
                          str(proj) if proj else None) for r in changes), key=lambda op: op[:3])
    # the picker's state copy may be stale: apply_plan re-reads it under the lock
    ops.apply_plan(plan, out, dry_run, batch=store.BATCH, headers=True)
    if dry_run:
        out.say("\ndry run -- nothing changed")
        return
    if any(r.type in ("mcp", "plugin") for r in changes):
        out.say("\nMCP/plugin changed -- open a NEW session for it to take effect.")


def cmd_toggle(args: argparse.Namespace, out: Result) -> None:
    action, type_, names, harness = args.command, args.type, args.names, args.harness
    if not type_ or not names:
        die(f"{action} needs <type> <name>... (or `enable --all`)", 2)
    for name in names:
        validate_name(name)
    if args.project is not None:
        # project scope: claude layout dir types only (DESIGN s5.5)
        if harness != "claude":
            die(f"--project supports only the claude layout, not {harness}", 4)
        h = project_view(args.project)
        if type_ not in (*h.types, "all"):
            die(f"--project supports {', '.join(h.types)} (not {type_})", 4)
        project = str(h.project)
    else:
        h, project = harness_of(harness), None
        home, supported = h.home, h.types
        if not home.is_dir():
            die(f"{harness} is not installed ({home} does not exist)", 4)
        if type_ not in (*supported, "all"):
            have = f"it has: {', '.join(supported)}" if supported else "no supported types"
            die(f"{harness} has no {type_} support ({have})", 4)
    pairs = [(type_, n) for n in names]
    if type_ == "all":
        pairs = _every_type(action, names, harness, h.project)
        for name in sorted(set(names) - {n for _, n in pairs}):
            fail_row(out, args.dry_run, harness, "all", action, name,
                     f"no {'live' if action == 'disable' else 'disabled'} item named {name} "
                     f"on {harness}", batch=store.BATCH, project=project)
    ops.apply_plan([ops.Op(harness, t, action, n, project) for t, n in pairs], out,
                   args.dry_run, batch=store.BATCH)


def _every_type(action: str, names: list[str], harness: str,
                project: Path | None) -> list[tuple[str, str]]:
    """`all`: each (type, name) on `harness` that `action` can act on -- live items for
    disable, parked ones for enable -- in one scope (user, or the --project dir)."""
    state, table = profiles.scope(load_state(write_back=False), project)
    want = action == "disable"
    # plugin ids are `name@marketplace`: skip the claude CLI call unless one could match
    plugins = project is None and any("@" in n for n in names)
    return sorted({(i.type, i.name) for i in cost.inventory(state, table, plugins=plugins)
                   if i.name in names and i.enabled == want
                   and harness in (i.harness, *i.shared_with)})


def cmd_migrate(out: Result) -> None:
    """Import ~/.claude-toggle state, then move legacy `<live>-disabled` park dirs into
    the central park dir (a killed run is repaired by the next one: see migrate_parks)."""
    buf = io.StringIO()             # store.migrate prints; fold it into the result
    with fs.lock():
        state = load_state()
        with contextlib.redirect_stdout(buf):
            store.migrate(state)
        parks = migrate_parks(state)
        save_state(state)
    lines = buf.getvalue().splitlines() + (
        parks or ["park dirs: nothing to migrate (no legacy <dir>-disabled/ dirs left)"])
    out.say("\n".join(lines))
    refused = [ln for ln in parks if ln.startswith("refused:")]
    out.row(None, None, None, "migrate", "error" if refused else "ok", "; ".join(lines),
            show=False)


SHIMS = Path(__file__).with_name("shims")
# Every template carries this line; install-shims only overwrites a file that has it.
SHIM_MARKER = ("<!-- agent-toggle shim: written by `agent-toggle install-shims`; "
               "re-running it overwrites this file -->")


def shim_text(harness: str) -> str:
    """The shim for `harness`: shims/<harness>.md.tmpl, else shims/generic.md.tmpl."""
    tmpl = SHIMS / f"{harness}.md.tmpl"
    template = (tmpl if tmpl.is_file() else SHIMS / "generic.md.tmpl").read_text(encoding="utf-8")
    # No checkout path: shims sync across machines and the checkout can live anywhere.
    return template.replace("__HARNESS__", harness)


def shim_refusal(dest: Path) -> str | None:
    """Why `dest` must not be overwritten (it is not a shim we wrote), else None."""
    if dest.is_symlink() or (dest.exists() and not dest.is_file()):
        return "is a symlink or not a regular file"
    if not dest.exists():
        return None
    try:
        text = dest.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        text = ""
    # Shims from before the marker existed: our frontmatter name plus our heading.
    legacy = text.startswith("---\nname: agent-toggle\n") and "\n# agent-toggle\n" in text
    ours = SHIM_MARKER in text or legacy
    return None if ours else "exists and lacks the agent-toggle shim marker"


def _reads_as(p: Path, text: str) -> bool:
    try:
        return p.read_text(encoding="utf-8") == text
    except (OSError, UnicodeDecodeError):
        return False


def cmd_install_shims(args: argparse.Namespace, out: Result) -> None:
    """Write <home>/skills/agent-toggle/SKILL.md into every installed harness that
    supports skills, then offer to drop stale legacy park-dir lines from a harness-home
    .gitignore (_stale_ignore_lines). A file there that is not our shim (no
    SHIM_MARKER) is refused, never overwritten. A harness switched off in settings is
    skipped unless named with --harness."""
    table = harnesses()
    on = set(settings.enabled_harnesses())
    verb = "would install" if args.dry_run else "installed"
    installed = found = 0
    homes = []
    for hname, h in table.items():
        home = h.home
        if args.harness and hname != args.harness:
            continue
        if "skill" not in h.dirs or not home.is_dir():
            out.row(hname, None, None, "install-shims", "skipped",
                    "not installed" if not home.is_dir() else "no skill support",
                    show=False, home=str(home))
            continue
        found += 1          # installed: an off harness is skipped, not "no harness found"
        if hname not in on and not args.harness:
            out.row(hname, None, None, "install-shims", "skipped", "off in settings",
                    show=False, home=str(home))
            out.say(f"  skipped {hname} (off in settings)")
            continue
        homes.append(h)
        # dirs[0] is OpenCode's first skills.paths redirect when set; if its parent dir
        # is missing, fall back to the first in-home dir instead of creating a tree.
        subs = h.dirs["skill"]
        sub = subs[0] if not Path(subs[0]).is_absolute() or (home / subs[0]).parent.is_dir() \
            else "skills"
        dest = home / sub / "agent-toggle" / "SKILL.md"
        view = dir_view(table, hname, "skill", home, sub)
        if view.owner != hname and table[view.owner].home.is_dir():
            # a redirect onto another installed harness's dir: that harness writes its own
            # shim there; never overwrite it with this harness's text
            out.row(hname, None, None, "install-shims", "skipped",
                    f"{dest} belongs to {view.owner}: run install-shims for it",
                    show=False, home=str(home))
            continue
        text = shim_text(hname)
        others = [p for s in subs if s != sub
                  and (home / s).resolve() != (home / sub).resolve()
                  and (p := home / s / "agent-toggle" / "SKILL.md").is_file()
                  and not p.is_symlink()]
        same = next((p for p in others if _reads_as(p, text)), None)
        if same and not dest.exists():
            # an alias dir (OpenCode scans ~/.claude/skills, ~/.agents/skills) already serves
            # this very shim: a second copy would only leave "which one wins" unchecked
            out.row(hname, None, None, "install-shims", "skipped", f"covered by {same}",
                    show=False, home=str(home))
            out.say(f"  skipped  {dest.parent.parent} (covered by {same})")
            continue
        if why := shim_refusal(dest):
            fix = (f"refused: {dest} {why}; fix: move it aside (or delete it if it is an "
                   f"older agent-toggle shim), then re-run install-shims")
            out.row(hname, None, None, "install-shims", "error", fix, show=False, home=str(home))
            out.say(f"  {fix}", style="red")
            continue
        if not args.dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
        out.row(hname, None, None, "install-shims", "planned" if args.dry_run else "ok",
                str(dest), show=False, home=str(home), also_seen=[str(p) for p in others])
        out.say(f"  {verb}  {dest}")
        if others:       # e.g. the claude shim, whose text says `--harness` defaults to claude
            out.say(f"    note: {hname} also loads {', '.join(map(str, others))} (a different "
                    f"shim); which one wins is unchecked")
        installed += 1
    for h in homes if not args.no_gitignore else ():
        _stale_ignore_lines(h, args, out)
    if not found:
        die("no harness found", 4)
    out.say(f"{installed} harness(es) {'planned' if args.dry_run else 'installed'}")


def _stale_ignore_lines(h, args: argparse.Namespace, out: Result) -> None:
    """Older versions of install-shims appended `<sub>-disabled/` to a harness-home .gitignore.
    Parks are central now, so offer to drop exactly those lines (never add any): only in
    a git work tree, and only once `migrate` has emptied the legacy dir a line covers."""
    ignore = h.home / ".gitignore"
    if not ignore.is_file() or fs.git_toplevel(h.home) is None:
        return
    # older versions wrote EVERY harness's park names into each home, so match them all
    legacy = {fs.legacy_park(Path(sub)).as_posix() + "/": fs.legacy_park(h.home / sub)
              for t in harnesses().values() for subs in t.dirs.values() for sub in subs
              if not Path(sub).is_absolute()}
    try:
        lines = ignore.read_bytes().decode("utf-8").splitlines(keepends=True)
    except (OSError, UnicodeDecodeError) as e:
        out.say(f"  gitignore  {ignore}: unreadable ({e}); stale park-dir lines not checked")
        return

    def report(status: str, lines_: list[str], what: str) -> None:
        msg = f"{ignore}: {what}"
        out.say(f"  gitignore  {msg}")
        out.row(h.name, None, None, "install-shims", status, msg, show=False,
                gitignore=str(ignore), stale=lines_)

    stale = list(dict.fromkeys(ln.rstrip("\r\n") for ln in lines if ln.rstrip("\r\n") in legacy))
    if busy := [s for s in stale if legacy[s].exists() or legacy[s].is_symlink()]:
        report("skipped", busy, f"{' '.join(busy)} still cover(s) a legacy park dir -- "
                                f"run `agent-toggle migrate` first, then re-run install-shims")
    if not (stale := [s for s in stale if s not in busy]):
        return
    shown = " ".join(stale)
    if ignore.is_symlink():          # an atomic replace would turn the link into a file
        return report("skipped", stale, f"stale {shown} (a symlink: remove them by hand)")
    if args.dry_run:
        return report("planned", stale, f"would remove stale {shown}")
    if not args.gitignore and not (args.prompt and _interactive(out)):
        return report("skipped", stale, f"stale {shown} (re-run with --gitignore to remove)")
    if not args.gitignore:
        try:
            yes = input(f"remove stale park-dir lines from {ignore}? [y/N] ").strip().lower()
        except EOFError:
            yes = ""
        if yes not in ("y", "yes"):
            return report("skipped", stale, f"kept stale {shown}")
    # every other line, ending included, is kept byte for byte
    keep = "".join(ln for ln in lines if ln.rstrip("\r\n") not in stale)
    fs.atomic_write(ignore, keep, mode=ignore.stat().st_mode & 0o777, newline="")
    report("ok", stale, f"removed stale {shown}")


COMMANDS = ("ui", "pick", "status", "list", "cost", "migrate", "disable", "enable",
            "install-shims", "profile", "undo", "doctor", "config")
_GLOBAL_FLAGS = ("--json", "-v", "--verbose")


def _interactive(out: Result) -> bool:
    """A y/n prompt can be answered: a terminal on both ends and no --json."""
    return not out.json_mode and sys.stdin.isatty() and sys.stdout.isatty()


def _command_index(argv: list[str]) -> int:
    """Index of the command word: the first argument after any leading global flags."""
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--harness", "--color"):
            i += 2
        elif a in _GLOBAL_FLAGS or a.startswith(("--harness=", "--color=")):
            i += 1
        else:
            break
    return i


def normalize_argv(argv: list[str]) -> list[str]:
    """Accept `--status` for `status` and bare `help` / `version` for `--help` / `--version`.

    Only the command position is rewritten (after any leading global flags), so a
    resource that happens to be named `list` or `help` is never touched. The topic
    after help is normalised too: `help --config` == `help config`.
    """
    argv = list(argv)
    i = _command_index(argv)
    if i < len(argv):
        a = argv[i]
        if a.startswith("--") and a[2:] in COMMANDS:
            argv[i] = a[2:]
        elif a in ("help", "version"):
            argv[i] = "--" + a
        j = i + 1 + _command_index(argv[i + 1:])      # the topic, after any global flags
        if argv[i] in ("--help", "-h") and j < len(argv) and argv[j][2:] in (*COMMANDS, "version", "help") \
                and argv[j].startswith("--"):
            argv[j] = argv[j][2:]
    return argv


def help_request(argv: list[str]) -> tuple[bool, str | None]:
    """(is this a help request, its command or None for the overview), from normalised argv.

    `--help [X]` / `-h [X]` in the command position, or `X ... --help|-h` after a command.
    """
    i = _command_index(argv)
    if i >= len(argv):
        return False, None
    if argv[i] in ("--help", "-h"):
        rest = argv[i + 1:]
        flags = ("--", "--help", "-h", "--harness", "--color", *_GLOBAL_FLAGS)
        words = [a for j, a in enumerate(rest)           # skip flags and their values; an
                 if a not in flags and not a.startswith(("--harness=", "--color="))   # unknown
                 and (j == 0 or rest[j - 1] not in ("--harness", "--color"))]         # --X is a topic
        topic = words[0] if words else None
        if topic and topic.startswith("--") and topic[2:] in (*COMMANDS, "version", "help"):
            topic = topic[2:]                       # `help -- --status` == `help status`
        return True, topic
    rest = argv[i + 1:]
    if "--" in rest:                                # `list -- --help` is an operand, not help
        rest = rest[:rest.index("--")]
    if argv[i] in COMMANDS and {"--help", "-h"} & set(rest):
        return True, argv[i]
    return False, None


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        die(message, 2)


PROJECT_HELP = ("project scope: <dir>/.claude (claude dir types; `.` = cwd) and <dir>/.mcp.json "
                "(mcp); parks under ~/.agent-toggle/parked, never inside the project")


def build_parser() -> argparse.ArgumentParser:
    # SUPPRESS: a flag given before the subcommand must survive the subparser's
    # own defaults, so neither level sets a default; main() fills them in.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--harness", choices=list(harnesses()), default=argparse.SUPPRESS,
                        help=f"target harness (default: {settings.get('default_harness')})")
    common.add_argument("--color", choices=COLOR_MODES, default=argparse.SUPPRESS,
                        help="colorize output (default: auto; honors NO_COLOR / FORCE_COLOR)")
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
    up = sub.add_parser("ui", aliases=["pick"], parents=[common], help="interactive picker")
    up.add_argument("--dry-run", action="store_true",
                    help="show the plan for what you stage; change nothing")
    up.add_argument("--project", metavar="dir", help=PROJECT_HELP)
    sub.add_parser("status", parents=[common], help="health check")
    ls = sub.add_parser("list", parents=[common], help="what is currently disabled")
    ls.add_argument("type", nargs="?", choices=TYPES)
    ls.add_argument("--project", metavar="dir", help="only this project's entries")
    cp = sub.add_parser("cost", parents=[common],
                        help="estimated startup tokens per item, biggest first")
    cp.add_argument("--type", choices=TYPES, help="only this resource type")
    cp.add_argument("--project", metavar="dir",
                    help="price this project's <dir>/.claude and <dir>/.mcp.json, not user scope")
    sub.add_parser("migrate", parents=[common],
                   help="import ~/.claude-toggle state; move legacy park dirs")
    sp = sub.add_parser("install-shims", parents=[common],
                        help="write the skill shim into every installed harness")
    sp.add_argument("--dry-run", action="store_true", help="show the plan; change nothing")
    gi = sp.add_mutually_exclusive_group()
    gi.add_argument("--gitignore", action="store_true",
                    help="remove stale legacy park-dir lines from harness .gitignore files "
                         "without asking")
    gi.add_argument("--no-gitignore", action="store_true",
                    help="skip the stale park-dir line check")
    sp.set_defaults(prompt=True)        # the config menu passes False: no y/n under curses
    pp = sub.add_parser("profile", parents=[common],
                        help="save / apply / diff / list named sets of live items")
    pp.add_argument("action", help="save | apply | diff | list")
    pp.add_argument("target", nargs="?", metavar="name|file")
    pp.add_argument("--out", metavar="file", help="save: write the profile here instead")
    pp.add_argument("--dry-run", action="store_true", help="apply: show the plan; change nothing")
    pp.add_argument("--project", metavar="dir", help=PROJECT_HELP)
    sub.add_parser("doctor", parents=[common],
                   help="check harness layouts and state against disk; offers y/n fixes")
    cf = sub.add_parser("config", parents=[common],
                        help="Telegram settings for the CI failure alerts (needs the telegram extra)")
    cf.add_argument("action", nargs="?", choices=("test", "sync-ci"),
                    help="test: send one message | sync-ci: set the GitHub repo secrets")
    cf.add_argument("--repo", metavar="OWNER/REPO", help="sync-ci: repository (default: this one)")
    cf.add_argument("--dry-run", action="store_true", help="sync-ci: show the plan; set nothing")
    up2 = sub.add_parser("undo", parents=[common], help="reverse the last logged batch")
    up2.add_argument("--dry-run", action="store_true", help="show the plan; change nothing")
    for name, verb in (("disable", "park"), ("enable", "restore")):
        sp = sub.add_parser(name, parents=[common], help=f"{verb} one or more items")
        sp.add_argument("type", choices=(*TYPES, "all"), nargs="?" if name == "enable" else None)
        sp.add_argument("names", nargs="*" if name == "enable" else "+", metavar="name")
        sp.add_argument("--dry-run", action="store_true",
                        help="show the plan; change nothing")
        sp.add_argument("--project", metavar="dir", help=PROJECT_HELP)
        if name == "enable":
            sp.add_argument("--all", action="store_true",
                            help="restore every disabled item (optionally --harness H)")
    return p


def main(argv: list[str] | None = None) -> int:
    """Run one command and return its exit code (the process exit code).

    Settings apply first; the update check starts before dispatch and is offered in a
    `finally`, so every way out (a command, --help/--version, a usage error, Locked,
    CliError) gets it. It writes to stderr only and never changes the exit code."""
    argv = normalize_argv(sys.argv[1:] if argv is None else argv)
    try:
        language, color = settings.get("language"), settings.get("color")
    except Exception:  # noqa: BLE001 - settings must never crash the CLI before it starts
        language, color = "en", "auto"
    i18n.set_language(language)
    color = scan_color(argv, color)     # --color > env > setting > auto
    started = update_prompt.start()
    try:
        return _main(argv, color)
    except KeyboardInterrupt:
        started = None                  # never prompt after the user hit Ctrl-C
        raise
    finally:
        with contextlib.suppress(KeyboardInterrupt):     # Ctrl-C while waiting on the check
            update_prompt.offer(started, json_mode="--json" in argv, color_mode=color)


def _main(argv: list[str], color: str) -> int:
    out = Result(next((a for a in argv if a in COMMANDS), ""), "--json" in argv, color)
    try:
        # no command: picker on a TTY, else help with exit 2; --json keeps the usage error
        bare = _command_index(argv) >= len(argv) and "--json" not in argv
        if bare:
            tty = sys.stdin.isatty() and sys.stdout.isatty()
            argv = [*argv, "ui" if tty else "--help"]
            out.command = "ui" if tty else ""
            bare = not tty
        wants_help, topic = help_request(argv)
        if wants_help:
            topic = None if topic == "help" else topic
            if topic is not None and topic not in (*COMMANDS, "version"):
                die(f"no such command: {topic} (see `agent-toggle help`)", 2)
            on = use_color(sys.stdout, color)
            default = settings.get("default_harness")
            text = (helptext.version_help(on) if topic == "version"
                    else helptext.command_help(topic, on, default) if topic
                    else helptext.top_help(on, default))
            if topic is None:
                logo.banner(sys.stdout, settings.get("logo"), on,
                            shutil.get_terminal_size().columns)
            print(ui_theme.encodable(text, sys.stdout))     # an ASCII stdout must not crash
            return 2 if bare else 0
        args = build_parser().parse_args(argv)
        out.command = "ui" if args.command == "pick" else args.command
        out.json_mode = getattr(args, "json", False)
        args.harness = getattr(args, "harness", None)
        # harness.<name>=false hides a harness from the overviews; --harness still reaches it
        hidden = frozenset() if args.harness else \
            frozenset(settings.HARNESS_NAMES) - frozenset(settings.enabled_harnesses())
        cmd = out.command
        if cmd == "ui":
            if out.json_mode:
                die("ui is interactive; --json is not supported", 2)
            cmd_ui(load_state(write_back=False), out, args.dry_run, args.project, args.harness)
        elif cmd == "cost":
            cmd_cost(load_state(write_back=False), out, args.harness, args.type, args.project,
                     hidden)
        elif cmd == "status":
            cmd_status(load_state(write_back=False), out, args.harness, hidden)
        elif cmd == "list":
            cmd_list(args.type, load_state(write_back=False), out, args.harness, args.project,
                     hidden)
        elif cmd == "migrate":
            cmd_migrate(out)
        elif cmd == "install-shims":
            cmd_install_shims(args, out)
            if not args.dry_run and _interactive(out):
                doctor.offer_quietly(out, args.harness)
        elif cmd == "profile":
            profiles.cmd_profile(args, out)
        elif cmd == "doctor":
            fixes = doctor.cmd_doctor(args.harness, out)
            if fixes and _interactive(out):
                doctor.offer_fixes(fixes, out)
        elif cmd == "config":
            config.cmd_config(args, out)
        elif cmd == "undo":
            undo.cmd_undo(args.dry_run, args.harness, out)
        elif cmd == "enable" and args.all:
            if args.type or args.names:
                die("enable --all takes no <type> or <name>", 2)
            undo.cmd_enable_all(args.harness, args.dry_run, out, args.project)
        else:
            # --project is claude-layout only, so it keeps claude as its default
            if not args.harness and args.project is None:
                args.harness = settings.get("default_harness")
                if args.harness not in settings.enabled_harnesses():
                    print(f"agent-toggle: default harness '{args.harness}' is turned off in "
                          "settings (agent-toggle config)", file=sys.stderr)
            args.harness = args.harness or "claude"     # --project is claude-layout only
            cmd_toggle(args, out)
        rc = out.exit_code()
    except fs.Locked as e:
        out.error(str(e))
        rc = 3
    except CliError as e:
        out.error(e.msg)
        rc = e.code
    except SystemExit as e:             # argparse --version (help never reaches argparse)
        return e.code or 0
    except Exception as e:              # never a bare traceback; --json stays one document
        out.error(f"{type(e).__name__}: {e}")
        if os.environ.get("AGENT_TOGGLE_DEBUG") == "1" or {"-v", "--verbose"} & set(argv):
            traceback.print_exc()
        rc = 1
    out.render()
    return rc
