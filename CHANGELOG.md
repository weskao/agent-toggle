## [Unreleased]

### 🚀 Features

- **opencode:** Skill dirs follow OpenCode 2.0.22: `skills.paths` is additive and read from `opencode.json` or `opencode.jsonc` (JSONC, bare-list form, `~` / `$HOME/` / `${HOME}/` expanded), plus `skill/`, `~/.claude/skills` and `~/.agents/skills`; relative entries (session-cwd based) are skipped
- **flag-json:** Rewrite JSONC configs in place (comments and trailing commas kept); JSON5 is still refused
- **fs:** Parse-check TOML edits on Python 3.10 with a stdlib structural validator; `doctor` no longer reports codex/grok `config.toml` as `unverified` there

### 🐛 Bug Fixes

- **plugin-cli:** `run_cli` takes a per-call timeout: 30 s for read-only `plugin list`, 120 s for every other call; `AGENT_TOGGLE_CLI_TIMEOUT` (seconds, > 0) overrides both, an invalid value is ignored with a warning, and the timeout error names the variable
- **cost:** A plugin parked as `name` no longer shows twice next to the `name@marketplace` row `claude plugin list` reports; and `profile save --project` lists the live servers of a project that has only `.mcp.json` (no `.claude/`)
- **doctor:** Check companion files: a recorded companion whose parked file is missing or that is also live is an `error`, a parked companion with no state entry is a `warn`, each with a fix
- **ops:** A killed run is recoverable: state is saved per item, with a write-ahead `pending` entry (the flag's previous value included) before each flag write or dir move that the next run finishes or rolls back, and `status` / `doctor` report with the fix; a cross-filesystem project park copies into a temp sibling and renames it into place, and `enable` prunes the emptied `parked/<sha8>` dirs
- **mcp-json:** A project `.mcp.json` backup holds only the toggled server's entry (its value, its exact text and its neighbours' names), no longer the whole file text with other servers' auth headers; `disable` cuts only that entry's bytes, `enable` re-inserts them into the current file (byte for byte when unchanged) and rolls back unless only that entry was added, both under a write-ahead `pending` entry. Old whole-file backups still restore as before and are not rewritten; disabling the server again replaces one
- **install-shims:** Write the OpenCode shim into its first `skills.paths` dir when one is set (falling back to `skills/`); never overwrite the shim of another installed harness whose dir it redirects onto (skipped, "belongs to <owner>")

## [0.2.0] - 2026-10-03

### 🚀 Features

- **platform:** Numbered-menu fallback, plugin id validation, replace retry
- **config:** Telegram settings for CI alerts via telegram-kit (optional extra)
- **completions:** Generate bash/zsh/fish completions at release; pin README git installs to a tag
- **install-shims:** Per-harness shim templates; refuse to overwrite files without the shim marker
- **ops:** Warn that rules may carry safety constraints when disabling one
- **status:** Report state entries whose parked item is gone
- **doctor:** Classify orphan parked items with actionable fix

### 🐛 Bug Fixes

- **install-shims:** Recognise shims written before the marker so upgrades are not refused
- **mcp:** Mask backed-up config values in claude CLI error output
- **status:** Skip dotfiles in park dirs

### 📚 Documentation

- Mark phases 3.5 and 4 complete after v0.1.0
- Numbered menu, harness homes per OS, plugin id rule
- README says the alert covers the test matrix; refresh stale Phase 6 TODO prose
- Record the config command as done in the Phase 6 checklist
- **security:** Scope statement and supported versions
- **harnesses:** Add harness survey with verified-against versions
- **readme:** Link the harness matrix to docs/harnesses.md
- Tick the security, docs and housekeeping items done on 2026-10-03
- Windows CI now runs the symlink tests; shims reinstalled
- **harnesses:** Openclaw version read from the CLI on Node 25.9
- Tick Phase 6 after the forced-failure check

### 🧪 Testing

- **picker:** Run data tests without curses; tick phase 5 items done on CI
- Output audit for backed-up MCP secrets (DESIGN s6.1 row 3)
- **fs:** Run the symlink containment tests wherever symlinks can be created

### ⚙️ Miscellaneous Tasks

- Harden workflow and add Telegram failure alert (phase 6)
- Dependabot for github-actions; document least-privilege release permissions
- Enable symlink creation on Windows runners before tests
- **harnesses:** Openclaw verified against 2026.9.2
## [0.1.0] - 2026-10-02

### 🚀 Features

- Multi-harness resource toggle extracted from skill-toggle
- Interactive curses picker for browsing and toggling
- **mcp:** Support local-scope Claude MCP servers
- **mcp:** Add claude.ai connector toggles
- **status:** Flag untracked parked items and stale live twins
- **symlinks:** Support toggling symlinks and broken links across harnesses
- **store:** Lock, 0600 writes, state schema v3
- **cli:** Json envelope, exit codes, dry-run
- **cli:** Install-shims subcommand
- **harness:** Rule type, codex prompts, grok mcp
- **harness:** Opencode adapter with alias detection
- **cost:** Token estimates and picker column
- **safety:** Containment, checked writes, entry checks, log schema
- **backends:** Flag_json one-token boolean edit for openclaw/opencode
- **safety:** Ops.apply_plan seam, enable refusal, checked config writes
- **profiles:** Profile save/apply/diff/list
- **flag:** Wire openclaw and opencode flag toggles
- **undo:** Undo last batch and enable --all
- **project:** --project scope for dir types
- **project:** --project .mcp.json mcp entries
- **doctor:** Read-only drift and state check
- **harnesses:** Add vibe, devin and agy rows
- **copilot:** Skills, agents and mcp-config.json
- **output:** Add --color auto|always|never

