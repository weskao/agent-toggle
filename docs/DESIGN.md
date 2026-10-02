# agent-toggle — Design & Roadmap

Living design document. Updated as phases land; the decisions log at the end
records why each choice was made so it is not re-argued.

- Status: **draft for review** (2026-10-02)
- Scope: public, multi-harness, multi-OS tool for parking AI-agent resources
  to cut per-session token cost. Nothing is ever deleted.

---

## 1. Goal

Every AI coding harness injects a fixed block of text at session start:
skill names and descriptions, agent descriptions, command lists, auto-loaded
rules, MCP tool schemas. On a machine with ~100 skills, ~30 agents and ~50
commands that block is thousands of tokens on every turn, paid before the
user types anything. Most of it is irrelevant to the task at hand.

agent-toggle lets a person (directly, or through an AI agent) **turn
resources off and back on reversibly**, so the injected block only contains
what the current work needs.

Success criteria for the public release:

1. One install command works on macOS (Linux and Windows follow) and puts a
   usable `agent-toggle` on PATH.
2. Every supported harness is driven by the same commands; unsupported
   (harness, type) pairs fail loudly, never silently.
3. The user can see **what each resource costs** before deciding what to park.
4. A disable is always undoable with `enable`, including MCP auth fields and
   companion files.
5. Nothing the tool does leaves marker files in the user's own repos or
   harness directories; all bookkeeping is in `~/.agent-toggle/`.

Non-goals: deleting or installing resources, syncing resources between
harnesses, syncing state between machines or through any account/cloud
service, editing a harness's own settings beyond the fields needed to
toggle.

---

## 2. Current state (phase 0 + phase 1 landed on `phase-1`)

A stdlib-only Python package (`agent_toggle/`, console script `agent-toggle`,
thin `agent_toggle.py` wrapper for checkouts) plus a curses picker and one
skill shim written into each harness by `agent-toggle install-shims`
(`install.sh` is a wrapper). Stdlib `unittest` suite; CI matrix pending the
first push.

| area | state |
|---|---|
| harnesses | claude, codex, grok, opencode, openclaw — one `Harness` record each in `harnesses.py` |
| types | skill, agent, command, rule (move to `*-disabled/`), plugin (claude CLI only), mcp (remove + verbatim backup) |
| safety | `safe_move()` guards the rename trap; companion files parked only when exclusive; park dirs checked for gitignore; symlinks and broken links handled; `0600` state/backups, lock per batch |
| state | `~/.agent-toggle/state.json` (schema v3, atomic write), `log.jsonl`, `mcp-backups/`, `companions/`, `lock` |
| MCP scopes | Claude user + local scope (project path recorded); codex and grok through the TOML backend; claude.ai connectors parked per existing project (`disabledMcpServers`) |
| cost | `cost` command and picker column (chars / 4 estimate) |
| UI | curses picker: cost column, sort, harness/type filters, `/` text filter, plugins included, `--dry-run` |
| AI access | `SKILL.md` shim using `--json`, written by `install-shims` |

### What is strong and must be kept

- The move-safety discipline (`safe_move`, `prune_empty`, resolved paths).
- Verbatim MCP backups (raw JSON entry / raw TOML block) instead of
  re-serialising through a CLI that drops auth fields.
- Shared-companion veto.
- Central state; no sidecar files.
- Loud failure for unsupported pairs.

### Gaps against the goal (as surveyed before phase 0; the first rows are now closed)

| gap | why it matters |
|---|---|
| **no cost information** | The tool's purpose is token reduction, yet it cannot say what anything costs. Users park by guesswork. |
| OpenCode unsupported | Required harness. Its skills dir is an alias of another harness's dir (see §4), so naive support would double-toggle. |
| grok MCP marked unsupported | Its `config.toml` uses the same `[mcp_servers.x]` tables as codex; the existing TOML backend already covers it. |
| rules not a type | `~/.claude/rules/*.md` is injected whole, every session — the heaviest per-file cost on a typical setup. |
| project-scope resources ignored | A repo's own `.claude/skills` and `.mcp.json` count too. |
| plugins absent from picker | `claude plugin list --json` now returns a stable array (`id`, `version`, `scope`, `enabled`, `installPath`), so the blocker is gone. |
| personal paths in README and shim flow | Blocks public use. |
| no packaging, license, CI | Blocks public use. |
| curses-only UI | `curses` is absent from Windows Python. |
| no lock on `state.json` | Two sessions (human + agent) toggling at once corrupt state. |
| backups hold secrets | MCP entries contain `headers`/tokens; files are written with default permissions and the location is documented publicly. |

---

## 3. Where the tokens go

Which text each harness injects at session start, and whether the tool can
reach it. This is the basis of the cost model (§5.6) and of which resource
types are worth adding.

| injected text | typical weight | togglable by | type |
|---|---|---|---|
| skill name + description (frontmatter) | 30–150 tok each; descriptions with trigger phrases run to 300+ | moving the skill dir | `skill` |
| agent name + description | 30–120 tok each | moving the file | `agent` |
| command name + description | 10–60 tok each | moving the file | `command` |
| rule files, injected in full | 100–800 tok **each** | moving the file | `rule` (new) |
| MCP tool schemas | 100–500 tok **per tool**, ×N tools per server | removing the server (with backup) or a native `enabled` flag | `mcp` |
| plugin bundles (skills + agents + commands + MCP inside) | sum of the above | native plugin disable / plugin table flag | `plugin` |
| `CLAUDE.md` / `AGENTS.md` and includes | varies | not ours — the user's prose | — |
| hook stdout (SessionStart etc.) | varies | editing `settings.json`, which this tool does not do | — |

Two honest caveats the cost model must carry:

- Some harnesses now **defer** tool schemas (Claude Code loads MCP schemas
  on demand via tool search), so an MCP server's real per-session cost may be
  only its tool *names*. Cost weights are therefore per harness, not global.
