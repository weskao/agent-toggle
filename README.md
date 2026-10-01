# agent-toggle

Temporarily disable and restore AI-agent resources — skills, agents, commands,
rules, plugins, MCP servers — across Claude Code, Codex, Grok CLI, OpenCode and
OpenClaw — and show what each one costs at session start.

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

```sh
uv tool install git+<repo-url>         # or: pipx install git+<repo-url>
agent-toggle --version
```

From a checkout, either install it editable or run it in place:

```sh
git clone <repo-url> agent-toggle
cd agent-toggle
pip install -e .                       # puts the `agent-toggle` console script on PATH
python3 agent_toggle.py status         # no install needed; same CLI
```

Python 3.10+, no runtime dependencies. Then write the skill shim:

```sh
agent-toggle install-shims             # or: ./install.sh, a thin wrapper over it
```

`install-shims` writes a thin skill shim (`skills/agent-toggle/SKILL.md`) into
every installed harness that supports skills, so `/agent-toggle` works from any
of them, and adds the park dirs (`skills-disabled/` etc.) to a `.gitignore`
already present in that harness home. It is idempotent; `--dry-run` shows the
plan. The tool itself stays in one place.

## Usage

```sh
agent-toggle <command> [args]          # or: python3 agent_toggle.py <command> [args]
```

| command | what it does |
|---|---|
| `ui` | interactive picker — cost column, sort, filters; `--dry-run` shows the plan for what you stage and changes nothing |
| `status` | health check: harnesses found, types each supports, parked counts, gitignore, untracked parked items, stale live twins, shared dirs |
| `list [type]` | what is currently disabled |
| `cost [--type T]` | estimated startup tokens per item, biggest first (read-only; `--harness H` filters) |
| `install-shims` | write the skill shim into every installed harness (`--dry-run` shows the plan) |
| `disable <type> <name>...` | park one or more items (`--dry-run` shows the plan) |
| `enable <type> <name>...` | put them back (`--dry-run` shows the plan) |
| `migrate` | import an older `~/.claude-toggle/` state |

`<type>` = `skill` / `agent` / `command` / `rule` / `plugin` / `mcp`.
`rule` is claude-only (`~/.claude/rules/*.md`, parked in `rules-disabled/`).

Flags accepted by every command, before or after the subcommand:

- `--harness claude|codex|grok|opencode|openclaw` picks the target (default
  `claude`; on `list` / `status` / `cost` it filters when given).
- `--json` prints exactly one JSON document and nothing else on stdout:
  `{"ok", "command", "results": [{harness, type, name, action, status, detail, ...}],
  "warnings", "needs_new_session"}`. Errors -- including unexpected ones, as
  `{ExceptionType}: {message}` -- always emit it, with `"ok": false`.
  Exception: `--help` / `--version` print plain text even with `--json`, and
  `ui` is interactive so it rejects `--json` (exit 2).
- `--version` prints the version.
- `-v` / `--verbose` (or `AGENT_TOGGLE_DEBUG=1`) adds a traceback on stderr for
  unexpected errors; otherwise they are a single `error:` line.

Extra row fields: `list` rows carry `at`, `mechanism`, `companions`; `cost` rows
carry `enabled`, `tokens`, `would_save`, `chars`, and the final `total` row
carries `total_tokens`, `saved_tokens`, `formula`. Rows for a
directory shared with another harness (see below) carry `shared_with`, the other
harnesses that read it: `disable` / `enable` rows relative to the harness you
asked, plus `owner`, the harness whose park dir and state entry hold the item;
`list` and `cost` rows are filed under the owner (`shared_with` is `[]` when
unshared).

`--dry-run` (`disable` / `enable` / `install-shims` / `ui`) computes the plan --
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

## What each harness supports

| harness | home | skill | agent | command | rule | plugin | mcp |
|---|---|:-:|:-:|:-:|:-:|:-:|:-:|
| claude | `~/.claude` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ `~/.claude.json` |
| codex | `~/.codex` | ✓ | ✓ | ✓ (`commands/` + `prompts/`) | — | — | ✓ `config.toml` |
| grok | `~/.grok` | ✓ | — | — | — | — | ✓ `config.toml` (assumed) |
| opencode | `$XDG_CONFIG_HOME/opencode`, else `~/.config/opencode` | ✓ | — | ✓ (`command/`) | — | — | — |
| openclaw | `~/.openclaw` | ✓ | ✓ | — | — | — | — (sqlite) |

Only `claude` plugins are toggleable (through `claude plugin enable/disable`).
Codex has no plugin CLI this tool can drive: `disable plugin x --harness codex`
exits `4`, which is why the matrix leaves it unchecked.

The grok MCP location (`~/.grok/config.toml`, same `[mcp_servers.<name>]`
tables as codex) is **assumed** from the design survey, not verified against a
live install; treat grok MCP as experimental until confirmed.

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
| project | the repo's own `.mcp.json` | — committed config, not ours to move |
| claude.ai connector | your account | ✓ — recorded in each *existing* project's `disabledMcpServers`; a project first opened later needs the toggle re-run |

`claude mcp remove -s local` only sees the project it runs in, so the project
path is recorded at disable time and the restore runs back in that directory.
One name that is local-scope in several projects is refused unless your cwd
picks the winner — guessing would restore it into the wrong project.

## Where state lives

All of it in `~/.agent-toggle/` (mode `0700`), never as marker files next to the targets —
your `git status` in your own project must not change because of our
bookkeeping.

| file | contents |
|---|---|
| `state.json` | current disabled list (schema v3; atomic write, mode `0600`) |
| `lock` | held by `disable` / `enable` / `migrate` / `ui` for the whole batch; a second run waits 5 s then exits `3` (stale after 10 min *and* its PID is gone; a live batch refreshes it per item) |
| `log.jsonl` | one line per operation (mode `0600`) |
| `mcp-backups/` | `<harness>__<server>.json` (mode `0600` -- may hold auth headers) |
| `companions/` | parked exclusive helper files |

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

## Cross-machine behaviour

Disabling is a **local** decision: park dirs are gitignored and do not sync.
But a disappearance under `skills/` is itself a tracked change, so committing
it means other machines lose those skills on pull. The content stays in git
history — `git checkout <commit> -- skills/<name>` brings it back. Don't
commit those deletions if you don't want them to travel.