### 🐛 Bug Fixes

- Prune park subdirs left empty after restoring a nested item
- **gitignore:** Append trailing slash to directory check-ignore queries
- **store:** Harden lock takeover and error paths
- **cli:** Refuse names that escape the harness dir
- Name utf-8 for all text I/O, fix windows tests
- **doctor:** Missing layout files are absent, not errors
- **cost:** Flag-disabled items are not live
- **profile:** Record scope, skip unusable names
- **fs:** No false restore-failed on early write error
- **toml:** Independent verify, keep CRLF, bad backup
- **state:** Validate entries, check --project
- Address self-review findings in doctor, cost, toml
- **copilot:** Validate backup text and cover guards with tests
- **test:** Write copilot fixtures as bytes
- **test:** Write copilot fixtures as bytes

### 💼 Other

- Phase 2 profiles, scope and safety
- Phase 2 windows test fix
- Tick phase 2 roadmap

### 🚜 Refactor

- Split into agent_toggle package
- **harness:** Declarative harness records

### 📚 Documentation

- Add living design doc and roadmap
- **design:** Defer pypi to phase 4, os ports to 5
- **design:** Add phase status checklist
- **design:** Add phase 6 ci telegram alerts
- Add design roadmap and CLI command surface
- **readme:** Drop personal path from test command
- Mark phase 0 and 1 complete
- **readme:** Add checkout form of install-shims
- **design:** Record green ci on all three oses
- Grok mcp path confirmed on a live install
- Document phase 2 features
- Sync review fixes into readme, design, changelog
- **design:** Record green ci for phase 2
- **design:** Add phase 3.5 colorful cli to roadmap
- Add remaining-work checklist in TODO.md
- Add phase 3 harness support (copilot, vibe, devin, agy)
- **roadmap:** Tick phase 3 and update changelog
- **todo:** Refresh intro for phase 3 status
- Document color output and release process

### 🧪 Testing

- **harness:** Cover multi-candidate dirs
- **backends:** Pin toml headers sub-table backup
- **fs:** Windows newline and 8.3 path in checked_write

### ⚙️ Miscellaneous Tasks

- Add oss hygiene docs
- Ignore claude agent memory dir
- **release:** Add tag-triggered PyPI publish workflow
# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Colorful CLI output: `--color auto|always|never` flag controls ANSI color for
  human output; `auto` (default) honors `NO_COLOR`, `FORCE_COLOR` and `TERM=dumb`
  environment variables and colors only on a TTY; `--json` is never colored and
  piped output stays plain unless `--color always` or `FORCE_COLOR` is set; the
  picker uses curses color pairs and falls back to monochrome when colors are
  unavailable.
- Copilot adapter: skills, agents and MCP servers (`~/.copilot/mcp-config.json`,
  through a user-scope strict-JSON backend); `config.json` is never edited and
  plugins are not yet supported.
- Vibe adapter (skills), a Devin row (explicit not applicable: no toggleable
  resources) and an Antigravity (`agy`) table row only; its adapter waits until
  the layout is observed (DESIGN section 11 q1).
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
- Tag-triggered release workflow (`.github/workflows/release.yml`) with PyPI
  trusted publishing (OIDC, no stored tokens, GitHub environment `pypi`) and a
  macOS post-publish smoke job that installs the published version and runs a
  `disable` / `enable` round trip in a throwaway HOME.
- PyPI metadata in `pyproject.toml`: classifiers, keywords, project URLs
  (homepage, repository, issues, changelog), SPDX license identifier, and
  setuptools ≥ 77 as a build backend requirement.

### Changed

- GitHub Actions in `ci.yml` pinned by commit SHA with a `# vX.Y.Z` comment
  denoting the semantic version of the action; deliberately updated together.
- MCP entries whose `tools` is `["*"]` now get the flat token estimate on every
  backend instead of counting as one tool.
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

### Notes

- Disabling a copilot MCP server re-serialises the whole `mcp-config.json`
  (formatting is normalised); the backup holds the pre-disable file text at mode
  `0600`.

### Fixed

- `doctor`: a missing MCP config file, missing `mcpServers` / flag-parent key or missing
  `config.toml` is now `absent` (informational, exit 0); only a present but unparseable
  or unsupported file is `error: layout changed`. A recorded flag entry whose file or key
  vanished is still an error. An empty leftover `parked/<sha8>` dir after a project
  `enable` no longer warns.
- `cost`, the picker and `profile save` show an openclaw skill that was disabled by its
  flag as disabled, and list live openclaw plugins and opencode mcp servers that carry a
  boolean `enabled` (they were listed only while parked, so a profile could not disable
  them).
- Profiles record their scope: a `--project` profile applied without `--project` (or the
  reverse) exits 2 instead of toggling the user's items. `profile save` skips, with a
  warning, names `apply` would refuse (such as an MCP server called `team/search`).
- codex / grok `config.toml` edits are verified against the original text (not a copy of
  the new text), refuse to overwrite a file edited between read and write, keep CRLF line
  endings byte for byte, and a corrupt or non-UTF-8 MCP backup fails that one row instead
  of aborting the batch.
- `fs.checked_write` no longer reports a failed restore when the write failed before
  touching the file.
- A hand-edited non-object state entry is a named error instead of a `TypeError`;
  `list`, `status`, `cost` and `profile save` tolerate bad field types.
- `list --project` and `enable --all --project` exit 4 for a missing project dir, like
  `disable --project`.
- Names with `..`, an absolute path, an empty part or a leading `-` are refused
  (exit 2), and an item whose parent resolves outside the harness dir is never
  touched, so `disable skill ../../x` can no longer move files outside it.
- Restoring a codex MCP server no longer doubles the blank line before it.