- The tool can only estimate static text. Measured numbers come from the
  harness itself; the estimator exposes `--json` so a measured total can be
  compared against the estimate, and documents its formula.
- Harness prompt caching lowers the *price* of the static block after the
  first turn, not its context-window footprint. `cost` reports tokens, not
  dollars.

---

## 4. Harness survey

Surveyed on one macOS machine with all eight harnesses installed. Paths are
`~`-relative. "flag" means the harness has a native boolean that enables or
disables the item without moving files — always preferred when present.

| harness | home | skills | agents | commands | rules | plugins | MCP config | notes |
|---|---|---|---|---|---|---|---|---|
| **claude** | `~/.claude` | `skills/<name>/SKILL.md` | `agents/*.md` | `commands/**/*.md` | `rules/*.md` (injected whole) | `claude plugin enable/disable`, `claude plugin list --json` | `~/.claude.json` top-level `mcpServers` (user) and `projects.<dir>.mcpServers` (local); repo `.mcp.json` (project) | fully supported today except rules, project scope, plugins in picker |
| **codex** | `~/.codex` | `skills/` | `agents/` | `commands/`, `prompts/*.md` | `rules/default.rules` is a **permission** rules file, not prompt text — not a token cost, not a target | `[plugins."name@marketplace"]` TOML tables; nested `.mcp_servers.*` sub-tables belong to the plugin | `[mcp_servers.<name>]` TOML blocks (+ `.env`, `.tools.*` sub-tables) | `prompts/` is a second command dir — add as a type alias |
| **grok** | `~/.grok` | `skills/` | — | — | — | `[plugins]` table + `installed-plugins/registry.lock` | `[mcp_servers.<name>]` TOML blocks (+ `.headers`) | **MCP is togglable with the existing TOML backend**; currently declared unsupported by mistake |
| **openclaw** | `~/.openclaw` | `skills/` **and** `openclaw.json → skills.entries.<name>.enabled` | `agents/` | — | — | `openclaw.json → plugins.entries.<name>.enabled` + `plugins.allow` list | `state/openclaw.sqlite` — refuse | skills and plugins have a **native flag**; prefer flipping it over moving dirs (the dir move still works as fallback for skills with no entry) |
| **opencode** | `~/.config/opencode` (XDG) | `opencode.json → skills.paths[]` — on the surveyed machine it points at **`~/.codex/skills`** | — (agents are config entries under `agent.*`, not files) | `command/*.md` (frontmatter `description`, body) | — | `plugins/` dir + `opencode.json → plugin[]` (URLs / `file://` paths) | `opencode.json → mcp.<name>.enabled` **native flag** | skills are an alias of another harness's dir: toggling must dedupe by real path and report "also affects codex" |
| **copilot** | `~/.copilot` | `skills/` (dir per skill) | `agents/*.md` | — | `instructions/` (`AGENTS.md`, docs) | `installed-plugins/` | `mcp-config.json → mcpServers` — JSON-key backend, different file than claude | `config.json` is JSONC and self-described as machine-managed: **never edit it**. Skill dir carries `.synced-from-claude*` markers — a sync job may overwrite parked state |
| **vibe** | `~/.vibe` | `skills/<name>/SKILL.md` (agents appear as `agent-*` skills) | — | — | — | — | none found | `config.toml` holds UI settings only. Skill set looks synced from another harness (same marker pattern) |
| **devin** | `~/.devin` | — | — | — | `DEVIN.md` | — | — | `config.json` holds version, org id, shell, theme only. **Nothing togglable locally**; resources live cloud-side. Adapter = explicit "not applicable" entry so it fails loudly |
| **agy** (Antigravity CLI) | `~/.antigravity` (+ `~/.gemini/antigravity-cli`) | not present on the surveyed machine | — | — | — | — | not present | CLI exposes `--disable-slash-commands` ("slash command and skill expansion"), so skills exist in some layout. Adapter deferred until a skills dir is observed on a real install; the harness table entry is added with `types=()` so `status` lists it as "found, nothing supported yet" |

### Cross-harness findings that shape the design

1. **Aliasing.** OpenCode reads skills from codex's directory. Copilot and
   vibe carry sync markers from claude. One physical directory can serve two
   harnesses, and a parked item may be re-created by a sync job. The tool must
   (a) key every move by *resolved real path*, (b) report every other harness
   that resolves to the same path, and (c) warn when a `.synced-from-*`
   marker exists in the live dir.
2. **Four mechanisms, not N harnesses.** Every (harness, type) pair uses one
   of: **move** (park a dir or file), **flag** (flip a boolean in a config
   file), **remove-with-backup** (MCP entries that have no flag), **native
   CLI** (`claude plugin`). New harnesses are table rows choosing a mechanism;
   new mechanisms are rare.
3. **Config formats are three:** JSON (claude, opencode, copilot, openclaw),
   TOML (codex, grok), SQLite (openclaw MCP — refused). JSON edits are done
   via `json` load/dump on the specific key with a verbatim backup of the
   removed value; TOML edits stay text slices (stdlib cannot write TOML).
4. **Machine-managed files are off limits** (copilot `config.json`). The
   harness row names the files the tool may touch; anything else is refused.

---

## 5. Target architecture

Principle: **data describes harnesses, a handful of small behaviours
implement mechanisms, everything else is shared.** Declarative table + three
protocols was chosen over one class per harness (8 near-identical classes)
and over entry-point plugin discovery (nobody has asked for third-party
adapters).

### 5.1 Package layout

