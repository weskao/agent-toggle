# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `cost` command: estimated startup tokens per item (chars / 4, about +-25 %),
  biggest first, with a cost column in the picker; `cost --json` carries the
  formula and a total row.
- `rule` resource type (claude `rules/*.md`, parked in `rules-disabled/`).
- OpenCode adapter (XDG home; skills and commands) with alias detection: a
  directory shared by several harnesses is one item, and rows report
  `shared_with`. `status` warns about `.synced-from-*` markers.
- Grok MCP through the existing TOML backend (`~/.grok/config.toml`,
  confirmed on a live install; `.headers` sub-tables are backed up verbatim) and codex `prompts/` as a second command directory.
- Plugins in the picker; picker sort (`s`), harness and type filters (`h`, `t`),
  key help (`?`), and `/` to type a filter.
- `install-shims` subcommand (replaces the shell logic of `install.sh`).
- `--json` envelope on every command, `--dry-run` for `disable`, `enable`,
  `install-shims` and `ui`, `--version`, `-v` / `--verbose`, and stable exit
  codes (0 ok, 1 partial, 2 usage, 3 locked, 4 unsupported).
- State hardening: `0600` state, log and MCP backups (`0700` directory), a lock
  around every mutating batch, and state schema v3 with a v2 migration.
- Packaging and CI: `pyproject.toml` with an `agent-toggle` console script and
  no runtime dependencies; GitHub Actions matrix (macOS, Linux, Windows;
  Python 3.10, 3.13) plus `ruff`.
- Open-source docs: `LICENSE` (MIT), `SECURITY.md`, `CONTRIBUTING.md`, this
  changelog, GitHub issue templates, and `docs/DESIGN.md`.
- `profile save|apply|diff|list`: portable snapshots of which items are live
  (`{harness, type, name, live}` only, no paths or secrets), stored under
  `~/.agent-toggle/profiles/` or any `.json` path. `apply` toggles only the items
  the profile mentions and skips unknown ones; `--dry-run` / `diff` plan without
  writing.
- `undo`: reverses the last logged batch (itself a batch, so undo of undo works);
  `enable --all [--harness H] [--project <dir>]` restores everything.
- `doctor [--harness H] [--json]`: read-only drift and state check
  (`ok` / `absent` / `note` / `unverified` / `warn` / `error`; exit `1` only on `error`).
- `--project <dir>`: project scope for a repo's `.claude/` dir types and its own
  `.mcp.json` MCP servers (strict JSON). Dir items park under
  `~/.agent-toggle/parked/<sha8>` and `.mcp.json` entries are backed up under
  `mcp-backups/`, never inside the project, with a git
  tracked-deletion warning and the restore command. Claude layout only.
- Flag mechanism: openclaw skills/plugins (`skills.entries.<name>.enabled`,
  `plugins.entries.<name>.enabled`) and opencode mcp (`mcp.<name>.enabled`), a
  one-token boolean edit that leaves every other byte alone. Both shapes are
  assumed from the design survey and not verified on a real install; JSONC /
  JSON5 files are refused.
- Post-write verification with rollback of bytes and mode for JSON and TOML config
  edits; an invalid codex `config.toml` fails the edit instead of being rewritten.
- Tampered-state refusal: `enable` refuses entries whose paths leave their
  harness home, project or `~/.agent-toggle` (`refused: <reason>`).
- Log rows carry `harness`, `batch`, `project` and `scope`; `agent_toggle/ops.py`
  is the single apply path for `disable`, `enable`, the picker, `profile apply`,
  `undo` and `enable --all`.

### Changed

- A dry run whose plan contains a failing item exits `1`, not `0`.
- Usage errors, an unknown harness and an unknown type now exit `2` (were `1`).
- The root `agent_toggle.py` is now a thin wrapper over the `agent_toggle/`
  package; `install.sh` is a thin wrapper over `install-shims`.
- Install flow is `uv tool install git+<repo-url>` or `pip install -e .`
  instead of a user-specific script path.
- MCP backups are written with mode `0600`.
- `enable` refuses state entries whose `origin`, `parked_at`, backup or flag
  file is outside its root, instead of replaying them.
- `log.jsonl` rows now follow one schema: `{ts, harness, type, name, action,
  result, batch, project, scope, detail}`; rows written by older versions have
  no `batch` and cannot be undone.
- `enable` takes `--all` in place of `<type> <name>...`.

### Fixed

- Names with `..`, an absolute path, an empty part or a leading `-` are refused
  (exit 2), and an item whose parent resolves outside the harness dir is never
  touched, so `disable skill ../../x` can no longer move files outside it.
- Restoring a codex MCP server no longer doubles the blank line before it.
