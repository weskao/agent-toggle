# agent-toggle project instructions

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