```
agent_toggle/
  __init__.py        version
  cli.py             argparse; subcommands; --json; --harness; --project; exit codes
  harnesses.py       THE TABLE (one Harness record per harness)
  mechanisms.py      move / flag / remove_backup / native_cli — the 4 strategies
  backends/
    mcp_json.py      JSON-key MCP backend (claude ~/.claude.json, copilot mcp-config.json)
    mcp_toml.py      TOML-block MCP backend (codex, grok)
    flag_json.py     boolean flag in a JSON file (openclaw skills/plugins, opencode mcp)
    plugin_cli.py    claude plugin CLI
  fs.py              safe_move, prune_empty, realpath, gitignored, atomic_write, lock
  companions.py      reference scan, exclusive/shared classification
  store.py           state.json schema v3, log.jsonl, backups (0600), migrations
  cost.py            per-type token estimators
  profiles.py        save / apply / diff
  output.py          text and JSON renderers; one envelope for every command
  ui/
    picker.py        curses (extended)
    menu.py          numbered-prompt fallback (no curses)
  shims/             one template per harness skill/command format
tests/
  fixtures/<harness>/   minimal fake homes
  test_*.py             stdlib unittest, parametrised over the harness table
pyproject.toml       console_scripts: agent-toggle = agent_toggle.cli:main
```

Ten modules, none over ~300 lines. Current code moves into these files
largely unchanged; the refactor is a split, not a rewrite.

### 5.2 The harness record

```python
@dataclass(frozen=True)
class Harness:
    name: str
    home: Path                                   # resolved at import via platform layer
    dirs: dict[str, tuple[str, ...]]             # type -> candidate subdirs, e.g. "command": ("commands", "prompts")
    mechanisms: dict[str, str]                   # type -> "move" | "flag" | "remove_backup" | "native_cli"
    mcp: McpSpec | None                          # backend name + file + key path
    flags: dict[str, FlagSpec]                   # type -> (file, json pointer template)
    editable: frozenset[str]                     # files the tool may write; everything else refused
    aliases_from: tuple[str, ...] = ()           # config keys that may redirect a dir (opencode skills.paths)
```

`aliases_from` is currently **metadata only**: the code never reads it. OpenCode's
`skills.paths` redirect is resolved by `opencode_skill_dirs()` in
`harnesses.py`, and alias detection itself is path-based (`dir_view()` resolves
every candidate dir and groups the harnesses that land on the same real path).

Adding Copilot is one record: `dirs={"skill": ("skills",), "agent": ("agents",)}`,
`mechanisms={"skill": "move", "agent": "move", "mcp": "remove_backup"}`,
`mcp=McpSpec("json", "mcp-config.json", ["mcpServers"])`,
`editable={"mcp-config.json"}`. No new code.

### 5.3 The three protocols

```python
class McpBackend(Protocol):
    def list(self, h: Harness) -> list[McpEntry]: ...
    def remove(self, h: Harness, name: str) -> Backup: ...     # returns verbatim payload
    def restore(self, h: Harness, backup: Backup) -> None: ...

class FlagBackend(Protocol):
    def get(self, h: Harness, spec: FlagSpec, name: str) -> bool | None: ...
    def set(self, h: Harness, spec: FlagSpec, name: str, enabled: bool) -> None: ...

class CostEstimator(Protocol):
    def estimate(self, h: Harness, item: Item) -> Cost: ...     # tokens + basis string
```

`PluginBackend` collapses into `native_cli` (claude) or `FlagBackend`
(openclaw `plugins.entries`, codex/grok TOML tables via the TOML text-slice
code already written for MCP). No separate protocol.

### 5.4 Operation flow (shared by every type)

```
resolve item  ──►  check editable/aliases  ──►  mechanism.disable/enable
     │                      │                          │
     │                      └── report other harnesses sharing the real path
     └── not found → loud error                        │
                                                  record in store (locked, atomic)
                                                  append log line
                                                  render via output.py (text | json)
```

Every mutating command acquires `~/.agent-toggle/lock` (O_EXCL create, PID
inside, stale after 10 min) for the whole batch. Two concurrent runs: the
second waits up to 5 s then fails loudly.

### 5.5 State schema v3

```json
{
  "version": 3,
  "disabled": {
    "<harness>:<type>:<name>": {
      "mechanism": "move|flag|remove_backup|native_cli",
      "harness": "...", "type": "...", "name": "...",
      "origin": "/real/path", "parked_at": "/real/path",      // move
      "backup": "~/.agent-toggle/mcp-backups/x.json",         // remove_backup
      "scope": "user|local", "project": "/dir",               // claude mcp
      "flag": {"file": "...", "pointer": "...", "was": true},  // flag
      "companions": [...],
      "shared_with": ["opencode"],                            // alias report at disable time
      "at": "2026-10-02T00:00:00+0000"
    }
  }
}
```

`shared_with` and `harness` semantics: for a directory shared by several
harnesses (§4, finding 1) there is ONE entry, and its `harness` is the
**owner**, the harness whose home really holds the directory. The entry is
keyed `<owner>:<type>:<name>` whichever harness the request came through, and
`shared_with` lists the *other* harnesses that resolve to the same real path.
Only owner-keyed alias entries carry it (written at disable time; entries
without sharers omit the field). `disable` / `enable` result rows report
`shared_with` relative to the requesting harness, plus `owner`. `list` and
`cost` rows are filed under the owner and report the stored `shared_with`
(never `owner`; `[]` when unshared). `enable` through an alias finds the
owner's entry.

Project scope (`--project`, §5.8) uses the key `<harness>@<sha8>:<type>:<name>`
(`<sha8>` = first 8 hex of the SHA-1 of the resolved project dir; helpers
`make_key` / `parse_key` in `store.py`) and carries `"project": "/dir"`, so a
project entry and a user entry of the same name never collide. Flag entries carry
`"mechanism": "flag"` and a `flag: {file, pointer, was}` record; claude.ai connector
entries also store `mechanism: flag` but carry `"connector": true` and no `flag` record.
Every log row follows `{ts, harness, type, name, action, result, batch,
project, scope, detail}`; `batch` is one id per process and is what `undo`
reverses.

`store.py` migrates v2 → v3 on first load (adds `mechanism` from the
presence of `backup`/`native`/`parked_at`). Migrations are forward-only and
tested with a v2 fixture.

### 5.6 Cost model

`agent-toggle cost [--harness H] [--type T] [--json]` prints items sorted by
estimated startup tokens, with the live/parked state, so the user parks the
expensive things first. The picker shows the same number as a column.

