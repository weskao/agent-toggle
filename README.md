# agent-toggle

Temporarily disable and restore AI-agent resources — skills, agents, commands,
plugins, MCP servers — across Claude Code, Codex, Grok CLI and OpenClaw.

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
git clone <this repo> agent-toggle
cd agent-toggle && ./install.sh        # same as: python3 agent_toggle.py install-shims
```

`install-shims` writes a thin skill shim (`skills/agent-toggle/SKILL.md`) into
every installed harness that supports skills, so `/agent-toggle` works from any
of them, and adds the park dirs (`skills-disabled/` etc.) to a `.gitignore`
already present in that harness home. It is idempotent; `--dry-run` shows the
plan. The tool itself stays in one place.

## Usage

```sh
python3 agent_toggle.py <command> [args]
```

| command | what it does |
|---|---|
| `ui` | interactive picker — type to filter, arrows to move, Tab to tick |
| `status` | health check: harnesses found, types each supports, parked counts, gitignore, untracked parked items and stale live twins |
| `list [type]` | what is currently disabled |
| `install-shims` | write the skill shim into every installed harness (`--dry-run` shows the plan) |
| `disable <type> <name>...` | park one or more items (`--dry-run` shows the plan) |
| `enable <type> <name>...` | put them back (`--dry-run` shows the plan) |
| `migrate` | import an older `~/.claude-toggle/` state |

`<type>` = `skill` / `agent` / `command` / `plugin` / `mcp`.

Flags accepted by every command, before or after the subcommand:

- `--harness claude|codex|grok|openclaw` picks the target (default `claude`;
  on `list` / `status` it filters when given).
- `--json` prints exactly one JSON document and nothing else on stdout:
  `{"ok", "command", "results": [{harness, type, name, action, status, detail, ...}],
  "warnings", "needs_new_session"}`. Errors -- including unexpected ones, as
  `{ExceptionType}: {message}` -- always emit it, with `"ok": false`.
  Exception: `--help` / `--version` print plain text even with `--json`.
- `--version` prints the version.
- `-v` / `--verbose` (or `AGENT_TOGGLE_DEBUG=1`) adds a traceback on stderr for
  unexpected errors; otherwise they are a single `error:` line.

`--dry-run` (`disable` / `enable`) computes the plan -- moves, companions,
backups, MCP edits, warnings -- and writes nothing: no state, log, lock or
backup, and no chmod; it never shells out to `claude`. Result rows say
`would-disable` / `would-enable`. Read-only commands (`list`, `status`) also
change nothing on disk -- `status` warns about a `state.json` or backup looser
than `0600` instead of fixing it.

| exit code | meaning |
|:-:|---|
| `0` | ok |
| `1` | partial failure (some items failed), or an unexpected error |
| `2` | usage error |
| `3` | locked by another run |
| `4` | unsupported harness/type pair, or harness not installed |

```sh
agent_toggle.py disable skill   academic-plotting matplotlib
agent_toggle.py disable command orch:batch          # nested commands/orch/batch.md
agent_toggle.py disable agent   kubernetes-architect
agent_toggle.py disable mcp     telegram-mcp
agent_toggle.py disable skill   foo --harness codex
agent_toggle.py enable  mcp     telegram-mcp
```

A colon addresses nesting: `orch:batch` is `commands/orch/batch.md`.

## Interactive picker

```sh
agent_toggle.py ui
```

```
 filter: telegram█
  [x] claude   command telegram-summary
 *[ ] claude   skill   telegram-display
  [x] claude   skill   telegram-group-send
  [ ] claude   mcp     telegram-mcp

 4 shown  |  1 change(s) staged -- Enter to apply
 ↑↓ move  Tab tick/untick  Enter apply  Esc cancel  type to filter
```

| key | action |
|---|---|
| any printable character | appends to the filter (terms are ANDed, case-insensitive) |
| `Backspace` / `Ctrl-U` | delete one character / clear the filter |
| `↑` `↓` / `Ctrl-P` `Ctrl-N` | move; `PgUp`/`PgDn` jump a screen |
| `Tab` | tick / untick the highlighted row |
| `Enter` | apply every staged change |
| `Esc` / `Ctrl-C` | cancel — nothing is applied |

The checkbox shows the **enabled** state: `[x]` is live, `[ ]` is parked. A
`*` marks a row you changed.

Nothing happens while the picker is open. Changes are staged, the screen is
torn down, and only then do the real operations run — so their output (which
companion files moved, which were kept because they are shared) is readable
instead of fighting curses for the terminal.

Built on stdlib `curses`, so there is nothing to install. Plugins are not in
the picker: enumerating them needs a `claude plugin list` subprocess whose
output format is not contracted, so they stay a CLI operation.

## What each harness supports

| harness | home | skill | agent | command | plugin | mcp |
|---|---|:-:|:-:|:-:|:-:|:-:|
| claude | `~/.claude` | ✓ | ✓ | ✓ | ✓ | ✓ `~/.claude.json` |
| codex | `~/.codex` | ✓ | ✓ | ✓ | ✓ | ✓ `config.toml` |
| grok | `~/.grok` | ✓ | — | — | — | — |
| openclaw | `~/.openclaw` | ✓ | ✓ | — | — | — (sqlite) |

Unsupported pairs fail loudly. OpenClaw keeps MCP servers in
`state/openclaw.sqlite`, not a file this tool can safely slice, so it refuses
rather than guessing.

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
| claude.ai connector | your account | — turn off at claude.ai → Settings → Connectors |

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

Architecture, harness survey, cost model, and the phased roadmap toward a
public multi-OS release live in `docs/DESIGN.md`.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

stdlib `unittest`, no fixtures, no network. Every test runs against a
throwaway temp `HOME` and a stubbed `claude` CLI.

## Cross-machine behaviour

Disabling is a **local** decision: park dirs are gitignored and do not sync.
But a disappearance under `skills/` is itself a tracked change, so committing
it means other machines lose those skills on pull. The content stays in git
history — `git checkout <commit> -- skills/<name>` brings it back. Don't
commit those deletions if you don't want them to travel.
