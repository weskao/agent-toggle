---
paths:
  - agent_toggle/cli.py
  - agent_toggle/help.py
  - tests/test_cli_surface.py
---
# Every command has two spellings

Each name in `cli.COMMANDS` (plus bare `help` / `version`) is also accepted as `--<name>`,
with identical output and exit code.

- Normalise once in `main()` before argparse: rewrite `--<command>` / bare `help` / `version`
  to the canonical form. No per-command aliases.
- `help <topic>` == `--help <topic>` == `<topic> --help`; `help <unknown>` exits 2.
- Commands only. Option flags (`--json`, `--dry-run`, `--harness`, `--project`, `--all`, `-v`)
  keep `--`: a bare `json` would collide with a resource name (`disable skill json`).
- Adding a command = adding it to `cli.COMMANDS`; `tests/test_cli_surface.py` then runs it in
  both spellings.
- `README.md` and the shims document the plain form only; `--` is a convenience.