Estimator per type (chars ÷ 4, the standard rough tokenizer-free estimate;
documented as ±25 %):

| type | text counted |
|---|---|
| skill | frontmatter `name` + `description` only — the body loads on invocation |
| agent | frontmatter `name` + `description` (+ `tools` line when present) |
| command | frontmatter `description` + `argument-hint`; filename |
| rule | **entire file** |
| mcp | per harness: if the harness defers schemas → tool names only (count from backup/listing when available, else flat 20 tok/server); otherwise a flat 300 tok/tool with the tool count from the harness's own listing when it offers one |
| plugin | sum of its bundled items, read from `installPath` |

Output carries a `basis` string per row ("description 412 chars", "6 tools ×
300 flat") so the number is explainable. In `--json` the basis is the row's
`detail` (there is no separate `basis` field); item rows add `enabled`,
`tokens`, `would_save`, `chars`, `shared_with`, and the final `total` row
carries `total_tokens`, `saved_tokens` and `formula` (the estimator rules as
data), so an external measured total can be compared. No tokenizer dependency; if a harness ships a
token counter later, a `CostEstimator` implementation can wrap it.

### 5.7 Profiles (phase 2, CLI only)

A profile is a named snapshot of which items are live, per harness:

```
agent-toggle profile save flutter          # snapshot: every item, live or parked
agent-toggle profile apply flutter         # toggle the items the profile mentions
agent-toggle profile diff flutter          # dry view of what apply would do
agent-toggle profile list
```

Stored as `~/.agent-toggle/profiles/<name>.json` (dir `0700`, file `0600`) as
`{"version": 1, "saved_at": ..., "items": [{"harness", "type", "name", "live"}]}`
holding only those fields, never a path or a secret.

**`apply` semantics (mentioned items only).** An item the profile lists as
live that is parked now is enabled; one it lists as parked that is live now is
disabled; **anything the profile does not mention is never touched**, so an
item installed after the save survives an `apply`. An item the profile names
that the machine lacks gets a `skipped` row (`not on this machine`), never a
failure and never invented. `apply` goes through `ops.apply_plan` under one
lock and one batch, so it is logged and `undo` reverses it. `diff` and
`apply --dry-run` plan without a lock, state, log or CLI call; because plugin
state needs the claude CLI, plugin items show as `skipped` in a dry run.

An argument ending in `.json` or containing a path separator is a file path
(absolute paths allowed, `..` refused); anything else is a stored name.
`profile save <name> --out <file>` and `profile apply <file.json>` let a profile
live in a dotfiles repo. Schema version, harness, type, every item name, the
profile name and the file size (1 MiB) are validated; any violation exits 2.
`--project <dir>` saves, diffs and applies one project's items instead, and
project-scope entries are never part of a user-scope profile. Nothing else is
exportable: `state.json` holds machine-local absolute paths; `mcp-backups/` holds
auth material. Per-project auto-switch is deliberately **not** in scope until
`profile apply` has been used for a while; it would require a hook per
harness and a definition of "project" that differs per harness.

### 5.8 New resource types

- **`rule`** — claude only (`rules/*.md`, park dir `rules-disabled/`). Codex's
  `rules/` is a permission file and is excluded by the table (not listed in
  `dirs`).
- **project scope** — `--project <dir>` (default: none; `--project .` for
  cwd). Items under `<dir>/.claude/{skills,agents,commands,rules}` and MCP
  entries in `<dir>/.mcp.json`. Parked items go to
  `~/.agent-toggle/parked/<sha1 of dir>/…`, never into the repo. The tracked
  file still disappears from the worktree, so `git status` **will** show a
  deletion — that is inherent and is printed as a warning on every
  project-scope disable, with the one-line restore command.
- **`prompt`** — codex `prompts/*.md`, handled as a second `command` dir via
  `dirs["command"] = ("commands", "prompts")`, not a new type.

### 5.9 Interfaces for AI agents

- **Skill shim per harness**, generated by the installer from
  `agent_toggle/shims/<harness>.md.tmpl` (Claude `SKILL.md` frontmatter,
  Codex skill, OpenCode `command/*.md` with `$ARGUMENTS`, Copilot skill,
  Vibe skill, Grok skill). The shim is ~40 lines of instructions and costs
  one description line per session.
- **`--json` on every command**: `{"ok": bool, "command": "...", "results":
  [...], "warnings": [...], "needs_new_session": bool}`. Shims instruct the
  agent to call with `--json` and summarise, instead of scraping text.
- **No MCP server.** An MCP server's tool schemas are injected (or at least
  listed) every session in every harness — a permanent cost to save
  occasional cost. Revisit only for a harness that cannot run file-based
  skills at all.
- **No Claude Code plugin for now.** The shim already installs in one
  command; a plugin adds marketplace packaging and a second install path to
  support. Revisit when the marketplace becomes the dominant way users find
  tools.

### 5.10 Standalone interactive CLI

`agent-toggle` with no arguments on a TTY opens the picker; otherwise prints
help. Picker additions, all within stdlib curses:

- cost column and `s` to sort by cost; harness and type filter chips
  (`h`/`t` cycle);
- plugin rows from `claude plugin list --json` (landed in phase 1; `ui --dry-run`
  skips them, it never shells out). Flag-mechanism items (phase 2: openclaw
  plugins and flagged skills, opencode mcp) are listed by the picker only while parked (§8.2);
  toggle them with `disable` / `enable`;
- `/` starts typing a text filter; `s`, `h`, `t`, `?` are commands only while
  the filter is empty, so a filter beginning with one of them needs the leading
  `/` (landed in phase 1). `?` shows the keys;
- `--dry-run` shows the plan and exits. A `p` key to apply a profile is **not
  built**: phase 2 ships profiles as CLI only (§5.7); the picker has no profile key yet.

