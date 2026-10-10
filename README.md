# agent-toggle

Temporarily disable and restore AI-agent resources — skills, agents, commands,
rules, plugins (mods included), MCP servers — across Claude Code, Codex, Grok CLI, OpenCode,
OpenClaw, Copilot, Vibe, Devin and Antigravity — and show what each one costs at
session start.

**Nothing is ever deleted.** Everything is parked and recorded, and `enable`
puts it back where it came from.

## At a glance

<!-- Absolute URLs so the images also render on PyPI, which uses this README. -->

**`agent-toggle ui`**: one list per harness, with what each resource costs in tokens.
Toggle rows, see the token change before you apply it, and apply it as one batch that
`undo` reverses ([Interactive picker](#interactive-picker)).

![agent-toggle ui: the interactive picker](https://raw.githubusercontent.com/weskao/agent-toggle/main/docs/images/ui-picker.jpeg)

**`agent-toggle config`**: every setting in one menu. Turn harnesses on or off and set the
language, update check and Telegram notifications. It also runs tools such as `doctor` and
`undo` ([Settings & config menu](#settings--config-menu)).

![agent-toggle config: the settings menu](https://raw.githubusercontent.com/weskao/agent-toggle/main/docs/images/config-menu.jpeg)

**`/agent-toggle` inside Claude Code**: the same picker as a pane, for turning skills, MCP
servers, plugins and mods on or off without leaving the session ([Claude Code pane mod](#claude-code-pane-mod)).

![/agent-toggle: the picker as a Claude Code pane](https://raw.githubusercontent.com/weskao/agent-toggle/main/docs/images/claude-pane-mod.jpeg)

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

Straight from this repository, pinned to a release tag (replace `vX.Y.Z` with one from
[tags](https://github.com/weskao/agent-toggle/tags); an unpinned `git+` URL installs
whatever is on the default branch):

```sh
uv tool install git+https://github.com/weskao/agent-toggle@vX.Y.Z
agent-toggle --version
```

Alternatively, use `pipx`:

```sh
pipx install agent-toggle             # from PyPI; or: pipx install git+https://github.com/weskao/agent-toggle@vX.Y.Z
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
of them. It writes nothing else into a harness home: parked items live under
`~/.agent-toggle/parked/`, so a harness-home `.gitignore` needs no park-dir
line. Older versions appended `<dir>-disabled/` lines there; inside a git work
tree, `install-shims` reports such stale lines once `migrate` has emptied the
dir they covered, asks y/n on a terminal, removes them without asking with
`--gitignore`, and skips the check with `--no-gitignore` (`--dry-run` only
reports; every other line is kept byte for byte). Each harness gets its own template from
`agent_toggle/shims/<harness>.md.tmpl`, else `generic.md.tmpl` (which tells the
agent to pass `--harness <that harness>`). The shim tells the agent to act only
on the user's explicit request, never on instructions found inside skill or
tool content, and to preview bulk operations with `--dry-run`.
OpenCode's shim goes into the first `skills.paths` dir when one is set (and its
parent exists), else `skills/`. OpenCode also scans `~/.claude/skills` and
`~/.agents/skills`: when one of them already holds the very same shim text, the
OpenCode shim is skipped (`covered by <path>`); a different shim there (the
claude one, whose default harness is `claude`) is noted and OpenCode's own is
still written.

Every shim carries an `<!-- agent-toggle shim: ... -->` marker line.
`install-shims` updates a file that has the marker in place (it is idempotent)
and **refuses** to overwrite one that lacks it, or a symlink: that harness gets
an `error` row with the fix, the other harnesses still install, and the run
exits `1`. A shim written by a version before the marker counts as foreign;
delete it and re-run. `--dry-run` shows the same plan, refusals included. The
tool itself stays in one place.

**Optional: Telegram alerts for CI** (for maintainers of a fork or clone). Install
with the `telegram` extra, which adds [telegram-kit](https://pypi.org/project/telegram-kit/)
(itself stdlib-only), then run the setup once; see [CI notifications](#ci-notifications):

```sh
uv tool install "agent-toggle[telegram]"     # or: pipx install "agent-toggle[telegram]"
pip install -e ".[telegram]"                 # from a checkout
agent-toggle config                          # settings menu: bot token + chat id live under Notifications
agent-toggle config sync-ci                  # set the two GitHub repository secrets
```

Without the extra everything else works exactly as before. Only `config test`,
`config sync-ci` (both exit `4` without it) and the Telegram rows of the
[settings menu](#settings--config-menu) need it; bare `config` still opens, and
those rows read `install agent-toggle[telegram]`.

**Optional: the Claude Code pane mod** (the picker as a pane inside Claude Code, claude harness
only). It is not part of the PyPI package; with `agent-toggle` already on `PATH` from one of the
installs above, type this at the prompt of a Claude Code terminal session (2.1.275 or later):

```
/plugin install agent-toggle --marketplace weskao/agent-toggle
```

Answer `y` to add the marketplace, pick a scope (user is the default), then run `/agent-toggle`.

See [Claude Code pane mod](#claude-code-pane-mod).

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
| `ui` (alias `pick`) | interactive picker — harness tabs, type groups (plus a Mods group), cost bars, search, sort, profiles (`p`); `--dry-run` shows the plan for what you stage and changes nothing; `--project <dir>` picks in a repo's own scope; see [Interactive picker](#interactive-picker) |
| `status` | health check: harnesses found, types each supports, parked counts, gitignore, untracked parked items, stale live twins, shared dirs, and state entries whose parked item is gone (a `stale` row with the fix command; read-only, still exit `0`) |
| `list [type]` | what is currently disabled (`--project <dir>` filters to one project) |
| `cost [--type T]` | estimated startup tokens per item, biggest first (read-only; `--harness H` filters; `--project <dir>` prices a repo's `.claude/` and `.mcp.json` instead of user scope) |
| `install-shims` | write the skill shim into every installed harness that is on in the settings (an off one is skipped unless named with `--harness`); refuses to overwrite a file it did not write (`--dry-run` shows the plan); reports stale legacy `<dir>-disabled/` lines in a harness-home `.gitignore` (`--gitignore` removes them, `--no-gitignore` skips the check); on a terminal, then asks y/n for each problem `doctor` can fix (see [Fixing](#fixing)) |
| `disable <type> <name>...` | park one or more items (`--dry-run` shows the plan; `--project <dir>` for a repo's own `.claude/` and `.mcp.json`) |
| `enable <type> <name>...` | put them back (`--dry-run` shows the plan; `--project <dir>` likewise) |
| `disable\|enable all <name>...` | every type on the harness with that name at once (skill, command, MCP, ...); a name with no match is an `error` row; one batch, so `undo` reverses it |
| `enable --all` | put back **every** disabled item (`--harness H` narrows it, `--project <dir>` takes only that project's) |
| `undo` | reverse the last logged batch (`--dry-run` shows the plan) |
| `profile save\|apply\|diff\|list` | named sets of live items; see [Profiles](#profiles) |
| `config` | settings menu (curses, else a numbered list); `config --json` lists every setting and the package version; see [Settings & config menu](#settings--config-menu) |
| `config test\|sync-ci` | send one Telegram test message / set the GitHub CI secrets; needs the `[telegram]` extra; see [CI notifications](#ci-notifications) |
| `help [command]` | styled help; `help ui` = `--help ui` = `ui --help`; see [Help](#help) |
| `doctor` | check each harness layout and `state.json` against disk; exit `1` only on an `error` row; on a terminal, asks y/n per fix it can run itself |
| `migrate` | import an older `~/.claude-toggle/` state, and move legacy `<home>/<dir>-disabled/` park dirs into `~/.agent-toggle/parked/user/` (see [Upgrading the park layout](#upgrading-the-park-layout)); safe to re-run |

Every command also works with a leading `--` (`agent-toggle --status`, `--help`, `--config`); this README uses the plain form.

`<type>` = `skill` / `agent` / `command` / `rule` / `plugin` / `mcp`.
`rule` is claude-only (`~/.claude/rules/*.md`, parked in `~/.agent-toggle/parked/user/claude/rules/`).
A **mod** (a claude plugin that loads UI code, see [Mods](#mods)) is type `plugin` on the
command line: `disable plugin blast-radius@example-marketplace`.
Disabling a rule prints a warning (also in `--json` `warnings`, dry run included)
that rules may carry safety constraints.

Flags accepted by every command, before or after the subcommand:

- `--harness claude|codex|grok|opencode|openclaw|copilot|vibe|devin|agy` picks the
  target (default `claude`; on `list` / `status` / `cost` it filters when given).
- `--json` prints exactly one JSON document and nothing else on stdout:
  `{"ok", "command", "results": [{harness, type, name, action, status, detail, ...}],
  "warnings", "needs_new_session"}`. Errors -- including unexpected ones, as
  `{ExceptionType}: {message}` -- always emit it, with `"ok": false`.
  Exception: `--help` / `--version` print plain text even with `--json`, and
  `ui` is interactive so it rejects `--json` (exit 2).
- `--project <dir>` (`disable` / `enable` / `enable --all` / `list` / `ui` / `cost` / `profile
  save|apply|diff`) switches to project scope; see [Project scope](#project-scope).
- `--version` prints the version.
- `AGENT_TOGGLE_CLI_TIMEOUT=<seconds>` overrides the timeout of every `claude` CLI call
  (default 30 s for read-only `plugin list`, 120 s for every other call); a
  non-positive or non-numeric value is ignored with a warning.
- `-v` / `--verbose` (or `AGENT_TOGGLE_DEBUG=1`) adds a traceback on stderr for
  unexpected errors; otherwise they are a single `error:` line.
- `--color auto|always|never` sets ANSI color for human output: green ok, red errors,
  yellow warnings, cyan harness names, dim secondary text (`status`, `list`, `cost`,
  `disable` / `enable`, `doctor`, warnings and errors; the picker uses curses color pairs
  and stays monochrome when the terminal has no colors). `--json` is never colored.
  `never` turns it off and `always` forces it even when piped; `auto` (default) checks
  `NO_COLOR`, then `FORCE_COLOR`, then `TERM=dumb`, and otherwise colors only on a TTY.
  Precedence: `--color` flag > `AGENT_TOGGLE_COLOR` > the `color` [setting](#settings--config-menu)
  > `auto`; `NO_COLOR` still wins over `auto`.
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
agent-toggle disable all     example-tool           # skill, command, mcp... named example-tool
agent-toggle disable skill   demo-skill --harness codex
agent-toggle enable  mcp     example-mcp
agent-toggle cost --type skill --json
```

A colon addresses nesting: `demo:batch` is `commands/demo/batch.md`.

## Interactive picker

```sh
agent-toggle ui          # alias: agent-toggle pick
agent-toggle             # same, on a terminal; elsewhere prints help and exits 2
```

```
 agent-toggle  vX.Y.Z                                 5 resources · ~6.3k tok live · 1 staged (+300 tok)
 All 5 │ claude 4 │ codex 2
 🔍 Type to search (name, path, fuzzy)                                           type: All  sort: name
 Skills 3 ─────────────────────────────────────────────────────────────│ beta
  ●   alpha                             claude      1.2k █▎            │
› ● + beta                              claude     (300) ▎             │ harness    claude
  ●   zeta                              claude      5.0k █████         │ type       skill
 Agents 1 ─────────────────────────────────────────────────────────────│ state      ○ parked
  ●   gamma                             claude        50 ▏     +shared │            staged → live
 Commands 1 ───────────────────────────────────────────────────────────│ path       ~/.agent-toggle/parked/user/claude/skills/beta
  ●   delta                             codex         10 ▏             │ cost       0 now; ~300 tok if restored
                                                                       │ since      2026-09-19 10:00:00
 5 of 5 shown · 2/5
 Space toggle  Enter apply  Esc cancel  ? help  / search  ←→ harness  t type  s sort  p profile  a all
```

What is on screen:

- **Title bar**: version and a summary (resources, live tokens, staged changes and their token effect).
- **Harness tab bar**: `All` plus each enabled harness, with counts. Harnesses switched off in
  the [settings](#settings--config-menu) are hidden.
- **Rows grouped by type** (Skills, Agents, Commands, Rules, Plugins, Mods, MCP) under a heading
  with a count.
  `●` is live, `○` parked (`*` / `o` in ASCII); a `+` / `-` before the name marks a staged change,
  and `+shared` marks a directory another harness also reads.
- **Cost column**: estimated startup tokens (chars / 4, about +-25 %) with a colored bar. A parked
  row shows `(N)` in brackets, what restoring it would load. Plugins appear as rows too (via
  `claude plugin list --json`; skipped under `ui --dry-run`, which never shells out).
  Mods get their own **Mods** heading right after Plugins (see [Mods](#mods)).
- **Detail pane** at 100 columns or wider: the file's frontmatter `description` (skills,
  agents, commands, rules), then harness, type, state, staged, path, cost, since,
  mechanism, and what it is shared with.
- **Search bar** (🔍; plain `/` on terminals that cannot draw emoji: the Linux console, the legacy
  Windows console, non-UTF-8 locales) under the tabs, always visible.
- **Per-harness colors** on the harness column, matching `ai-accounts list` (claude orange on
  256-color terminals, codex cyan, agy blue, grok yellow, vibe green, copilot red, opencode magenta).
- **Key-chip footer** and a `?` help overlay. A spinner shows on stderr while the list loads
  (only on a TTY; off with `NO_COLOR`). The mouse wheel scrolls (xterm alternate-scroll mode).

| key | action |
|---|---|
| `Space` | toggle the highlighted row (live / parked) and stay on it (while searching, the first `Space` separates search terms and a second one toggles) |
| `Tab` | toggle the highlighted row and advance to the next |
| `Enter` | apply every staged change (with `--dry-run`: show the plan) |
| `Esc` / `Ctrl-C` | cancel; nothing is applied |
| `↑` `↓` `PgUp` `PgDn` `Home` `End`, `Ctrl-P` / `Ctrl-N` | move |
| `/` | start a search; any other non-command letter starts one too. Terms are ANDed, case-insensitive, and match the harness/type/name, the group (`mod` or `mods` finds every mod) and the path; when nothing matches, letters-in-order (fuzzy) is tried: `ctxmd` finds `context-md`, and the bar says `≈ fuzzy match` |
| `Backspace` / `Ctrl-U` | delete one character / clear the filter |
| `←` `→` | switch harness tab (also while typing a filter); `0` = All, `1`-`9` = that tab, `h` = next tab |
| `t` | cycle the type filter |
| `s` | cycle sort: name, cost (biggest first); applies within each type group |
| `p` | profiles: stage a saved one, or save the live state (see below) |
| `?` | show the key list |
| `a` / `Ctrl-A` | toggle every visible row |
| `n` | toggle every row on this row's harness with the same name, of any type (skill, command, MCP, ...), shown or filtered out; like `disable all <name>` |

The command keys (`t`, `s`, `p`, `h`, `a`, `n`, `0`-`9`, `?`) act only while no filter is active.
Press `/` first to type a filter that begins with one of them; once the filter is non-empty,
every letter just types.

The start view (sort, harness tab, type filter) comes from the `picker_sort`, `picker_harness`
and `picker_type` [settings](#settings--config-menu). A profile skips harnesses that are hidden
and counts those items as `hidden by settings`; `ui --harness <name>` shows that harness even
when it is switched off.

`p` opens a prompt over the list of saved profiles: type a number or name (or
`apply <name>`) and Enter to **stage** that profile's changes (only the items it
mentions; ones this machine lacks are skipped), then Enter in the picker applies
them like any other change, so `ui --dry-run` previews a profile and a stray
`p` changes nothing. `save <name>` writes the live state (not your staged changes) as
a profile, with the same name rules and project scope as `profile save`; it is
off under `--dry-run`. Esc closes the prompt.

Nothing happens while the picker is open. Changes are staged, the screen is
torn down, and only then do the real operations run, so their output (which
companion files moved, which were kept because they are shared) is readable
instead of fighting curses for the terminal.

Built on stdlib `curses`, so there is nothing to install on macOS and Linux
(on Windows, `pip install "agent-toggle[windows]"` pulls `windows-curses`).
Without curses, `ui` falls back to a numbered menu with the same staging and the
same result. It is grouped by type, with the same glyphs and color: type row
numbers (`1 3 5-7`) to tick or untick, `n <number>` to toggle every type with that
row's name, `/text` to filter (`/` alone clears it),
`s` / `h` / `t` to sort and cycle the harness and type filters, `p` to list profiles,
`p <number|name>` to stage one and `p save <name>` to save the live state, `a` to
apply, `q` (or end of input) to cancel.

### Mods

A mod is a claude plugin whose `hooks/hooks.json` has a top-level `modules` key, for example
`{ "modules": ["./register.tsx"] }`: it loads `.mjs` / `.tsx` code that can draw UI (a pane,
a band above the prompt, buttons) and change Claude Code's behaviour. A plugin with no
`hooks.json`, or with only ordinary hooks (shell commands such as `PreToolUse`), is a plain
plugin: it ships skills, commands, agents, MCP servers or hooks and draws nothing.

- The check reads the install path `claude plugin list` reports and every cached version
  under `~/.claude/plugins/cache/<marketplace>/<name>/`, so a parked mod is still a mod.
  To list the installed mods by hand:

  ```sh
  grep -l modules ~/.claude/plugins/cache/*/*/*/hooks/hooks.json
  ```

- The picker lists mods under **Mods**, right after Plugins. `t` cycles to it, the
  `picker_type` setting can open on it, and a search for `mod` or `mods` finds every mod.
  The detail pane shows its type as `mod (plugin)`.
- A mod is still a plugin everywhere else: it toggles through `claude plugin
  enable/disable`, `disable plugin <id>` reaches it, profiles store it as `plugin`, and
  `list` / `cost` show it with the plugins (`cost --json` marks it `"mod": true`).

The chrome (title, tabs, footer, help, detail labels) is translated when `language` is
`zh-TW`; item names and paths are never translated.

## Settings & config menu

```sh
agent-toggle config           # or: agent-toggle --config
agent-toggle config --json    # every setting with its value and source, plus the package version; the token is masked
```

`config` opens the settings menu: curses on a terminal, a numbered list otherwise. The title
shows the package version. Each change is saved the moment you make it. On a numbered list,
end of input, `q` or an empty line exits `0`
(safe in CI; note it reads piped digits, so an open stdin pipe waits).

| group | rows |
|---|---|
| General | Check for updates, Color, Logo, Language, Default harness |
| Harnesses | one On/Off per harness, with `found ~/.x` or `not on this machine` |
| Picker | default sort, harness filter, type filter |
| Notifications | Telegram bot token (masked, last 4 characters shown), Telegram chat ID |
| Tools | Health check (`doctor`; then asks y/n per fix it can run), Install shims, Undo last change (asks y/n), Send test message, Sync CI secrets (asks y/n); their output shows in an overlay |

Keys: `↑` `↓` move (wraps, skips headings), `←` `→` cycle a value, `Enter` / `Space` change or run
the row, `r` reset the row, `R` reset everything (asks y/n), `q` / `Esc` / `Ctrl-C` quit. In an
inline text field `Enter` commits, `Esc` cancels and `-` then `Enter` clears. A dim `env` tag
marks a row whose value is overridden by an environment variable. The Language row switches the
UI live.

Settings live in `~/.agent-toggle/config.json` (mode `0600`, written atomically; keys the
tool does not know are kept):

| key | values | default | env override |
|---|---|---|---|
| `update_check` | `true` / `false` | `true` | `AGENT_TOGGLE_UPDATE_CHECK` |
| `color` | `auto` / `always` / `never` | `auto` | `AGENT_TOGGLE_COLOR` |
| `logo` | `color` / `mono` / `animated` / `off` | `animated` | `AGENT_TOGGLE_LOGO` |
| `language` | `en` / `zh-TW` | `en` | `AGENT_TOGGLE_LANG` |
| `default_harness` | a harness name | `claude` | `AGENT_TOGGLE_DEFAULT_HARNESS` |
| `harness.<name>` | `true` / `false`, for `claude` `codex` `grok` `opencode` `openclaw` `copilot` `vibe` `devin` `agy` | `true` | |
| `picker_sort` | `name` / `cost` | `name` | |
| `picker_harness` | `all` or a harness name | `all` | |
| `picker_type` | `all`, a type, or `mod` | `all` | |
| `telegram_chat_id` | a number, `-100...` or `@channel` | unset | `TG_CHAT_ID` |

The bot token is not in this file: it is in the OS keystore through telegram-kit
(`TG_BOT_TOKEN` overrides it). An environment variable always beats the file; an
invalid value in either is ignored with one warning on stderr.

- `harness.<name>` off hides that harness from `ui`, `list`, `status` and `cost` (`list`, `status` and
  `cost` print a dim `N hidden by settings` note, and `--json` carries it in `warnings`; an item shared with a harness that is still
  on stays listed) and `install-shims` skips it; an explicit
  `--harness <name>` still reaches it, `ui --harness <name>` included.
- `default_harness` is what `disable` / `enable` act on without `--harness`, even when that
  harness is switched off (with one warning on stderr);
  `disable` / `enable --project` without `--harness` still default to `claude`.
- `language` translates the config menu, help, update prompt and picker chrome; command output stays English.
- Bare `config` works without the `[telegram]` extra; `config test` and `config sync-ci` are
  unchanged and need it (exit `4` without it).

## Update check

On every invocation (unless disabled) agent-toggle checks PyPI for a newer release. It starts
before the command and is offered after it, on every exit path; it overlaps the command and waits at most 0.8 s afterwards.

- **On a terminal** (stdin and stderr are TTYs, no `--json`): a panel on stderr, "agent-toggle X
  is available (you have Y)", with `Update now`, `Skip` and `Skip until next version`, a release
  notes link, and `↑` `↓` `Enter` `q`. `Update now` runs `uv tool upgrade agent-toggle`; if that
  fails you get a yellow warning with the command to run yourself.
- **Off a terminal**: two stderr lines, `agent-toggle X is available (you have Y)` and
  `  uv tool upgrade agent-toggle`.
- It never changes the exit code or stdout, so `--json` output stays one document.

It is one `GET https://pypi.org/pypi/agent-toggle/json` on a background thread with a 0.8 s
timeout, cached for 10 minutes in `~/.agent-toggle/update-check.json`. Nothing is sent beyond a
normal HTTP request with the User-Agent `agent-toggle-update-check`. See `SECURITY.md`
for the hardening. To turn it off, set `AGENT_TOGGLE_UPDATE_CHECK=0` or the `update_check` setting
to off (the settings menu's "Check for updates").

## Help

```sh
agent-toggle help               # grouped overview
agent-toggle help config        # one command; same as: --help config, config --help
```

Help is grouped as Toggle (`ui`, `disable`, `enable`, `undo`, `profile`), Inspect (`status`,
`list`, `cost`, `doctor`) and Setup (`config`, `install-shims`, `migrate`), with the global
options, examples and exit codes. `help <command>` shows that command's usage, arguments,
options and examples; `help help` is the overview, `help version` shows how to print the version,
and an unknown command exits `2`. It honours `--color` and `NO_COLOR`, and is
translated when `language` is `zh-TW`.

The overview opens with the agent-toggle logo (cyan to blue to magenta): the large one from 97
columns, a compact one from 49, none below that. Piped output gets the plain glyphs, a stream
that cannot encode them gets none. The `logo` setting picks `color`, `mono` (one colour),
`animated` (one light sweep, only on a colour terminal) or `off`. The config menu shows the same
logo, centred, when the window is tall enough to fit the whole menu beside it; there,
`animated` sweeps on entry and again after every 5 s without a key, and any key stops it.

## What each harness supports

| harness | home | skill | agent | command | rule | plugin | mcp |
|---|---|:-:|:-:|:-:|:-:|:-:|:-:|
| claude | `~/.claude` | ✓ | ✓ | ✓ | ✓ | ✓ (mods too) | ✓ `~/.claude.json` |
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
`opencode.json` → `skills.paths` (absolute, `~`, `$HOME/...`
and `${HOME}/...` entries; read from `opencode.json`, else `opencode.jsonc`;
relative entries are skipped, since OpenCode resolves them against the session
directory). OpenCode also always scans `~/.claude/skills` and `~/.agents/skills`,
so claude's skills count as shared with it. Such a directory is **one** item, filed under
its owner (the harness whose home really holds it): it is
parked once, tracked once, and every row reports `shared_with`, the other
harnesses it also affects. `status` prints `shared dir with: ...`.

If the live directory carries `.synced-from-*` markers, a sync job may
re-create what you parked; `status` and `disable` warn about it. Park in the
source harness instead. The warning is printed once per real directory, naming
every harness that views it.

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
`2`) -- that is user scope. `list`, `enable --all`, `cost` and `ui` run the same
checks. `cost --project` prices that project's items only (no plugins), and
`ui --project` stages and applies in project scope (its `p` key saves and applies
project profiles).

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
  a BOM, comments or trailing commas are refused. `disable` cuts only that
  server's entry, leaving every other byte as it was, and saves a backup at
  `mcp-backups/<sha8>__claude__<name>.json` (mode `0600`) holding only that entry
  -- its own headers, never another server's. `enable` puts the entry's bytes
  back into the current file (an unchanged file comes back byte for byte; after
  other edits it goes after its old neighbour, else at the end, indented like the
  file) and checks that nothing else changed.
- A move across filesystems copies into a temp dir beside the target, renames it
  into place, then deletes the source, so a killed run leaves either the source
  intact or the target complete, and the next run settles it (see "Where state
  lives"). `enable` removes the emptied `parked/<sha8>/*-disabled` and
  `parked/<sha8>` dirs; a dir that still holds anything is kept.
- `status` prints one `project <dir>` line per project holding parked items.
  A project with only `.mcp.json` has its live servers listed too in
  `profile save --project`, next to its parked ones.

## Doctor

```sh
agent-toggle doctor [--harness H] [--json]
```

The check is read-only: no lock, no state write-back, no `claude` CLI call. For each
installed harness it compares the live layout with the table row (expected
dirs and config keys), then cross-checks `state.json` against disk (parked
item present, origin dir present, backup present, project dir present, entry
passes the same tamper checks `enable` runs, companion files present and not also
live, modes no looser than `0600` / `0700`). Rows (`action: doctor`) carry a status:

| status | meaning |
|---|---|
| `ok` | matches |
| `absent` | a dir, config file or key the row expects is not there (an MCP file never created, a missing `mcpServers` key) -- informational, exit `0` |
| `note` | worth knowing: shared dir, orphan backup, JSONC `openclaw.json` / `opencode.json`, a `--harness` that is not installed |
| `unverified` | no version could be read, or the row has nothing to check |
| `warn` | loose file modes; a legacy `<dir>-disabled/` park dir in a harness home (fix: `agent-toggle migrate`); a parked item or companion file with no state entry; a leftover `parked/<sha8>` dir that still holds files (an empty one is ignored); an op a killed run left in flight that the next change settles (`pending: done` / `undone`) |
| `error` | needs fixing: a config that exists but is unparseable or unsupported (`layout changed`), a state entry whose files (companions included) are gone or fail the tamper checks, a companion both live and parked, a flag re-enabled outside the tool, an op a killed run left in flight that needs you (`pending: stuck`, with the exact fix) |

Only `error` makes the exit code `1`; each problem row names the command that
fixes it. `--harness X` for a harness that is not installed is a `note` (exit
`0`).

A harness item parked with no state entry also carries `orphan`, and its fix
follows from it: `identical` (the live copy has the same content: delete the
parked copy), `differs` (a different live copy exists: compare, keep one), or
`parked-only` (move it back, then `disable` it so the state records it).

### Fixing

`install-shims` (and so `./install.sh`) runs the same check silently after
writing the shims and asks only about these fixable problems, each shown first.

On a terminal (stdin and stdout both a TTY, no `--json`) doctor then asks
`(y/n)` for each problem it can fix without a judgment call, and runs each yes
under the run lock; anything else keeps its `fix:` hint for you. A fixed row's
status becomes `fixed` and no longer counts toward the exit code; a failed fix
adds an `error` row. Piped, `--json`, or run by an agent through a shim, doctor
stays read-only.

| problem | fix it offers |
|---|---|
| loose mode on the state dir, `state.json` or a backup | `chmod 700` / `chmod 600` |
| backup with no state entry, server configured again in its harness | delete the backup (not offered while the server is missing, nor for project backups) |
| flag re-enabled outside the tool | `enable` the item, which clears the stale entry |
| origin dir gone | recreate it, then `enable` the item |
| op a killed run left in flight (`pending: done` / `undone`) | settle it now, as the next change would |
| orphan parked item, `identical` | delete the parked copy |
| orphan parked item, `parked-only` | move it back (restores it) |

Not offered: `differs` orphans, missing parked items or backups, a gone project
dir, tamper refusals, `pending: stuck`, companion conflicts, unparseable configs.

## Assumed formats

Two config shapes came from the design survey (`docs/DESIGN.md` §4 / §11). Both
were since seen on one real install each (openclaw 2026.7.1-2, opencode 2.0.22; see
`docs/harnesses.md`), but the tool still treats them defensively:

- `openclaw.json`: `skills.entries.<name>.enabled` and
  `plugins.entries.<name>.enabled`
- `opencode.json`: `mcp.<name>.enabled`

The edit changes one boolean token and nothing else, is verified after writing
and rolled back on any mismatch (bytes and file mode). JSONC (`//` and `/* */`
comments, trailing commas) is edited the same way: only the flag token changes,
comments and commas are kept. JSON5 (unquoted keys, single quotes, hex) and files
that repeat a key are **refused**, never rewritten; a leading BOM is kept as is; a
missing key is refused, never invented. An openclaw skill
uses the flag only when `skills.entries.<name>` already exists, otherwise its
directory is moved. The state entry (with the flag's previous value) is saved
before the flag is written, so a run killed in between is settled by the next
run, never guessed (see "Where state lives"). Please report a real install that
differs.

## Safety checks

- `enable` refuses a state entry whose `origin`, `parked_at`, backup or flag
  file is outside its harness home, project or `~/.agent-toggle`, or holds `..`:
  an error row `refused: <reason>`, nothing moved.
- Every edit of a JSON or TOML config is verified after writing (still parses,
  only the target changed) and rolled back, bytes and mode, on failure. An
  invalid codex `config.toml` makes an MCP edit fail and roll back instead of
  being rewritten. A codex `config.toml` edit also fails (and is left as the other
  tool wrote it) if the file changed between read and write, and CRLF files keep their
  line endings. On Python 3.10 (no `tomllib`) the edit is parse-checked by a stdlib structural
  validator (`agent_toggle/toml_check.py`, differentially tested against `tomllib`),
  on top of the textual check that only the one block changed.
- Profiles and project dirs are validated like command-line input.

## Where state lives

All of it in `~/.agent-toggle/` (mode `0700`), never as marker files next to the targets —
your `git status` in your own project must not change because of our
bookkeeping.

| file | contents |
|---|---|
| `state.json` | current disabled list (schema v3; atomic write, mode `0600`), saved before each flag write or dir move with the op in flight under an optional `pending` key, and after each MCP / plugin item |
| `lock` | held by `disable` / `enable` / `enable --all` / `undo` / `profile apply` / `migrate` / `ui` for the whole batch; a second run waits 5 s then exits `3` (stale after 10 min *and* its PID is gone; a live batch refreshes it per item; the error says when its PID is gone, i.e. a killed run left it) |
| `log.jsonl` | one line per operation (mode `0600`), see below |
| `mcp-backups/` | `<harness>__<server>.json`, or `<sha8>__<harness>__<server>.json` for a project `.mcp.json` (mode `0600` -- may hold auth headers) |
| `companions/` | parked exclusive helper files |
| `parked/user/<owner>/<dir>/` | user-scope items, e.g. `parked/user/claude/skills/<name>` (`<owner>` = the harness whose home holds the dir; a dir shared by several harnesses parks once, under its owner); an absolute OpenCode `skills.paths` dir parks under `parked/user/opencode/ext-<sha8>/` (`<sha8>` of its resolved path). Nothing is ever parked inside a harness home |
| `parked/<sha8>/` | items parked by `--project` (`<sha8>` = first 8 hex of the SHA-1 of the resolved project dir) |
| `profiles/` | `<name>.json` profiles (dir `0700`, files `0600`) |
| `config.json` | [settings](#settings--config-menu) and the Telegram chat id (mode `0600`, atomic write, unknown keys kept) |
| `update-check.json` | the [update check](#update-check)'s 10-minute cache (mode `0600`, atomic write) |

A killed run loses at most the one item in flight. If that was a flag write or a
dir move (with its companions), its `pending` record (the full entry, a flag's
previous value included) lets the next `disable` / `enable` / `enable --all` /
`undo` / ... finish it (it reached disk: recorded) or drop it (it did not), with
a warning and a `recovered` log row (which `undo` does not reverse). `status` and
`doctor` report it, with `agent-toggle enable ...` when the item ends up
disabled. The one case left to you is a cross-filesystem move killed between its
two renames: both copies are complete, and the report names the `diff -r` to
check and the `rm -rf` that keeps either one. MCP and plugin items are saved
per item (when the next one starts), without a `pending` record (a project `.mcp.json`
edit has one): a kill inside that window
leaves the change unrecorded (an MCP server's backup stays in `mcp-backups/`).
A killed run also leaves its `lock`; the next run's error says so.

Each `log.jsonl` row is `{ts, harness, type, name, action, result, batch,
project, scope, detail}`. `batch` is one id per run (what `undo` reverses);
`project` is `null` and `scope` is `user` outside project scope (a claude
local-scope MCP row has `scope: local` and its working directory in `project`).
Older rows without `batch`/`harness` cannot be undone.

### Upgrading the park layout

**Breaking:** older versions parked user-scope items in a sibling dir inside
the harness home (`~/.claude/skills-disabled/`, `~/.codex/prompts-disabled/`,
...). Claude Code loads `commands/` and `rules/` recursively, so a park dir
cannot nest inside a live dir either; items now park under
`~/.agent-toggle/parked/user/`. Run once after upgrading:

```sh
agent-toggle migrate
```

It moves every child of each `<dir>-disabled/` into its new park dir with the
same crash-safe move `disable` uses, repoints the matching `parked_at` entries
in `state.json`, and removes the emptied old dir. Items with no state entry
move too (they stay disabled). A name that already exists at the destination is
refused and left where it is (exit `1`, the row names it); resolve it and re-run.
A second run changes nothing and says so. Until then `enable` refuses an item
still in an old dir with `run: agent-toggle migrate`, and `status` / `doctor`
list each old dir with the same hint. Afterwards, `install-shims` offers to
remove the old `<dir>-disabled/` lines from a harness-home `.gitignore`.

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

**2. A park dir that is not gitignored gets a warning.** Parked items live in
`~/.agent-toggle/parked/`, outside every harness home, so a harness repo
(`~/.claude` as a dotfiles repo) never sees them. Only when the park dir itself
sits inside a git work tree (a tracked `$HOME`) and is not ignored there does
`disable` warn, once per run, with the `.gitignore` line to add; `status` tags
each type `[NOT gitignored]` in that case. Without it, every disable would leave
deletion lines in `git status`.

## Paths are resolved before comparison

`~/.claude` is often a symlink, and on macOS `/var` is one. Comparing
unresolved paths makes every companion look like it lives outside the harness.
Both sides are resolved first.

## Claude Code pane mod

A Claude Code mod in `mod/` that shows the picker as a pane inside Claude (not to be confused
with the [Mods](#mods) group the picker lists). It is driven by the installed `agent-toggle`
CLI (`cost --json`, `disable|enable --json`) and works for the claude harness only; the curses
`agent-toggle ui` stays the cross-harness UI.

Install it with `/plugin install agent-toggle --marketplace weskao/agent-toggle` (see
[Install](#install)); `claude plugin update agent-toggle@agent-toggle` picks up a new release. To
run your own checkout instead (edits reload as you save):

```sh
claude --plugin-dir ./mod    # then run /agent-toggle inside Claude
```

- It looks like the `ui` picker: a title with the live-token total, a search line, rows under
  type headings with counts, green `●` live / yellow `○` parked, tokens and a bar per row (a
  parked row shows `(N)`, what restoring it would load), the cursor row in reverse video, and a
  key-chip footer. The list scrolls with the cursor.
- The mouse wheel over the list moves the cursor, as in the terminal picker; no click needed.
- Click the list once so it takes keys (Esc hands the keyboard back to the prompt). Then `↑` `↓`
  `PgUp` `PgDn` `Home` `End` (also `Ctrl-P` / `Ctrl-N`) move; `Space` or `Enter` toggles the row,
  `Tab` toggles and moves on; `/` or any other letter starts a search (terms ANDed over
  `type/name` and the group, letters-in-order if nothing matches; a second `Space` toggles);
  `s` sorts by name or cost; `Ctrl-U` clears the search. A click moves the cursor.
- A toggle applies at once (no staging, no Enter to apply); the toast says `ok`, or that the change
  takes effect in a new session, or the error. While a toggle runs, further keys are ignored.
- Not in the pane: harness tabs (claude only), and the `t` `p` `a` `n` keys. Where Claude draws no
  such pane (the mobile app, VS Code) it is a plain list of buttons instead.
- Needs `agent-toggle` on `PATH`.
- On Windows `agent-toggle` must resolve to a real executable on `PATH` (a pip or uv install
  provides `agent-toggle.exe`).

The design is in `docs/MOD.md`.

## Design and roadmap

Architecture, harness survey, cost model, known gaps and the phased roadmap
toward a public multi-OS release live in `docs/DESIGN.md`. The design for the
Claude Code pane mod (the picker inside Claude, driven by this CLI) is in
`docs/MOD.md`. Release notes are in `CHANGELOG.md`.

## Tests

```sh
python3 -m unittest discover -s tests -v
ruff check .
```

stdlib `unittest`, no fixtures, no network. Every test runs against a
throwaway temp `HOME` and a stubbed `claude` CLI.

The pane mod has its own checks (they need the `claude` CLI, so CI does not run them):

```sh
claude plugin validate mod
claude plugin test mod
```

`python3 tools/wheel_smoke.py` builds the wheel, installs it without extras into a
fresh venv (what a PyPI user gets) and runs the CLI against a temp `HOME`; CI runs
it on macOS, Linux and Windows.

## CI notifications

`.github/workflows/ci.yml` can send a Telegram message when the test matrix fails on a
push (a lint-only failure does not page). Alerts never fire for pull requests or green
runs. The message names the repository, branch, 7-character commit and a link to the run.

Set it up once, with the `telegram` extra installed (see [Install](#install)):

```sh
agent-toggle config            # settings menu: Notifications has the bot token (hidden) and chat id
agent-toggle config test       # send one test message
agent-toggle config sync-ci    # set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID on the repo
```

In the [settings menu](#settings--config-menu) an inline field commits on `Enter`, cancels on
`Esc`, and clears on `-` then `Enter`; the Tools group also has Send test message and Sync CI
secrets (the same as `config test` / `config sync-ci`, and they need the extra). The bot token
is kept only in the OS credential store (macOS Keychain, Linux Secret Service, Windows DPAPI)
through telegram-kit, never in a file or on a command line, and is shown masked. With no
credential store it refuses to store the token; set `TG_BOT_TOKEN` in the environment instead
(`TG_CHAT_ID` likewise for the chat id). The chat id (`telegram_chat_id`) is ordinary
configuration in `~/.agent-toggle/config.json`: a number, `-100...` for a group, or an
`@channel`. `config sync-ci` needs the GitHub CLI (`gh`) signed in; it passes both values on
stdin, and `--repo OWNER/REPO` / `--dry-run` work as elsewhere.

To skip the tool, set the secrets by hand (each command prompts for the value):

```sh
gh secret set TELEGRAM_BOT_TOKEN
gh secret set TELEGRAM_CHAT_ID
```

With either secret unset (a fork, or a clone you have not configured) the notify job
prints a `::notice::` and exits 0, so it never adds a second red X.

## Releasing

A release is a git tag `vX.Y.Z` (where `X.Y.Z` must match `agent_toggle.__version__`).
Bump `version` in `mod/.claude-plugin/plugin.json` to the same value by hand; nothing checks it,
and without it `claude plugin update` does not hand marketplace installs the new mod.
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

Disabling is a **local** decision: parked items live in `~/.agent-toggle/`,
outside every harness home, and do not sync.
But a disappearance under `skills/` is itself a tracked change, so committing
it means other machines lose those skills on pull. The content stays in git
history — `git checkout <commit> -- skills/<name>` brings it back. Don't
commit those deletions if you don't want them to travel.
