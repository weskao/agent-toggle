# agent-toggle

Temporarily disable and restore AI-agent resources — skills, agents, commands,
rules, plugins, MCP servers — across Claude Code, Codex, Grok CLI, OpenCode,
OpenClaw, Copilot, Vibe, Devin and Antigravity — and show what each one costs at
session start.

**Nothing is ever deleted.** Everything is parked and recorded, and `enable`
puts it back where it came from.

## Why

| target | native mechanism | the gap |
|---|---|---|
| plugin | `claude plugin disable/enable` | fine — this tool just batches it |
| skill | none | moving files by hand hits the rename trap below |
| agent | none | same |
| command | none | same, plus nested `group/name.md` paths get flattened |
| MCP | `remove` only | the config is gone unless you saved it first |

## Install

Latest release, from [PyPI](https://pypi.org/project/agent-toggle/):

```sh
uv tool install agent-toggle
agent-toggle --version
```

Straight from this repository, pinned to a release tag (`v0.1.0` is the current one;
an unpinned `git+` URL installs whatever is on the default branch):

```sh
uv tool install git+https://github.com/weskao/agent-toggle@v0.1.0
agent-toggle --version
```

Alternatively, use `pipx`:

```sh
pipx install agent-toggle             # from PyPI; or: pipx install git+https://github.com/weskao/agent-toggle@v0.1.0
```

From a checkout, either install it editable or run it in place:

```sh
git clone https://github.com/weskao/agent-toggle agent-toggle
cd agent-toggle
pip install -e .                       # puts the `agent-toggle` console script on PATH
python3 agent_toggle.py status         # no install needed; same CLI
```

Python 3.10+, no runtime dependencies. Then write the skill shim:

```sh
agent-toggle install-shims             # installed, or: ./install.sh (a thin wrapper)
python3 agent_toggle.py install-shims  # from a checkout, no install needed
```

`install-shims` writes a thin skill shim (`skills/agent-toggle/SKILL.md`) into
every installed harness that supports skills, so `/agent-toggle` works from any
of them, and adds the park dirs (`skills-disabled/` etc.) to a `.gitignore`
already present in that harness home. It is idempotent; `--dry-run` shows the
plan. The tool itself stays in one place.

**Optional: Telegram alerts for CI** (for maintainers of a fork or clone). Install
with the `telegram` extra, which adds [telegram-kit](https://pypi.org/project/telegram-kit/)
(itself stdlib-only), then run the setup once; see [CI notifications](#ci-notifications):

```sh
uv tool install "agent-toggle[telegram]"     # or: pipx install "agent-toggle[telegram]"
pip install -e ".[telegram]"                 # from a checkout
agent-toggle config                          # bot token + chat id
agent-toggle config sync-ci                  # set the two GitHub repository secrets
```

Without the extra everything else works exactly as before; only `config` asks for it.

## Shell completion

Each release publishes static completion files for bash, zsh and fish as the
`completions` artifact of its Release workflow run. They are generated from the
CLI parser; to build them from a checkout:

```sh
python3 tools/gen_completions.py completions   # agent-toggle.bash, _agent-toggle (zsh), agent-toggle.fish
```

Source `agent-toggle.bash` from `~/.bashrc`, put `_agent-toggle` on your zsh
`fpath`, or copy `agent-toggle.fish` to `~/.config/fish/completions/`.

## Usage

```sh
agent-toggle <command> [args]          # or: python3 agent_toggle.py <command> [args]
```

| command | what it does |
|---|---|
| `ui` | interactive picker — cost column, sort, filters; `--dry-run` shows the plan for what you stage and changes nothing |
| `status` | health check: harnesses found, types each supports, parked counts, gitignore, untracked parked items, stale live twins, shared dirs |
| `list [type]` | what is currently disabled (`--project <dir>` filters to one project) |
| `cost [--type T]` | estimated startup tokens per item, biggest first (read-only; `--harness H` filters) |
| `install-shims` | write the skill shim into every installed harness (`--dry-run` shows the plan) |
| `disable <type> <name>...` | park one or more items (`--dry-run` shows the plan; `--project <dir>` for a repo's own `.claude/` and `.mcp.json`) |
| `enable <type> <name>...` | put them back (`--dry-run` shows the plan; `--project <dir>` likewise) |
| `enable --all` | put back **every** disabled item (`--harness H` narrows it, `--project <dir>` takes only that project's) |
| `undo` | reverse the last logged batch (`--dry-run` shows the plan) |
| `profile save\|apply\|diff\|list` | named sets of live items; see [Profiles](#profiles) |
| `config [test\|sync-ci]` | Telegram settings for the CI failure alerts; see [CI notifications](#ci-notifications) |
| `doctor` | read-only check of each harness layout and of `state.json` against disk; exit `1` only on an `error` row |
| `migrate` | import an older `~/.claude-toggle/` state |

`<type>` = `skill` / `agent` / `command` / `rule` / `plugin` / `mcp`.
`rule` is claude-only (`~/.claude/rules/*.md`, parked in `rules-disabled/`).

Flags accepted by every command, before or after the subcommand:

- `--harness claude|codex|grok|opencode|openclaw|copilot|vibe|devin|agy` picks the
  target (default `claude`; on `list` / `status` / `cost` it filters when given).
- `--json` prints exactly one JSON document and nothing else on stdout:
  `{"ok", "command", "results": [{harness, type, name, action, status, detail, ...}],
  "warnings", "needs_new_session"}`. Errors -- including unexpected ones, as
  `{ExceptionType}: {message}` -- always emit it, with `"ok": false`.
  Exception: `--help` / `--version` print plain text even with `--json`, and
  `ui` is interactive so it rejects `--json` (exit 2).
- `--project <dir>` (`disable` / `enable` / `enable --all` / `list` / `profile
  save|apply|diff`) switches to project scope; see [Project scope](#project-scope).
- `--version` prints the version.
- `-v` / `--verbose` (or `AGENT_TOGGLE_DEBUG=1`) adds a traceback on stderr for
  unexpected errors; otherwise they are a single `error:` line.
- `--color auto|always|never` sets ANSI color for human output: green ok, red errors,
  yellow warnings, cyan harness names, dim secondary text (`status`, `list`, `cost`,
  `disable` / `enable`, `doctor`, warnings and errors; the picker uses curses color pairs
  and stays monochrome when the terminal has no colors). `--json` is never colored.
  `never` turns it off and `always` forces it even when piped; `auto` (default) checks
  `NO_COLOR`, then `FORCE_COLOR`, then `TERM=dumb`, and otherwise colors only on a TTY.
  `always` still stays plain on a Windows console that cannot do ANSI.

Extra row fields: `list` rows carry `at`, `mechanism`, `companions`; `cost` rows
carry `enabled`, `tokens`, `would_save`, `chars`, and the final `total` row
carries `total_tokens`, `saved_tokens`, `formula`. Rows for a
directory shared with another harness (see below) carry `shared_with`, the other
harnesses that read it: `disable` / `enable` rows relative to the harness you
asked, plus `owner`, the harness whose park dir and state entry hold the item;
`list` and `cost` rows are filed under the owner (`shared_with` is `[]` when
unshared).

`--dry-run` (`disable` / `enable` / `enable --all` / `undo` / `profile apply` /
`install-shims` / `ui`) computes the plan --
moves, companions, backups, MCP edits, warnings -- and writes nothing: no state,
log, lock or backup, and no chmod; it never shells out to `claude`. `disable` /
`enable` result rows have action `would-disable` / `would-enable`; every plan
row has status `planned`. Read-only commands (`list`, `status`, `cost`) also
change nothing on disk -- `status` warns about a `state.json` or
backup looser than `0600` instead of fixing it. A dry run whose plan contains a
failing item exits `1`, like the real run would.

| exit code | meaning |
|:-:|---|
| `0` | ok |
| `1` | partial failure (some items failed), or an unexpected error |
| `2` | usage error, including an unknown harness or type |
| `3` | locked by another run |
| `4` | unsupported harness/type pair, or harness not installed |

```sh
agent-toggle disable skill   demo-skill other-skill
agent-toggle disable command demo:batch             # nested commands/demo/batch.md
agent-toggle disable agent   demo-agent
agent-toggle disable mcp     example-mcp
agent-toggle disable skill   demo-skill --harness codex
agent-toggle enable  mcp     example-mcp
agent-toggle cost --type skill --json
```

A colon addresses nesting: `demo:batch` is `commands/demo/batch.md`.

## Interactive picker

```sh
agent-toggle ui
```

```
 filter: telegram█
 *[x]    (92)  claude   command telegram-summary
  [ ]    (61)  claude   skill   telegram-display
  [x]      48   claude   skill   telegram-group-send
  [x]      20   claude   mcp     telegram-example

 4 shown  |  ~68 tok  |  harness:all type:all sort:name  |  1 staged -- Enter to apply
 Tab tick  Enter apply  Esc cancel  s sort  h/t filter  ? keys  / type to filter
```

The number column is the estimated startup tokens (chars / 4, about +-25 %);
a parked row shows `(N)`, what restoring it would load. Plugins appear as rows
too (via `claude plugin list --json`; skipped under `ui --dry-run`, which never
shells out).

| key | action |
|---|---|
| `s` | cycle sort: name, cost (biggest first) |
| `h` | cycle the harness filter |
| `t` | cycle the type filter |
| `?` | show the key list |
| `/` | start typing a filter |
| any other printable character | appends to the filter (terms are ANDed, case-insensitive) |
| `Backspace` / `Ctrl-U` | delete one character / clear the filter |
| `↑` `↓` / `Ctrl-P` `Ctrl-N` | move; `PgUp`/`PgDn` jump a screen |
| `Tab` | tick / untick the highlighted row |
| `Enter` | apply every staged change (with `--dry-run`: show the plan) |
| `Esc` / `Ctrl-C` | cancel — nothing is applied |

`s`, `h`, `t` and `?` are commands while the filter is empty. Press `/` first
to type a filter that begins with one of them (the example above is typed
`/telegram`); once the filter is non-empty, every letter just types.

The checkbox shows the **enabled** state: `[x]` is live, `[ ]` is parked. A
`*` marks a row you changed.

Nothing happens while the picker is open. Changes are staged, the screen is
torn down, and only then do the real operations run — so their output (which
companion files moved, which were kept because they are shared) is readable
instead of fighting curses for the terminal.

Built on stdlib `curses`, so there is nothing to install on macOS and Linux
(on Windows, `pip install "agent-toggle[windows]"` pulls `windows-curses`).
Without curses, `ui` falls back to a numbered menu with the same staging and the
same result: type row numbers (`1 3 5-7`) to tick or untick, `/text` to filter,
`s` / `h` / `t` to sort and cycle the harness and type filters, `a` to apply, `q`
(or end of input) to cancel.

## What each harness supports

| harness | home | skill | agent | command | rule | plugin | mcp |
|---|---|:-:|:-:|:-:|:-:|:-:|:-:|
| claude | `~/.claude` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ `~/.claude.json` |
| codex | `~/.codex` | ✓ | ✓ | ✓ (`commands/` + `prompts/`) | — | — | ✓ `config.toml` |
| grok | `~/.grok` | ✓ | — | — | — | — | ✓ `config.toml` |
| opencode | `$XDG_CONFIG_HOME/opencode`, else `~/.config/opencode` | ✓ | — | ✓ (`command/`) | — | — | ✓ `opencode.json` (flag, *assumed*) |
| openclaw | `~/.openclaw` | ✓ (dir move, or flag when `skills.entries.<name>` exists, *assumed*) | ✓ | — | — | ✓ `openclaw.json` (flag, *assumed*) | — (sqlite) |
| copilot | `~/.copilot` | ✓ | ✓ | — | — | — | ✓ `mcp-config.json` |
| vibe | `~/.vibe` | ✓ | — | — | — | — | — |
| devin | `~/.devin` | — | — | — | — | — | — |
| agy | `~/.antigravity` | — | — | — | — | — | — |

Per-harness locations, mechanisms and the harness versions this was checked
against are in [docs/harnesses.md](docs/harnesses.md).

**Homes per OS.** Every `~` above is Python's `Path.home()`: `$HOME` on macOS and
Linux, `%USERPROFILE%` on Windows (so `~/.claude` is `C:\Users\<you>\.claude`).
opencode honours an absolute `$XDG_CONFIG_HOME` on every OS and otherwise uses
`~/.config/opencode`, Windows included; the tool does not look in `%APPDATA%`. This
tool's own state lives in `~/.agent-toggle/`. The Linux and Windows locations
follow the harnesses' documentation and have not yet been checked against real
installs there; if a harness keeps its files elsewhere, `status` shows it as not
installed.

Plugin ids are passed to `claude plugin ...`, which on Windows runs through
`cmd.exe`. An id with anything outside `A-Z a-z 0-9 . _ @ : / -` (for example `&`,
`%`, `|` or a space) is refused on every OS before any command runs.

Copilot's `config.json` is machine-managed and never edited by this tool; Copilot
plugins are not yet supported. `claude` plugins are toggled through `claude
plugin enable/disable`; `openclaw` plugins through a config flag (see [Assumed
formats](#assumed-formats)). Codex has no plugin CLI this tool can drive: `disable
plugin x --harness codex` exits `4`, which is why the matrix leaves it unchecked.

Flag items (openclaw plugins and flagged skills, opencode mcp) are toggled with
`disable` / `enable`, `undo` and `enable --all`. The picker, `cost` and `profile`
list them live (read from the config file; only entries that carry a boolean
`enabled`, since a key is never invented) and parked (from `state.json`); an
openclaw skill whose flag was switched off by this tool is shown as disabled even
though its directory is still in place.

The grok MCP location (`~/.grok/config.toml`, the same `[mcp_servers.<name>]`
tables as codex, including a `.headers` sub-table for remote servers) was
confirmed on a live install. Backups keep every sub-table verbatim.

Unsupported pairs fail loudly. OpenClaw keeps MCP servers in
`state/openclaw.sqlite`, not a file this tool can safely slice, so it refuses
rather than guessing.

### Shared directories

OpenCode may read skills from another harness's directory through
`opencode.json` → `skills.paths` (absolute, `~/`-prefixed or relative-to-the-
opencode-dir entries; a bare `~` or `$HOME/...` is not expanded, and
`opencode.jsonc` is not read). Such a directory is **one** item, filed under
its owner (the harness whose home really holds it): it is
parked once, tracked once, and every row reports `shared_with`, the other
harnesses it also affects. `status` prints `shared dir with: ...`.

If the live directory carries `.synced-from-*` markers, a sync job may
re-create what you parked; `status` warns about it. Park in the source harness
instead. The warning is printed once per harness that views the directory.

## Companion files

A skill or command often calls a helper next to it — `scripts/foo.py`,
`tg-send.sh`. On `disable`, the item's text is scanned for such references and
each one is classified:

- **exclusive** (nothing else in the harness mentions it) → parked alongside
  the item, restored with it.
- **shared** (anything else references it) → **left alone**, and reported.

Sharing is a veto, not a warning. `tg-send.sh` is referenced by five different
commands; moving it because one of them got disabled would silently break the
other four.

Detection reads file text, so a path built at runtime cannot be found. Every
companion decision is printed before anything moves, so a wrong guess is
visible rather than silent.

## MCP backups are verbatim

- **Claude**: the raw entry from `~/.claude.json`. Never `claude mcp get` —
  that prints a human summary and silently drops auth fields (`headers`,
  `headersHelper`), so a server restored from it fails with 401.
- **Codex**: the exact `[mcp_servers.<name>]` text block including its
  `.tools.*` sub-tables. Python's stdlib reads TOML but cannot write it, and a
  hand-rolled serializer would mangle comments — a text slice is lossless and
  much less code.

MCP changes need a **new session** to take effect.

### Claude MCP scopes

Both scopes stored in `~/.claude.json` are togglable, and the scope round-trips:

| scope | lives in | toggle |
|---|---|---|
| user | top-level `mcpServers` | ✓ |
| local | `projects/<dir>/mcpServers` | ✓ — project path saved with the backup |
| project | the repo's own `.mcp.json` | ✓ with `--project <dir>` only, see [Project scope](#project-scope) |
| claude.ai connector | your account | ✓ — recorded in each *existing* project's `disabledMcpServers`; a project first opened later needs the toggle re-run |

`claude mcp remove -s local` only sees the project it runs in, so the project
path is recorded at disable time and the restore runs back in that directory.
One name that is local-scope in several projects is refused unless your cwd
picks the winner — guessing would restore it into the wrong project.

## Profiles

A profile is a named snapshot of which items are live, for the dotfiles repo
or a second machine. It holds only `{harness, type, name, live}` per item --
never a path or a secret.

```sh
agent-toggle profile save work                       # -> ~/.agent-toggle/profiles/work.json
agent-toggle profile save work --out ~/dotfiles/agent-toggle/work.json
agent-toggle profile diff work                       # what apply would do; writes nothing
agent-toggle profile apply ~/dotfiles/agent-toggle/work.json --dry-run
agent-toggle profile apply work
agent-toggle profile list
```

**`apply` toggles only the items the profile mentions.** An item the profile
lists as live but that is parked now is enabled; one it lists as parked but
that is live now is disabled; everything else is left alone -- in particular an
item installed after the save is never touched. An item the profile names that
this machine does not have gets a `skipped` row (`not on this machine`), not a
failure. `apply` runs through the same path as `enable` / `disable` (one lock,
one batch, logged, undoable with `undo`).

- An argument ending in `.json` or containing a path separator is a **file
  path** (absolute paths are fine); anything else is a stored profile name under
  `~/.agent-toggle/profiles`. `..` in a path, and names that are not plain file
  names, are refused (exit `2`), as are a bad version, an unknown harness or
  type, an invalid item name, a duplicate item or a file over 1 MiB.
- `--out` belongs to `save` only; `--dry-run` to `apply` / `diff`. `--harness H`
  narrows `save`, `apply` and `diff` to one harness.
- `diff` and `apply --dry-run` do not read plugin state (that needs the claude
  CLI, and a dry run never shells out), so profile plugin items show as
  `skipped` there.
- A user-scope profile never contains project-scope entries; with
  `--project <dir>` the same commands save, diff and apply that project's
  items (claude layout) instead -- the profile holds no directory, so `apply`
  needs `--project` again. A project profile records `"scope": "project"`;
  applying it without `--project` (or a user profile with `--project`) exits `2`,
  so a project profile can never disable the user's items.
- `save` skips (with a warning) any item whose name `apply` would refuse, such as
  an MCP server called `team/search`, so a saved profile always applies.

## Undo and `enable --all`

`undo` reads `~/.agent-toggle/log.jsonl`, takes the batch of the last
successful `disable` / `enable`, and reverses its rows in reverse order. The
reversal is logged as its own batch, so `undo` twice puts things back.
Project rows are replayed in their own project scope, never in user scope.

```sh
agent-toggle undo --dry-run
agent-toggle undo
```

- Nothing logged yet: `nothing to undo` (exit `0`). A log written before
  batch ids existed: `nothing to undo: log predates undo` (exit `1`).
- `undo` rejects `--harness` (exit `2`): it reverses a whole batch.
- The log is a plain file you can edit; its names and project dirs are checked
  like command-line input, and a project dir is trusted as written, exactly as
  user-scope replay trusts the log.

`enable --all [--harness H] [--project <dir>]` restores every disabled entry in
scope, each in its own scope (`--project` takes only that project's).
`enable --all <name>` and `disable --all` are usage errors (exit `2`). Preview
bulk operations with `--dry-run`.

## Project scope

`--project <dir>` (`.` = the current directory) points `disable`, `enable`,
`enable --all`, `list` and `profile` at one repo: its `.claude/` dir types
(`skill`, `agent`, `command`, `rule`) and its own `.mcp.json` servers (`mcp`).
Claude layout only: `--harness codex --project ...` exits `4`, as does a
missing directory or one with neither `.claude/` nor `.mcp.json`. `$HOME`, its
ancestors, the tool's own state dir and the harness homes are refused (exit
`2`) -- that is user scope. `list` and `enable --all` run the same checks. `cost` and
`ui` are user-scope only.

```sh
agent-toggle disable skill demo-skill --project .
agent-toggle disable mcp example-mcp --project ~/work/repo
agent-toggle list --project .
agent-toggle enable --all --project .
```

- Parked items go to `~/.agent-toggle/parked/<sha8>/`, **never inside the
  project**; project and user state entries are separate, so one can never
  restore into the other. Companion files are not moved in project scope.
- **A tracked file still disappears from the worktree.** Every project-scope
  disable prints a warning like this one, and `git status` shows the deletion:

  ```
  <repo>/.claude/skills/demo-skill is a tracked deletion in git status; restore with: agent-toggle enable skill demo-skill --project <repo>
  <repo>/.mcp.json is a tracked change in git status; restore with: agent-toggle enable mcp example-mcp --project <repo>
  ```

  Restore with that command, or `git checkout`; do not commit the deletion if
  the repo is shared.
- `.mcp.json` is edited directly (no `claude` CLI) and must be **strict JSON**:
  a BOM, comments or trailing commas are refused. `disable` rewrites the file in
  its detected layout (tabs or 2 spaces, LF or CRLF) and saves a verbatim backup
  at `mcp-backups/<sha8>__claude__<name>.json` (mode `0600`; it holds the file
  text, so it may hold auth headers). `enable` restores the file byte for byte
  if it is unchanged since the disable, otherwise merges the entry back in and
  reformats.
- Moves across filesystems fall back to copy + delete (not atomic). An empty
  `parked/<sha8>/*-disabled` dir may remain after `enable`; `doctor` ignores it.
- `status` prints one `project <dir>` line per project holding parked items.
  A project with only `.mcp.json` saves only its parked servers in
  `profile save --project`; live ones are not listed (the inventory needs
  `.claude/`).

## Doctor

```sh
agent-toggle doctor [--harness H] [--json]
```

Read-only: no lock, no state write-back, no `claude` CLI call. For each
installed harness it compares the live layout with the table row (expected
dirs and config keys), then cross-checks `state.json` against disk (parked
item present, origin dir present, backup present, project dir present, entry
passes the same tamper checks `enable` runs, modes no looser than `0600` /
`0700`). Rows (`action: doctor`) carry a status:

| status | meaning |
|---|---|
| `ok` | matches |
| `absent` | a dir, config file or key the row expects is not there (an MCP file never created, a missing `mcpServers` key) -- informational, exit `0` |
| `note` | worth knowing: shared dir, orphan backup, JSONC `openclaw.json` / `opencode.json`, a `--harness` that is not installed |
| `unverified` | could not be parsed here (an existing codex/grok `config.toml` on Python 3.10, which has no `tomllib`) |
| `warn` | loose file modes; a parked item with no state entry; a leftover `parked/<sha8>` dir that still holds files (an empty one after `enable` is ignored) |
| `error` | needs fixing: a config that exists but is unparseable or unsupported (`layout changed`), a state entry whose files are gone or fail the tamper checks, a flag re-enabled outside the tool |

Only `error` makes the exit code `1`; each problem row names the command that
fixes it. `--harness X` for a harness that is not installed is a `note` (exit
`0`). Companion files are not checked.

## Assumed formats

Two config shapes come from the design survey (`docs/DESIGN.md` §4 / §11) and
were **not verified on a real install**:

- `openclaw.json`: `skills.entries.<name>.enabled` and
  `plugins.entries.<name>.enabled`
- `opencode.json`: `mcp.<name>.enabled`

The edit changes one boolean token and nothing else, is verified after writing
and rolled back on any mismatch (bytes and file mode). Files that are not
strict JSON (JSONC / JSON5 -- comments, trailing commas) or that repeat a key are
**refused**, never rewritten; a leading BOM is kept as is; a missing key is
refused, never invented. An openclaw skill
uses the flag only when `skills.entries.<name>` already exists, otherwise its
directory is moved. If a run is killed between the flag write and the state
save, the flag is `false` with no state entry: `enable` then tells you to set it
back by hand. Please report a real install that differs.

## Safety checks

- `enable` refuses a state entry whose `origin`, `parked_at`, backup or flag
  file is outside its harness home, project or `~/.agent-toggle`, or holds `..`:
  an error row `refused: <reason>`, nothing moved.
- Every edit of a JSON or TOML config is verified after writing (still parses,
  only the target changed) and rolled back, bytes and mode, on failure. An
  invalid codex `config.toml` makes an MCP edit fail and roll back instead of
  being rewritten. A codex `config.toml` edit also fails (and is left as the other
  tool wrote it) if the file changed between read and write, and CRLF files keep their
  line endings. On Python 3.10 (no `tomllib`) the TOML check is textual only: it checks
  against the original text that only the one block changed, but cannot parse.
- Profiles and project dirs are validated like command-line input.

## Where state lives

All of it in `~/.agent-toggle/` (mode `0700`), never as marker files next to the targets —
your `git status` in your own project must not change because of our
bookkeeping.

| file | contents |
|---|---|
| `state.json` | current disabled list (schema v3; atomic write, mode `0600`) |
| `lock` | held by `disable` / `enable` / `enable --all` / `undo` / `profile apply` / `migrate` / `ui` for the whole batch; a second run waits 5 s then exits `3` (stale after 10 min *and* its PID is gone; a live batch refreshes it per item) |
| `log.jsonl` | one line per operation (mode `0600`), see below |
| `mcp-backups/` | `<harness>__<server>.json`, or `<sha8>__<harness>__<server>.json` for a project `.mcp.json` (mode `0600` -- may hold auth headers) |
| `companions/` | parked exclusive helper files |
| `parked/<sha8>/` | items parked by `--project` (`<sha8>` = first 8 hex of the SHA-1 of the resolved project dir) |
| `profiles/` | `<name>.json` profiles (dir `0700`, files `0600`) |

Each `log.jsonl` row is `{ts, harness, type, name, action, result, batch,
project, scope, detail}`. `batch` is one id per run (what `undo` reverses);
`project` is `null` and `scope` is `user` outside project scope (a claude
local-scope MCP row has `scope: local` and its working directory in `project`).
Older rows without `batch`/`harness` cannot be undone.

## Two guardrails, both earned

**1. `safe_move()` never lets the source be renamed into the destination.**
`mv X dest/` when `dest` does not exist *renames* `X` to `dest` — the first
item fails, the second "succeeds" by becoming that directory, and `SKILL.md`
and `.git` end up scattered at the root. `shutil.move()` behaves identically.
So: create the directory, prove it *is* a directory, prove the target does not
exist, and only then touch the source.

Order matters too: `mkdir(exist_ok=True)` raises `FileExistsError` when the
path exists as a *file*, so the `is_dir()` check has to come **before** the
`mkdir` or it is unreachable.

**2. Park dirs that are not gitignored get a warning.** Without it, every
disable leaves dozens of deletion lines in `git status`.

## Paths are resolved before comparison

`~/.claude` is often a symlink, and on macOS `/var` is one. Comparing
unresolved paths makes every companion look like it lives outside the harness.
Both sides are resolved first.

## Design and roadmap

Architecture, harness survey, cost model, known gaps and the phased roadmap
toward a public multi-OS release live in `docs/DESIGN.md`. Release notes are in
`CHANGELOG.md`.

## Tests

```sh
python3 -m unittest discover -s tests -v
ruff check .
```

stdlib `unittest`, no fixtures, no network. Every test runs against a
throwaway temp `HOME` and a stubbed `claude` CLI.

## CI notifications

`.github/workflows/ci.yml` can send a Telegram message when the test matrix fails on a
push (a lint-only failure does not page). Alerts never fire for pull requests or green
runs. The message names the repository, branch, 7-character commit and a link to the run.

Set it up once, with the `telegram` extra installed (see [Install](#install)):

```sh
agent-toggle config            # prompts for the bot token (hidden) and the chat id
agent-toggle config test       # send one test message
agent-toggle config sync-ci    # set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID on the repo
```

`config` works like aicp's `--config`: Enter keeps the current value, `-` clears it.
The bot token is kept only in the OS credential store (macOS Keychain, Linux Secret
Service, Windows DPAPI) through telegram-kit, never in a file or on a command line, and
is shown masked. With no credential store it refuses to store the token; set
`TG_BOT_TOKEN` in the environment instead (`TG_CHAT_ID` likewise for the chat id). The
chat id is ordinary configuration in `~/.agent-toggle/config.json`: a number, `-100...`
for a group, or an `@channel`. `config sync-ci` needs the GitHub CLI (`gh`) signed in; it
passes both values on stdin, and `--repo OWNER/REPO` / `--dry-run` work as elsewhere.

To skip the tool, set the secrets by hand (each command prompts for the value):

```sh
gh secret set TELEGRAM_BOT_TOKEN
gh secret set TELEGRAM_CHAT_ID
```

With either secret unset (a fork, or a clone you have not configured) the notify job
prints a `::notice::` and exits 0, so it never adds a second red X.

## Releasing

A release is a git tag `vX.Y.Z` (where `X.Y.Z` must match `agent_toggle.__version__`).
Pushing the tag triggers `.github/workflows/release.yml`:

1. **Build** job checks that the tag matches `__version__`, builds a wheel and
   source distribution, and generates the shell completion files (uploaded as the
   `completions` artifact; the workflow does not create a GitHub Release).
2. **Publish** job uploads to PyPI using
   [trusted publishing](https://docs.pypi.org/trusted-publishers/),
   with no stored tokens—only the `pypi` environment and OIDC setup.
3. **Smoke** job installs the published version on macOS under a throwaway `HOME`
   and runs a `disable` / `enable` round trip to verify the install.

Before the first tag, register a trusted publisher for this repository with
PyPI: workflow `release.yml`, environment `pypi`. See
[PyPI trusted publishers documentation](https://docs.pypi.org/trusted-publishers/).

## Cross-machine behaviour

Disabling is a **local** decision: park dirs are gitignored and do not sync.
But a disappearance under `skills/` is itself a tracked change, so committing
it means other machines lose those skills on pull. The content stays in git
history — `git checkout <commit> -- skills/<name>` brings it back. Don't
commit those deletions if you don't want them to travel.
