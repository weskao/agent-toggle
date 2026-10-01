"""User-facing error output."""
from __future__ import annotations

import sys


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(1)
