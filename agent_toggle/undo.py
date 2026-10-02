"""`undo` (reverse the last logged batch) and `enable --all` (restore everything).

Both build a plan of ops.Op and hand it to ops.apply_plan, the one apply path,
so locking, state saving, refusal checks and logging are shared with disable/enable.
"""
from __future__ import annotations

import json

from . import fs, ops, store
from .harnesses import TYPES, harnesses
from .mechanisms import fail_row, validate_name
from .output import CliError, Result, die

REVERSE = {"disable": "enable", "enable": "disable"}


def _log_rows() -> list[dict]:
    p = fs.log_file()
    if not p.is_file():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _toggled(row: dict) -> bool:
    return row.get("result") == "ok" and row.get("action") in REVERSE


def _runnable(plan: list[ops.Op], table: dict, out: Result, dry_run: bool, batch: str) -> list[ops.Op]:
    """Drop (as error rows) ops whose harness is unknown or whose home is gone."""
    keep = []
    for op in plan:
        h = table.get(op.harness)
        why = (f"unknown harness {op.harness!r}" if h is None
               else f"{op.harness} is not installed ({h.home} does not exist)"
               if not h.home.is_dir() else None)
        if why:
            fail_row(out, dry_run, op.harness, op.type, op.action, op.name, why, batch=batch)
        else:
            keep.append(op)
    return keep


def cmd_undo(dry_run: bool, harness: str | None, out: Result) -> None:
    if harness:
        die("undo reverses a whole batch; --harness does not apply", 2)
    rows = _log_rows()
    last = next((r for r in reversed(rows) if _toggled(r)), None)
    if last is None:
        out.say("nothing to undo")
        return
    target = last.get("batch")
    if not target:
        die("nothing to undo: log predates undo (its rows carry no batch id)")
    if not isinstance(target, str):
        die("nothing to undo: corrupt log row (batch is not a string)")
    batch_rows = [r for r in rows if _toggled(r) and r.get("batch") == target]
    if any(not isinstance(r.get("harness"), str) or not r["harness"] for r in batch_rows):
        die("nothing to undo: log predates undo (a row of the last batch has no harness)")
    for r in batch_rows:                   # the log is a file anyone can edit: validate like argv
        try:
            if r.get("type") not in TYPES or not isinstance(r.get("name"), str):
                raise CliError("bad type or name")
            validate_name(r["name"])
        except CliError:
            die(f"nothing to undo: corrupt log row for {r.get('type')!r} {r.get('name')!r}")
    if any(r.get("scope") == "project" for r in batch_rows):
        # claude local-scope mcp rows log scope=user, project=null: user scope. Only
        # scope=project is a --project change, and it must never replay into user scope.
        # Refuse the whole batch: a partial undo would bury the project rows.
        die("nothing undone: the last batch holds project-scope changes "
            "(undo with --project is not supported yet)", 2)
    seen = {r.get("batch") for r in rows}
    batch = store.BATCH
    if batch in seen:                 # only when several runs share one process (tests)
        batch = f"{batch}-{len(seen)}"
    table, plan = harnesses(), []
    out.say(f"undoing batch {target} ({len(batch_rows)} change(s))")
    for r in reversed(batch_rows):
        plan.append(ops.Op(r["harness"], r["type"], REVERSE[r["action"]], r["name"]))
    plan = _runnable(plan, table, out, dry_run, batch)
    if plan:
        ops.apply_plan(plan, out, dry_run, batch=batch, headers=True)


def cmd_enable_all(harness: str | None, dry_run: bool, out: Result) -> None:
    """Restore every user-scope state entry (optionally only one harness's)."""
    table, plan, bad = harnesses(), [], []
    for key, entry in store.load_state(write_back=False)["disabled"].items():
        try:
            h, digest, type_, name = store.parse_key(key)
            if digest:                    # a project-scope entry belongs to --project
                continue
            if type_ not in TYPES:
                raise ValueError(f"unknown type {type_!r}")
            validate_name(name)
        except (ValueError, CliError) as e:
            bad.append((key, str(getattr(e, "msg", e))))
            continue
        shared = entry.get("shared_with") if isinstance(entry, dict) else None
        if harness and harness != h and harness not in (shared if isinstance(shared, list) else ()):
            continue
        plan.append(ops.Op(h, type_, "enable", name))
    for key, why in bad:
        out.row(None, None, key, "enable", "error", f"unusable state key: {why}")
    plan = _runnable(plan, table, out, dry_run, store.BATCH)
    if not plan and not bad:
        out.say("nothing disabled")
        return
    if plan:
        ops.apply_plan(plan, out, dry_run, batch=store.BATCH, headers=True)
