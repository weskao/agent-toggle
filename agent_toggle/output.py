"""Command output: one collector that renders as human text OR one JSON document.

Every command builds a `Result`. In human mode lines stream to stdout as they
happen (diagnostics to stderr); in --json mode nothing is printed until
`render()` emits the single envelope, so text can never leak into stdout.
"""
from __future__ import annotations

import json
import os
import sys

FAILED = ("error", "unsupported")      # row statuses that make the run a failure
MARK = {"ok": "v", "planned": "~", "skipped": "-"}   # human marker per status; else "x"


STYLE = {"green": "32", "red": "31", "yellow": "33", "cyan": "36", "dim": "2"}
COLOR_MODES = ("auto", "always", "never")


def _vt_enable(stream) -> bool:
    """Windows 10+: make sure the console is in ANSI mode. True iff it is (TTY only)."""
    try:
        import ctypes
        import msvcrt
        k32 = ctypes.windll.kernel32                       # type: ignore[attr-defined]
        h = ctypes.c_void_p(msvcrt.get_osfhandle(stream.fileno()))
        mode = ctypes.c_uint32()
        if not k32.GetConsoleMode(h, ctypes.byref(mode)):
            return False
        return bool(mode.value & 0x0004) or bool(k32.SetConsoleMode(h, mode.value | 0x0004))
    except Exception:                                      # no console / old Windows: plain text
        return False


def use_color(stream, mode: str = "auto") -> bool:
    """Decide for ONE stream, at write time -- nothing is cached between calls.
    --color always|never wins; then NO_COLOR, FORCE_COLOR, TERM=dumb, then isatty.
    A Windows console that cannot do ANSI stays plain, even for --color always."""
    if mode == "never":
        return False
    env = os.environ
    try:
        tty = bool(stream.isatty())
    except Exception:
        tty = False
    if mode != "always":
        if env.get("NO_COLOR"):
            return False
        forced = env.get("FORCE_COLOR", "0") not in ("", "0")    # beats TERM=dumb and non-tty
        if not forced and (env.get("TERM") == "dumb" or not tty):
            return False
    return not (tty and os.name == "nt" and not _vt_enable(stream))


def scan_color(argv: list[str]) -> str:
    """The --color value from raw argv (so even argparse errors honor it); bad -> auto."""
    mode = "auto"
    for i, a in enumerate(argv):
        if a == "--color" and i + 1 < len(argv):
            mode = argv[i + 1]
        elif a.startswith("--color="):
            mode = a[8:]
    return mode if mode in COLOR_MODES else "auto"


class CliError(SystemExit):
    """A fatal, user-facing error. `code` is the process exit code (a SystemExit,
    so legacy `except SystemExit` callers keep working); `msg` is for the human."""

    def __init__(self, msg: str, code: int = 1) -> None:
        super().__init__(code)
        self.msg = msg


def die(msg: str, code: int = 1) -> None:
    raise CliError(msg, code)


class Result:
    def __init__(self, command: str = "", json_mode: bool = False,
                 color: str = "auto") -> None:
        self.command = command
        self.json_mode = json_mode
        self.color = color
        self.rows: list[dict] = []
        self.warnings: list[str] = []

    def paint(self, text: str, style: str, stream=None) -> str:
        """`text` in `style` if `stream` (default stdout) takes color; never in --json.
        Apply AFTER width padding so uncolored output stays byte-identical."""
        if self.json_mode or not text or not use_color(stream or sys.stdout, self.color):
            return text
        return f"\033[{STYLE[style]}m{text}\033[0m"

    def say(self, msg: str, warn: bool = False, style: str | None = None) -> None:
        """A human line on stdout. warn=True also records it in the envelope."""
        if warn:
            self.warnings.append(msg.strip())
        if not self.json_mode:
            style = style or ("yellow" if warn else None)
            print(self.paint(msg, style) if style else msg)

    def warn(self, msg: str) -> None:
        """A warning: stderr for humans, `warnings` for --json."""
        self.warnings.append(msg)
        if not self.json_mode:
            print(f"{self.paint('WARNING', 'yellow', sys.stderr)}  {msg}", file=sys.stderr)

    def row(self, harness: str | None, type_: str | None, name: str | None,
            action: str, status: str, text: str = "", show: bool = True,
            **extra) -> dict:
        """Record one result; `show=False` records it without a human line."""
        row = {"harness": harness, "type": type_, "name": name,
               "action": action, "status": status, "detail": text, **extra}
        self.rows.append(row)
        if show and not self.json_mode:
            if status in MARK:
                mark = self.paint(MARK[status], "green" if status == "ok" else "dim")
                print(f"  {mark} {type_} {name} {text}")
            else:
                print(self.paint(f"  x {type_} {name}: {text}", "red"))
        return row

    def error(self, msg: str) -> None:
        self.rows.append({"harness": None, "type": None, "name": None,
                          "action": self.command, "status": "error", "detail": msg})
        if not self.json_mode:
            print(f"{self.paint('error:', 'red', sys.stderr)} {msg}", file=sys.stderr)

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
