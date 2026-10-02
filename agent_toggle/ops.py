"""The one apply path: every command that changes things hands a plan to apply_plan.

cmd_toggle, the picker (cmd_ui) and later profile apply / undo / enable --all
all build a list of Op and call apply_plan, which routes each item by the
harness MECHANISM table (Harness.mechanisms[type]), never by hard-coded types.
"""
from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from . import fs
from .harnesses import harnesses, project_view
from .mechanisms import (
    fail_row,
    toggle_dir_type,
    toggle_json_mcp,
    toggle_mcp,
    toggle_plugin,
    toggle_project_mcp,
)
from .output import CliError, Result
from .store import BATCH, load_state, save_state


class Op(NamedTuple):
    harness: str
    type: str
    action: str           # "disable" | "enable"
    name: str
    project: str | None = None   # --project dir: project scope, never user scope


def _dispatch(plan: list[Op], state: dict, out: Result, dry_run: bool, batch: str,
              headers: bool) -> int:
    """Run the plan, grouped per harness/type/action in first-seen order."""
    groups: dict[tuple[str, str, str, str | None], list[str]] = {}
    for op in plan:
        groups.setdefault((op.harness, op.type, op.action, op.project), []).append(op.name)
    user = harnesses()
    fails = 0
    for (harness, type_, action, project), names in groups.items():
        table = user
        if project is not None:
            # project scope: only the claude dir types, through a project-only table,
            # so no user-scope dir or entry can be reached (DESIGN s5.5)
            try:
                if harness != "claude":
                    raise CliError(f"{harness} has no --project scope", 4)
                table = {"claude": project_view(Path(project))}
            except CliError as e:
                for name in names:
                    fails += fail_row(out, dry_run, harness, type_, action, name, e.msg,
                                      batch=batch, project=project)
                continue
        h = table[harness]
        mech = h.mechanisms.get(type_)
        if headers:
            out.say(f"\n{action} {type_} on {out.paint(harness, 'cyan')}"
                    + (f" (project {h.project}):" if h.project else ":"))
        if mech == "move":
            fails += toggle_dir_type(action, type_, names, state, harness, h.home, out,
                                     dry_run, batch=batch, table=table)
        elif mech == "native_cli" or (mech == "flag" and type_ == "plugin"):
            fails += toggle_plugin(action, names, state, harness, out, dry_run, batch=batch)
        elif mech == "remove_backup" and h.project:      # the repo's own .mcp.json
            fails += toggle_project_mcp(action, names, state, harness, out, dry_run, table,
                                        batch=batch)
        elif mech == "remove_backup" and h.backend == "json":    # user-scope strict JSON file
            fails += toggle_json_mcp(action, names, state, harness, out, dry_run, table,
                                     batch=batch)
        elif mech == "remove_backup" or (mech == "flag" and type_ == "mcp"):
            fails += toggle_mcp(action, names, state, harness, h.home, h.backend, out,
                                dry_run, batch=batch)
        else:
            for name in names:
                fails += fail_row(out, dry_run, harness, type_, action, name,
                               f"{harness} has no {type_} support"
                               + (" in project scope" if h.project else ""), "unsupported",
                               batch=batch, project=project)
    return fails


def apply_plan(plan: list[Op], out: Result, dry_run: bool = False, *,
               batch: str = BATCH, state: dict | None = None,
               headers: bool = False) -> int:
    """Apply `plan`; returns the fail count.

    A real run takes ONE fs.lock() for the whole plan, re-reads state under it
    and saves it in `finally`, so what already moved is kept even if a later
    item crashes. A dry run plans against a read-only state: no lock, state,
    log or backup write. A caller that already holds the lock passes `state`.
    """
    if state is not None:
        return _dispatch(plan, state, out, dry_run, batch, headers)
    if dry_run:
        return _dispatch(plan, load_state(write_back=False), out, True, batch, headers)
    with fs.lock():
        state = load_state()
        try:
            return _dispatch(plan, state, out, False, batch, headers)
        finally:
            save_state(state)
