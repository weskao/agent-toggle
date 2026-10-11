#!/usr/bin/env python3
"""Write every shell's completion file (agent_toggle.completions) for the release artifact.

    python3 tools/gen_completions.py [outdir]     # default: completions/
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_toggle.cli import build_parser  # noqa: E402
from agent_toggle.completions import generate  # noqa: E402


def main(argv: list[str]) -> int:
    out = Path(argv[0] if argv else "completions")
    out.mkdir(parents=True, exist_ok=True)
    for name, text in generate(build_parser()).items():
        (out / name).write_text(text, encoding="utf-8")
        print(out / name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
