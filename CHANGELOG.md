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

### Changed

- A dry run whose plan contains a failing item exits `1`, not `0`.
- Usage errors, an unknown harness and an unknown type now exit `2` (were `1`).
- The root `agent_toggle.py` is now a thin wrapper over the `agent_toggle/`
  package; `install.sh` is a thin wrapper over `install-shims`.
- Install flow is `uv tool install git+<repo-url>` or `pip install -e .`
  instead of a user-specific script path.
- MCP backups are written with mode `0600`.

### Fixed

- Names with `..`, an absolute path, an empty part or a leading `-` are refused
  (exit 2), and an item whose parent resolves outside the harness dir is never
  touched, so `disable skill ../../x` can no longer move files outside it.
- Restoring a codex MCP server no longer doubles the blank line before it.