Windows: `pip install agent-toggle[windows]` pulls `windows-curses`; without
it `ui/menu.py` provides a numbered-menu fallback (filter prompt → numbered
list → toggle by number → apply). Same `pick()` return type, so `cli.py`
does not care which ran.

### 5.11 Platform layer (`fs.py` + `harnesses.py` home resolution)

| concern | macOS (now) | Linux | Windows |
|---|---|---|---|
| home | `$HOME` | `$HOME` | `%USERPROFILE%` (`Path.home()` covers all) |
| XDG | opencode uses `~/.config` | honour `$XDG_CONFIG_HOME` | `%APPDATA%` per harness docs — table value, not code |
| atomic write | `os.replace` | same | `os.replace` fails if destination is open; retry ×3 then loud error |
| symlinks | yes | yes | need privilege or Developer Mode; `move` of a symlink → refuse with the reason |
| case sensitivity | usually insensitive | sensitive | insensitive — resolve names via `iterdir()` match, never by string compare alone |
| harness CLI lookup | `shutil.which("claude")` + known paths | same | also `claude.cmd` / `.exe` (`which` handles `PATHEXT`) |
| curses | stdlib | stdlib | optional extra or menu fallback |
| lock file | O_EXCL | same | same (no fcntl needed) |
| `~/.claude.json` | same path | same | same |

All OS branching lives in `fs.py`; the rest of the code calls `fs.*`. CI runs
the suite on all three OSes from phase 1 even though Windows/Linux harness
tables are only filled in at phase 5 — the file-mechanism code is identical
and should be proven early.

---

## 6. Other considerations and recommendations

