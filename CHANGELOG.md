# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Phase 0 (public readiness): `LICENSE` (MIT), `SECURITY.md`,
  `CONTRIBUTING.md`, this changelog, and GitHub issue templates.
- Phase 0: `pyproject.toml` with an `agent-toggle` console script and zero
  runtime dependencies; `agent-toggle install-shims` subcommand; CI matrix
  (macOS, Linux, Windows) with `ruff` linting.
- Phase 1: package split, `--json` output, a state lock and stable exit
  codes, a `cost` command with a picker column, the `rule` resource type,
  grok MCP via the TOML backend, an OpenCode adapter with alias detection,
  plugins in the picker, and `--dry-run`.

### Changed

- Install flow is `uv tool install git+<repo-url>` instead of a
  user-specific script path.
- MCP backups are written with mode `0600`.
