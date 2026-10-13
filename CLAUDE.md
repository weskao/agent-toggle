# agent-toggle project instructions

## Every command has two spellings

Each command is accepted both with and without a leading `--`, and both forms
behave identically (same output, same exit code):

| command | also accepted as |
|---|---|
| `help [command]` | `--help [command]` |
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
| `config` | `--config` |
| `update` | `--update` |
| `completion <shell>` | `--completion <shell>` |

Rules:
- Normalise once, in `main()` in `agent_toggle/cli.py`, before argparse runs:
  if the first argument is `--<command>` or bare `help` / `version`, rewrite it
  to the canonical form. Do not add per-command aliases.
- `help` and `--help` take an optional command topic, and all three spellings
  render the same text: `help config` == `--help config` == `config --help`.
  `help <unknown>` exits 2.
- Applies to commands only. Option flags (`--json`, `--dry-run`, `--harness`,
  `--project`, `--all`, `-v`) keep the `--` form: a bare `json` or `dry-run` would collide with a
  resource name such as `disable skill json`.
- Adding a command means adding it to the table above and to the test in
  `tests/test_cli_surface.py` that runs every command in both spellings and
  compares output and exit code.
- `README.md` and the shims document the plain form; the `--` form is a
  convenience, not a second documented interface.

## Cross-platform

Code must run on macOS, Linux, and Windows (paths, separators, shell commands, line
endings, `$HOME` vs `%USERPROFILE%`, file locking, symlinks, terminal/ANSI). Guard
platform-specific code and say so.

- Reuse the shared platform helpers first: `fs.WIN`, `fs.replace`, `output._vt_enable`, `ui.supports_unicode`.
- Text I/O always passes `encoding="utf-8"` (Windows defaults to cp1252).
- Windows is verified by CI's `windows-latest` job, not locally; POSIX-only tests are
  skipped on Windows (`os.name == "nt"`).

## Hidden features

- Telegram rows in the `config` menu are hidden, not removed (`SHOW_TELEGRAM = False` in `agent_toggle/ui/config_menu.py`, commit `409ff0a`); restore with `SHOW_TELEGRAM = True` or `git revert 409ff0a`.

## Keeping `docs/toggle-paths.md` in sync

`docs/toggle-paths.md` documents what `disable` / `enable` change on disk for each type, with
example live, parked and backup paths. Update it in the same commit as any change to:

- park, companion or backup locations: `user_park`, `parked_dir`, `companion_dir`, `backup_dir`
  in `agent_toggle/fs.py`, and the backup file names in `agent_toggle/mechanisms.py`
  (`<harness>__<name>.json`, `<sha8>__<harness>__<name>.json`);
- how a type is toggled: the move / native CLI / flag / remove-with-backup paths in
  `agent_toggle/mechanisms.py`, `agent_toggle/companions.py` and `agent_toggle/backends/`;
- a harness row's `dirs`, `mechanisms`, `flags` or `mcp` in `agent_toggle/harnesses.py` (also
  update `docs/harnesses.md`);
- the state key (`<harness>:<type>:<name>`) or the project park layout (`parked/<sha8>/<sub>-disabled`).

Keep the examples synthetic (`demo-skill`, `example-mcp`). After changing a move path,
check the doc's example paths with a real disable/enable round trip in a temp `HOME`.