| topic | recommendation |
|---|---|
| **Secrets in backups** | MCP entries carry auth headers. Write backups and `state.json` with mode `0600`; `log.jsonl` never includes payloads; `SECURITY.md` states what is stored and where; `status` warns if the directory is group/world readable. |
| **Concurrency** | Lock file as in §5.4. Agents and humans do run the tool simultaneously. |
| **Sync jobs** | Detect `.synced-from-*` markers in a live dir and warn that a sync may re-create parked items; recommend parking in the *source* harness. |
| **Harness drift** | Harness config formats change between versions. Each table row records the harness version it was verified against; `doctor` compares the live layout against the row (expected dirs/keys present) and reports "layout changed" instead of failing deep inside an operation. Fixture homes in tests freeze the verified layout. Phase 2 ships it read-only (no lock, no state write, no CLI): a missing dir, file or key is an informational `absent`; only a present-but-unparseable or unsupported file is `error: layout changed`; JSONC is a `note`. |
| **Dry run** | `--dry-run` on disable/enable/profile apply prints the plan (moves, flags, backups, shared-path warnings) and writes nothing; it exits 0, or 1 when the plan contains a failing item, exactly as the real run would. Cheap and the first thing a cautious public user looks for. `disable` / `enable` plan rows have action `would-<verb>`; every plan row has status `planned`. |
| **Undo** | `undo` reverses the last logged batch using `log.jsonl`; `enable --all [--harness H]` restores everything. Done in phase 2: the reversal is its own batch (undo of undo works), rows replay in their own scope, a pre-batch log is refused. |
| **Stale state** | `status` already reports untracked parked items and live twins; add the inverse — state entries whose `parked_at` no longer exists — with the fix command. |
| **Name collisions** | A name may exist as both a skill and a command; the type is always explicit, and `cost`/picker rows show type. No "guess the type" convenience. |
| **Exit codes** | `0` ok, `1` partial failure, `2` usage error, `3` locked, `4` unsupported pair. Shims branch on them. |
| **Shell completion** | argparse + `shtab`-style static completion files generated at release time; no runtime dependency. |
| **Telemetry / update checks** | None. A tool that trims context should not phone home. |
| **Cache** | None. `cost` reads ~200 frontmatter blocks (chars ÷ 4) — milliseconds. The only slow call is `claude plugin list --json`, run once per invocation. Add a cache only after a measured `cost` run exceeds ~1 s; then key it by file mtime, one entry per path. |
| **Import / export** | Profiles are the format (§5.7). No `export` bundle of `~/.agent-toggle/`: state is machine-local, backups hold secrets. Bug reports attach `status --json` (§7). |
| **Multi-machine sync** | Non-goal (§1). No server, no account, no phone-home (see Telemetry). Same set on two machines → commit `~/.agent-toggle/profiles/` to dotfiles or symlink the directory. `state.json`, `log.jsonl`, `mcp-backups/` are machine-local and must never be synced; `SECURITY.md` says so and warns against placing `~/.agent-toggle` in a cloud-synced folder. |
| **i18n** | English output only; the shim descriptions keep the zh-TW trigger phrases because they drive skill matching, not UI text. |
| **Logging** | `-v` prints every path decision (today's companion report style); default stays terse; `--json` is never mixed with text. |
| **Documentation** | README = install + 10-line usage; `docs/DESIGN.md` (this file); `docs/harnesses.md` = the survey table kept current; `CONTRIBUTING.md` = "how to add a harness in one table row" with the fixture-home recipe. |

### 6.1 Security model

Scope: a same-user, local tool. It defends against **untrusted input** (names
from the CLI, an agent or a profile file; resource contents; a tampered
`state.json`) and against **accidental exposure** (secrets, supply chain). It
does not defend against malware running as the same user or against root;
that actor can already edit every file the tool touches.

Assets: MCP auth material in backups, the integrity of the user's harness
directories, and the promise that a disable never loses data.

| # | threat | control | status |
|---|---|---|---|
| 1 | **Path traversal via names**: `disable skill ../../x`, an absolute name, or an agent or profile supplying one | one `validate_name()` at the CLI boundary rejects empty parts, `..`, absolute paths and a leading `-`; after resolving, the item must sit inside its harness dir (`is_relative_to`); a symlink item is moved as a link, never followed | done: `validate_name()` runs for every name on `disable`/`enable` (exit 2); `resolve_item` also requires the item's parent to resolve inside the harness dir (`tests/test_containment.py`) |
| 2 | **Tampered `state.json` or imported profile steers a move**: `enable` replays `origin` and `parked_at` | `enable` refuses an entry whose `origin` is outside its harness home or project, or whose `parked_at` is outside `~/.agent-toggle/parked` and the `*-disabled` dirs; profiles carry only `(harness, type, name)`, never paths; malformed files fail loudly | done: `store.check_entry` runs before every `enable` replay (`refused: <reason>`, nothing moved; also covers flag files and backups); profiles are validated, `..` and out-of-root paths exit 2 |
| 3 | **Secret exposure** | backups `0600`, directories `0700`, `status` warns on loose modes; `log.jsonl`, `--json`, `-v`, `--dry-run` and tracebacks show names and paths, never backed-up values; profiles hold no secrets by construction | modes and warning done; output audit planned |
| 4 | **Prompt injection through the AI interface**: text inside a skill description or tool output tells the agent to disable a guardrail | the shim tells the agent to act only on the user's request; no command deletes, installs or fetches; every change is logged and reversible; bulk operations (`--all`, `profile apply`) are previewed with `--dry-run`; disabling a `rule` warns that rules may carry safety constraints; the tool never edits hooks or `settings.json` | planned: shim text, rule warning |
| 5 | **Command injection via subprocess** | argv lists only, never `shell=True`; plugin ids validated against `[A-Za-z0-9._@:/-]+` before use, because on Windows `claude.cmd` runs through `cmd.exe` where `&` in a name would inject | argv form done; validation lands with phase 5 |
| 6 | **Hostile or malformed files parsed**: oversized, binary or odd frontmatter; broken harness config | stdlib line parser for frontmatter with reads capped at 64 KiB, never evaluated; every JSON or TOML edit is verified after writing (file still parses, only the target key or block changed) and rolled back from the backup on failure | done for JSON and TOML config edits: `fs.checked_write` re-reads, verifies and restores bytes and mode on any failure; frontmatter caps unchanged. Python 3.10 has no `tomllib`, so the TOML check is textual only (see §8.2) |
| 7 | **Races and links** | one lock per batch; `safe_move` on one filesystem; refuse a park dir reached through a symlinked parent; same-user attackers are out of scope | lock and `safe_move` done |
| 8 | **Supply chain** | zero runtime dependencies; PyPI trusted publishing (OIDC, no stored token); GitHub Actions pinned by commit SHA and kept current by Dependabot; workflows default to `contents: read` and only the publish job gets `id-token: write`; README pins installs to a tag, `git+<repo-url>@vX.Y.Z` | planned for phases 0, 4 and 6 |
| 9 | **Installer overwrites**: `install-shims` writes into harness dirs | writes only its own shim files under `$HOME`-relative paths and refuses to overwrite a file that lacks the shim marker | planned; confirm against the current `install-shims` |

Disclosure: `SECURITY.md` already covers what is stored, file modes and
private reporting through GitHub advisories. Extend it with the scope
statement above and a supported-versions line.

---

## 7. Public-release readiness (phase 0, ship-blocking)

- [x] Remove every personal path: README install flow becomes
      `uv tool install git+<repo-url>` (or `pipx install git+<repo-url>`),
      with a `git clone` + `pip install -e .` fallback. No PyPI release
      until phase 4.
- [x] `LICENSE` (MIT), `pyproject.toml` (name `agent-toggle`, console script,
      `requires-python >= 3.10`, zero runtime dependencies, optional
      `[windows]` extra).
- [x] `install.sh` → `agent-toggle install-shims` subcommand (works on every
      OS; the shell script stays as a thin wrapper for one release).
- [x] CI: GitHub Actions matrix macOS/Linux/Windows × Python 3.10/3.13 running
      the test suite; lint with `ruff` (dev-only dependency).
- [x] `SECURITY.md` (backups hold auth material; never sync or cloud-share
      `~/.agent-toggle`; reporting channel),
      `CONTRIBUTING.md` (adding a harness row + fixture + verifying version),
      `CHANGELOG.md` (Keep a Changelog), issue templates (bug: harness +
      version + `status --json` output; harness request: layout survey).
- [x] Name and path containment (§6.1 row 1): `validate_name()` plus an
      inside-the-harness-dir check on every resolved item, with a test that
      `..`, absolute and leading-`-` names are refused.
- [x] No real user data anywhere: fixtures use synthetic names; docs use `~`
      and `<name>` placeholders; tests never touch the real home.
- [x] Versioning: SemVer; state schema version bumps are minor releases with
      an automatic migration; CLI flag removals are major.

---

## 8. Roadmap

Each phase ends with its acceptance criteria met on CI, not by inspection.

Phase status (tick a phase only once its acceptance criteria hold):

- [x] Phase 0 — public readiness (local gate and CI green on macOS, Linux, Windows)
- [x] Phase 1 — cost + structure (local gate and CI green on macOS, Linux, Windows)
- [ ] Phase 2 — profiles + scope
- [ ] Phase 3 — remaining harnesses
- [ ] Phase 4 — PyPI release
- [ ] Phase 5 — Linux + Windows
- [ ] Phase 6 — CI hardening + Telegram failure alerts

| phase | content | acceptance |
|---|---|---|
| **0 — public readiness** | §7 | fresh macOS user installs from the git repo with one command, runs `status`, `disable skill x`, `enable skill x`; no personal data in repo; CI green on 3 OSes |
| **1 — cost + structure** | package split (§5.1), `--json`, lock, exit codes, `cost` command + picker column, `rule` type, grok MCP via TOML backend, OpenCode adapter with alias detection, plugins in picker, `--dry-run`, 0600 backups | `cost` sorts a fixture home correctly; alias fixture reports "also affects"; v2 state migrates; shims use `--json` |
| **2 — profiles + scope** | profiles (§5.7), `--project`, flag mechanism (openclaw skills/plugins, opencode mcp), `undo`, `enable --all`, `doctor` | profile round-trip on fixtures; project-scope disable prints the git warning; flag toggles leave the rest of the JSON byte-identical except the flag; a profile or `state.json` entry with `..` or an out-of-root path is refused; a post-write parse check rolls back a broken edit |
| **3 — remaining harnesses** | copilot (skills, agents, mcp-config.json), vibe (skills), devin (explicit N/A), agy (table row; adapter once layout observed) | each has a fixture home and passes the shared conformance test |
| **4 — PyPI release** | publish `agent-toggle` to PyPI (trusted publishing from a tag via GitHub Actions), README install switches to `uv tool install agent-toggle` | tagged release installs from PyPI on a clean macOS runner and passes the phase-0 smoke test; the publish job uses OIDC with no stored PyPI token; all workflow actions are pinned by SHA |
| **5 — Linux + Windows** | platform table (§5.11) filled from real installs, `windows-curses` extra, menu fallback, path/case/symlink behaviour tested on CI; plugin ids with shell metacharacters are refused on the Windows runner | full suite green on Windows runner including picker fallback; documented harness homes per OS |
| **6 — CI hardening + Telegram alerts** | §8.1: bring `.github/workflows/ci.yml` up to the aicp CI pattern — `permissions: contents: read`, per-ref `concurrency` with cancel-in-progress, `PYTHONUTF8=1`, job `timeout-minutes`, `fail-fast: false`, and a `notify-telegram` job; README "CI notifications" section with the two `gh secret set` commands | a forced test failure on a push sends one Telegram message naming repo, branch, short SHA and the run URL; with the secrets unset the notify job exits 0 with a `::notice::`; PRs never notify; no token or chat id appears in the repo |

Order rationale: cost visibility is the feature that serves the stated goal,
so it is phase 1, before any new harness. Profiles make the saving
repeatable, so they precede the long tail of harnesses. PyPI waits until the CLI
surface has settled, because a published name and version are hard to take
back. OS ports come last
because the mechanism code is OS-neutral and CI proves that from phase 0.
CI alerts (phase 6) come after everything else: the basic CI matrix already
lands in phase 0, and alerts only pay off once the project has outside
pushes to watch.

### 8.1 Phase 6 reference: aicp CI

Model the workflow on the CI of the aicp project
(`https://github.com/weskao/aicp`, `.github/workflows/ci.yml`), which already
runs this pattern in production:

- `on: [push, pull_request]`; `permissions: contents: read`;
  `concurrency: ci-${{ github.workflow }}-${{ github.ref }}` with
  `cancel-in-progress: true`; `env: PYTHONUTF8: "1"`.
- Test job: OS matrix with `fail-fast: false` and `timeout-minutes: 15`.
- `notify-telegram` job: `needs: test`,
  `if: ${{ failure() && github.event_name == 'push' }}`, `timeout-minutes: 5`.
  Reads `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` from repository secrets
  only. If either is empty (a fork, or an owner who never set them) it prints
  `::notice::` and exits 0, so a missing secret never adds a second red X.
  The message uses `parse_mode=HTML` with `&`, `<`, `>` escaped in the branch
  and commit fields, and is sent with
  `curl --fail --silent --show-error --max-time 30 --data-urlencode ...`.
- README gets a "CI notifications" section listing the two
  `gh secret set TELEGRAM_BOT_TOKEN` / `gh secret set TELEGRAM_CHAT_ID`
  commands. Secrets never appear in the workflow file or the docs.

### 8.2 Known gaps (non-critical, found at the phase 0 + 1 gate)

- `run_cli` in `backends/plugin_cli.py` uses a fixed 120 s timeout for every
  `claude plugin ...` call.
- A plugin can show twice in the picker when it is parked under a name that
  differs from the `name@marketplace` id `claude plugin list` reports.
- Picker typing mode (after `/`) has no on-screen cue; the header only shows the
  typed text.
- `skills.paths` entries `~`, `$HOME/...` and the `opencode.jsonc` file are not
  expanded / read; only `opencode.json` with absolute, `~/` or relative paths.
- `install-shims` writes the shim into `opencode/skills` even when `skills.paths`
  redirects OpenCode's skills elsewhere.
- The `.synced-from-*` warning repeats once per harness that views the same
  directory.
- `aliases_from` on the harness record is metadata only (§5.2).
- Grok's MCP location (`~/.grok/config.toml`, `[mcp_servers.<name>]` plus a
  `.headers` sub-table) was confirmed on a live install; OpenCode's
  no-`skills.paths` default is still assumed to be its own `skills/` (§11 q2).
