# agent-toggle project instructions

## Every command has two spellings

Each command is accepted both with and without a leading `--`, and both forms
behave identically (same output, same exit code):

| command | also accepted as |
|---|---|
| `help` | `--help` |
| `version` | `--version` |
| `status` | `--status` |
| `list` | `--list` |
| `cost` | `--cost` |
| `ui` (alias `pick`) | `--ui`, `--pick` |
| `migrate` | `--migrate` |
| `install-shims` | `--install-shims` |
| `disable`, `enable` | `--disable`, `--enable` |
| `profile` | `--profile` |
| `undo` | `--undo` |
| `doctor` | `--doctor` |

Rules:
- Normalise once, in `main()` in `agent_toggle/cli.py`, before argparse runs:
  if the first argument is `--<command>` or bare `help` / `version`, rewrite it
  to the canonical form. Do not add per-command aliases.
- Applies to commands only. Option flags (`--json`, `--dry-run`, `--harness`,
  `--project`, `--all`, `-v`) keep the `--` form: a bare `json` or `dry-run` would collide with a
  resource name such as `disable skill json`.
- Adding a command means adding it to the table above and to the test in
  `tests/test_cli_surface.py` that runs every command in both spellings and
  compares output and exit code.
- `README.md` and the shims document the plain form; the `--` form is a
  convenience, not a second documented interface.
