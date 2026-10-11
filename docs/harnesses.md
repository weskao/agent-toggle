# Harness survey

What each harness keeps, where, and how this tool toggles it. The source of truth
is `agent_toggle/harnesses.py` (the table) and DESIGN §4 (the original survey);
this page is the readable, kept-current copy. When a row in `harnesses.py`
changes, change the matching row here in the same commit.

`~` is Python's `Path.home()`: `$HOME` on macOS and Linux, `%USERPROFILE%` on
Windows.

## Verified against

The `verified` field on each harness row in `harnesses.py` is the harness
version whose on-disk layout was last checked against that row, and the column
below mirrors it (a test fails if they disagree). A version counts as verified
when the harness reported it and `doctor` found the layout matching the row on a
real install. `unverified` means no version could be read, or the row has
nothing to check.

| harness | verified against | how the version was read |
|---|---|---|
| claude | 2.1.288 | `claude --version` |
| codex | 0.160.0 | `codex --version` |
| grok | 1.0.44 | `version` key of `~/.grok/version.json` (the `grok` binary was not runnable) |
| opencode | 2.0.22 | `opencode --version` |
| openclaw | 2026.9.2 | `openclaw --version` (needs Node ≥ 25.9; run on 25.9.0) |
| copilot | 1.0.90 | `copilot --version` |
| vibe | 2.25.8 | `vibe --version` |
| devin | unverified | `devin --version` did not return within 10 s; the row has nothing to check |
| agy | 1.3.3 | `agy --version` |

Last full pass: 2026-10-03, macOS, `doctor` reported `ok` ("layout matches the
table row") for every harness above except as noted. To refresh, run
`<harness> --version` and `agent-toggle doctor --json`, then update `verified`
here and in `harnesses.py`.

## Homes, resources and mechanisms

Mechanisms: **move** (park the dir or file), **flag** (flip a boolean in a
config file), **remove-with-backup** (delete the entry, keep it verbatim under
`~/.agent-toggle/`), **native CLI** (hand off to the harness's own command).
A dash means the harness has no such resource this tool can toggle.

| harness | home | skill | agent | command | rule | plugin | mcp |
|---|---|---|---|---|---|---|---|
| claude | `~/.claude` | `skills/` — move | `agents/` — move | `commands/` — move | `rules/` — move | native CLI (`claude plugin enable/disable`); a plugin whose `hooks/hooks.json` has `modules` is a mod | remove-with-backup |
| codex | `~/.codex` | `skills/` — move | `agents/` — move | `commands/` or `prompts/` — move | — (`rules/default.rules` is a permission file) | — (no CLI this tool can drive) | remove-with-backup |
| grok | `~/.grok` | `skills/` — move | — | — | — | — | remove-with-backup |
| opencode | `$XDG_CONFIG_HOME/opencode`, else `~/.config/opencode` (every OS) | every `skills.paths[]` entry of `opencode.json` and `opencode.jsonc` (both load and merge; `~`, `$HOME/`, `${HOME}/` expanded, relative entries skipped), `skills/`, `skill/`, `~/.claude/skills`, `~/.agents/skills` — move | — (agents are config entries) | `command/` — move | — | — | flag |
| openclaw | `~/.openclaw` | `skills/` — move, or flag when `skills.entries.<name>` exists | `agents/` — move | — | — | flag | — (state is sqlite) |
| copilot | `~/.copilot` | `skills/` — move | `agents/` — move | — | — | — | remove-with-backup |
| vibe | `~/.vibe` | `skills/` — move | — | — | — | — | — |
| devin | `~/.devin` | — | — | — | — | — | — (resources are cloud-side) |
| agy | `~/.gemini/config` | `skills/` — move | — | — | — (`GEMINI.md` / `AGENTS.md` are per-directory files) | — | remove-with-backup |

Linux and Windows homes follow each harness's documentation and are not yet
checked against real installs there (DESIGN §11 q4). opencode and agy
(`~/.gemini/config`) are the only homes that differ from `~/.<name>`.

## MCP configuration

| harness | config file | where the servers live | backend |
|---|---|---|---|
| claude | `~/.claude.json` | top-level `mcpServers` (user scope); `--project` uses the repo's `.mcp.json` | `claude-json` / `project-json` |
| codex | `~/.codex/config.toml` | `[mcp_servers.<name>]` tables, sub-tables kept verbatim | `toml` |
| grok | `~/.grok/config.toml` | `[mcp_servers.<name>]` tables, including `.headers` | `toml` |
| opencode | `<home>/opencode.json` | `mcp.<name>.enabled` (flag, no removal) | flag, strict JSON only |
| openclaw | `~/.openclaw/state/openclaw.sqlite` | not togglable | refused |
| copilot | `~/.copilot/mcp-config.json` | `mcpServers` | `json` |
| agy | `~/.gemini/config/mcp_config.json` | `mcpServers` | `json` |
| vibe, devin | none the tool handles | | |

Files the tool may write are limited to the row's `editable` set
(`opencode.json`, `openclaw.json`, `mcp-config.json`, `mcp_config.json`) plus the MCP config files
above. Copilot's `config.json` is machine-managed JSONC and is never edited or
listed.

## Assumed versus confirmed

| item | status |
|---|---|
| claude, codex, grok, copilot, vibe homes and skill/agent dirs | Confirmed on a real install (DESIGN §4). |
| grok MCP in `config.toml` | Confirmed on a live install. |
| opencode `mcp.<name>.enabled` boolean in `opencode.json` | Shape observed (boolean under each server) on a real install at the version above. Still assumed: that the file is always strict JSON (the one inspected was). |
| opencode `skills.paths` | A list in `opencode.json`; observed on a real install. The scanned-dir set and relative-entry rule (session cwd, not the config dir) were probed on 2.0.22 (DESIGN §11 q2). Probed on 2.0.22 with `opencode debug agents`: `opencode.json` and `opencode.jsonc` both load and merge (`.jsonc` after `.json`, so it wins on a key conflict); not verified whether `skills.paths` arrays are concatenated or replaced by the later layer (the tool unions them). |
| openclaw `skills.entries.<name>.enabled`, `plugins.entries.<name>.enabled` | Shape observed (boolean `enabled` per entry; some plugin entries carry only `config` and no `enabled`, which the tool treats as not flaggable) at the version above. `openclaw.json` parsed as strict JSON. |
| copilot `installed-plugins/` entry format | Unknown: the directory was empty on the surveyed machine, so plugins stay unsupported. |
| agy `~/.gemini/config/` layout | Confirmed on 1.3.3: the CLI's bundled `agy-customizations` docs name `~/.gemini/config/` as the global root (`skills/<name>/SKILL.md`, `mcp_config.json`), and `agy mcp disable` was probed in a throwaway HOME. agy's own `disabled: true` MCP flag and its `plugins/` (`config.json -> plugins.<dir>.enabled`) are not driven by this tool yet. |
| Windows and Linux harness homes | Assumed from documentation. |
