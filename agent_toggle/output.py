"""Command output: one collector that renders as human text OR one JSON document.

Every command builds a `Result`. In human mode lines stream to stdout as they
happen (diagnostics to stderr); in --json mode nothing is printed until
`render()` emits the single envelope, so text can never leak into stdout.
"""
from __future__ import annotations

import json
import sys

FAILED = ("error", "unsupported")      # row statuses that make the run a failure
MARK = {"ok": "v", "planned": "~"}      # human marker per status; anything else is "x"


class CliError(SystemExit):
    """A fatal, user-facing error. `code` is the process exit code (a SystemExit,
    so legacy `except SystemExit` callers keep working); `msg` is for the human."""

    def __init__(self, msg: str, code: int = 1) -> None:
        super().__init__(code)
        self.msg = msg


def die(msg: str, code: int = 1) -> None:
    raise CliError(msg, code)


class Result:
    def __init__(self, command: str = "", json_mode: bool = False) -> None:
        self.command = command
        self.json_mode = json_mode
        self.rows: list[dict] = []
        self.warnings: list[str] = []

    def say(self, msg: str, warn: bool = False) -> None:
        """A human line on stdout. warn=True also records it in the envelope."""
        if warn:
            self.warnings.append(msg.strip())
        if not self.json_mode:
            print(msg)

    def warn(self, msg: str) -> None:
        """A warning: stderr for humans, `warnings` for --json."""
        self.warnings.append(msg)
        if not self.json_mode:
            print(f"WARNING  {msg}", file=sys.stderr)

    def row(self, harness: str | None, type_: str | None, name: str | None,
            action: str, status: str, text: str = "", show: bool = True,
            **extra) -> dict:
        """Record one result; `show=False` records it without a human line."""
        row = {"harness": harness, "type": type_, "name": name,
               "action": action, "status": status, "detail": text, **extra}
        self.rows.append(row)
        if show and not self.json_mode:
            if status in MARK:
                print(f"  {MARK[status]} {type_} {name} {text}")
            else:
                print(f"  x {type_} {name}: {text}")
        return row

    def error(self, msg: str) -> None:
        self.rows.append({"harness": None, "type": None, "name": None,
                          "action": self.command, "status": "error", "detail": msg})
        if not self.json_mode:
            print(f"error: {msg}", file=sys.stderr)

    @property
    def failed(self) -> int:
        return sum(r["status"] in FAILED for r in self.rows)

    @property
    def needs_new_session(self) -> bool:
        return any(r["status"] == "ok" and r["action"] in ("disable", "enable")
                   for r in self.rows)

    def exit_code(self) -> int:
        """0 ok; 4 when every failure was an unsupported pair; else 1 (partial)."""
        bad = [r for r in self.rows if r["status"] in FAILED]
        if not bad:
            return 0
        return 4 if all(r["status"] == "unsupported" for r in bad) else 1

    def envelope(self) -> dict:
        return {"ok": self.failed == 0, "command": self.command, "results": self.rows,
                "warnings": self.warnings, "needs_new_session": self.needs_new_session}

    def render(self) -> None:
        if self.json_mode:
            print(json.dumps(self.envelope(), indent=2, ensure_ascii=False))