- `claude.ai` connectors are toggled through each *existing* project's
  `disabledMcpServers`; a project opened for the first time later starts without
  the entry until the toggle is re-run (`ponytail:` note in `backends/mcp_json.py`).
  Codex plugins are not toggleable. A repo's own `.mcp.json` is toggleable only
  through `--project` (phase 2).

Known gaps added by phase 2:

- The picker, `cost` and `profile` see an openclaw plugin or opencode mcp server only
  while it is parked (the inventory adds flag items from `state.json`): `profile
  save` does not record live ones and `profile apply` can re-enable them but not
  disable them. A flagged openclaw skill is listed by its directory and always shows
  as live, even while its flag is false. `ui` and `cost` are user-scope only (no
  `--project`).
- The picker has no profile key (§5.10); profiles are CLI only.
- TOML post-write verification is textual only on Python 3.10 (no `tomllib`):
  the block removed or appended is checked, not a full parse. `doctor` reports
  codex/grok `config.toml` as `unverified` there.
- JSONC / JSON5 config (`opencode.json`, `openclaw.json`) is refused, never
  rewritten; the flag edits require strict JSON (no comments, no trailing
  commas, no duplicate keys; a leading BOM is preserved).
- The openclaw and opencode flag shapes are assumed, not verified (§11).
- A project park across filesystems is copy + delete, not an atomic rename
  (symlink and permission handling differ); an empty `parked/<sha8>/*-disabled`
  dir can remain after `enable`.
- A killed run between the flag write and the state save leaves the flag `false`
  with no state entry; `enable` then asks the user to set it back by hand.
- `undo` and `enable --all` trust the project dir recorded in the log or state,
  the same trust user-scope replay already places in them; project dirs are
  still validated (not `$HOME`, roots or tool dirs; must exist).
- A project with only `.mcp.json` saves only its parked servers in
  `profile save --project`; live ones are not listed (the cost inventory needs
  `.claude/`). `list --project` and `enable --all --project` do not validate the
  dir; a bad one just matches nothing.
- Project `.mcp.json` backups hold the whole file text before and after the edit
  (mode `0600`), so they can include other servers' auth headers.
- `doctor` flags an empty leftover `parked/<sha8>` dir as a warning, and it
  does not check companion files.

---

## 9. Testing strategy

- Keep stdlib `unittest`; no fixtures framework. One **fixture home per
  harness** under `tests/fixtures/` (a handful of synthetic skills, agents,
  commands, an MCP config with a fake header value, a plugin table).
- One **conformance test** parametrised over the harness table: for every
  (harness, type) the table claims to support, disable → assert gone from
  live and present in state → enable → assert byte-identical restore
  (including MCP auth fields and TOML comments).
- Alias test: two harness homes sharing a real skills dir; disabling in one
  must report the other and must not double-park.
- Migration test: v2 `state.json` fixture → v3.
- Picker: `loop()` tested with a scripted key sequence against a fake window
  (already feasible: `loop` takes `win`); menu fallback tested with scripted
  `input()`.
- Windows runner exercises `fs.py` and the menu fallback; curses tests skip
  there.

---

## 10. Decisions log

| date | decision | alternatives considered | why |
|---|---|---|---|
| 2026-10-02 | Living `docs/DESIGN.md` | dated spec files; one-off evaluation | one place to keep current as phases land |
| 2026-10-02 | Cost is first-class (`cost` + picker column) | external measurement only; pure toggler | the project's purpose is token reduction; without numbers users guess |
| 2026-10-02 | AI access = skill shim + `--json`; no MCP server; no plugin yet | MCP server; plugin packaging | an MCP server costs tokens every session; the shim costs one line; plugin adds a second install path to support |
| 2026-10-02 | Python stdlib package via uv/pipx | Go binary; stay single-file | lowest rewrite cost, keeps tests, python3 is present on every target OS; Go revisited only if install friction is reported |
| 2026-10-02 | Profiles in phase 2; per-project auto-switch deferred | auto-switch now; no profiles | profiles are the repeatable saving; auto-switch needs per-harness hooks and a "project" definition first |
| 2026-10-02 | New types: `rule`, project scope; **not** hooks or output styles | hooks; CLAUDE.md includes | rules are injected whole (highest per-file cost); hooks need `settings.json` edits this tool refuses to make |
| 2026-10-02 | Extend curses picker; Windows via extra or menu fallback | Textual; prompts only | zero-install promise kept; 100+ items need a real list UI |
| 2026-10-02 | Survey all 8 harnesses now | OpenCode only; architecture only | grounded table found two freebies (grok MCP, openclaw/opencode native flags) and one trap (opencode aliasing) |
| 2026-10-02 | MIT + PyPI | Apache-2.0; clone only | standard for dev tooling; `uv tool install` is cross-OS with no packaging work |
| 2026-10-02 | Declarative table + 3 protocols | class per harness; entry-point plugins | 8 classes would be near-identical; nobody has asked for third-party adapters |
| 2026-10-02 | PyPI moved from phase 0 to phase 4; OS ports to phase 5 | PyPI in phase 0 | install from git is enough for early users; publish once the CLI and state schema stop changing |
| 2026-10-02 | Phase 6: CI hardening + Telegram failure alerts, modelled on aicp | alerts in phase 0; no alerts | basic CI already ships in phase 0; failure alerts are only worth wiring once outside pushes exist, and aicp's job is a proven template |
| 2026-10-02 | Phase-0 OSS hygiene section | minimal; none | the project is going public; these items block the first external user |
| 2026-10-02 | Explicit security model (§6.1): same-user local scope; validate every name at the boundary; profiles never carry paths; no subprocess shell; trusted publishing | ad hoc per-feature checks; defending against same-user malware | the tool moves files and holds secrets and is driven by AI agents that read untrusted text; a written model makes each control testable, and same-user malware could already edit every file involved |
| 2026-10-02 | No cache, no export/import command, no account sync; profiles are the portable unit | mtime-keyed cost cache; `export`/`import` bundle; hosted sync of `~/.agent-toggle` | cost is ms-scale file reads; state is machine-local and backups hold secrets; sync contradicts "never phone home" and dotfiles + git already solve it |
| 2026-10-02 | `profile apply` toggles only the items a profile mentions; profiles are CLI only in phase 2 | apply = exact set (disable everything else); picker `p` key | an exact-set apply would park anything installed after the save; the picker key adds UI surface before the semantics have been used |

## 11. Open questions (need a real install to answer)

1. Agy skill/command layout — not present on the surveyed machine.
2. Whether OpenCode's `skills.paths` default (when unset) is its own
   `~/.config/opencode/skills` — determines the fixture for a non-aliased
   install.
3. Copilot `installed-plugins/` entry format once a plugin is installed
   (directory was empty on the surveyed machine).
4. Windows harness home paths per harness — each harness documents its own;
   fill §5.11 from docs at phase 5, not from guesses.
5. openclaw: are `skills.entries.<name>.enabled` and
   `plugins.entries.<name>.enabled` in `openclaw.json` the real flag shape, and
   is the file strict JSON on a real install (not JSON5)? The flag mechanism
   assumes yes and refuses anything else (phase 2; not verified).
6. opencode: is `mcp.<name>.enabled` in `opencode.json` the real flag, and does
   a real `opencode.json` stay free of comments and trailing commas (it may be
   JSONC)? Assumed, refused otherwise (phase 2; not verified).
