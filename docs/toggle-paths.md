# What disable and enable do, per type

What `disable` and `enable` change on disk for each resource type, with example
paths. The names are made up (`demo-skill`, `example-mcp`). `~` is
`Path.home()`: `$HOME` on macOS and Linux, `%USERPROFILE%` on Windows.

The table of which harness supports which type is in [harnesses.md](harnesses.md).

Every toggle also does two things that are not repeated below. It records an
entry in `~/.agent-toggle/state.json`, keyed `<harness>:<type>:<name>` (for
example `claude:skill:demo-skill`). It also appends a row to
`~/.agent-toggle/log.jsonl`, which is what `undo` reads.

## Summary

| type | mechanism | disable | enable |
|---|---|---|---|
| skill, agent, command, rule | move | moves the item to `~/.agent-toggle/parked/user/<owner>/<dir>/`, nested path kept; exclusive companion files go to `~/.agent-toggle/companions/` | moves it back to the `origin` in the state entry, restores the companions, removes emptied park dirs |
| plugin (claude) | native CLI | `claude plugin disable <id> -s <scope>`, run in the install's project; scope and dir recorded; nothing moved | `claude plugin enable <id>` with the recorded scope and dir |
| plugin (openclaw) | flag | `plugins.entries.<name>.enabled` → `false` in `openclaw.json` | the flag's previous value is written back |
| MCP (claude) | remove with backup | raw entry from `~/.claude.json` saved to `mcp-backups/`, then `claude mcp remove -s <scope>` | `claude mcp add-json` with the saved entry; local scope runs in the saved project dir; the name leaves every project's `disabledMcpServers` |
| MCP (codex, grok) | remove with backup | the `[mcp_servers.<name>]` text block saved, then cut from `config.toml` | the block is appended back to `config.toml` |
| MCP (copilot, agy) | remove with backup | the entry is saved, then cut from the JSON config | the entry is put back |
| MCP (opencode) | flag | `mcp.<name>.enabled` → `false` in `opencode.json` | the flag's previous value is written back |
| MCP (project `.mcp.json`) | direct JSON edit, `--project` only | only that server's entry is cut; every other byte stays | the entry goes back after its old neighbour; an unchanged file comes back byte for byte; the name leaves that project's `disabledMcpServers` |
| MCP (claude.ai connector) | flag | the name is added to `disabledMcpServers` in every existing project of `~/.claude.json` | the name is removed again; a project first opened later needs a re-run |

## Moved items: skill, agent, command, rule

User scope parks under `~/.agent-toggle/parked/user/<owner>/<dir>/`, never
inside a harness home. Claude Code loads `commands/` and `rules/` recursively,
so a park dir nested there would still load.

| item | live path (enabled) | parked path (disabled) |
|---|---|---|
| claude skill `demo-skill` | `~/.claude/skills/demo-skill/` | `~/.agent-toggle/parked/user/claude/skills/demo-skill/` |
| claude agent `demo-agent` | `~/.claude/agents/demo-agent.md` | `~/.agent-toggle/parked/user/claude/agents/demo-agent.md` |
| claude command `demo:batch` | `~/.claude/commands/demo/batch.md` | `~/.agent-toggle/parked/user/claude/commands/demo/batch.md` |
| claude rule `demo-rule` | `~/.claude/rules/demo-rule.md` | `~/.agent-toggle/parked/user/claude/rules/demo-rule.md` |
| codex prompt `demo-prompt` | `~/.codex/prompts/demo-prompt.md` | `~/.agent-toggle/parked/user/codex/prompts/demo-prompt.md` |
| opencode skill in an absolute `skills.paths` dir | `/opt/shared-skills/demo-skill/` | `~/.agent-toggle/parked/user/opencode/ext-<sha8>/demo-skill/` |
| project skill (`--project ~/work/repo`) | `~/work/repo/.claude/skills/demo-skill/` | `~/.agent-toggle/parked/<sha8>/skills-disabled/demo-skill/` |

- For an `ext-` park dir, `<sha8>` is the first 8 hex digits of the SHA-1 of the
  resolved path, here `/opt/shared-skills`.
- For a project, `<sha8>` is the same hash of the resolved project dir.
- A dir that several harnesses read parks once, under its owner. OpenCode, for
  example, also scans `~/.claude/skills`, so those skills park under
  `parked/user/claude/`.
- An openclaw skill whose `skills.entries.<name>` exists in `openclaw.json` gets
  a flag instead, and its directory stays in place.

**Companion files** (user scope only). A helper that the item's text refers to,
and that nothing else uses, moves with the item. It goes under
`~/.agent-toggle/companions/<key>/`, where `<key>` is the state key with `:`
replaced by `_`, followed by its path relative to the harness home.

| live path | parked path |
|---|---|
| `~/.claude/scripts/demo-helper.py`, used only by `demo:batch` | `~/.agent-toggle/companions/claude_command_demo_batch/scripts/demo-helper.py` |

A helper that something else also references is left in place. The output
reports it as `kept ... (also used by ...)`.

**Enable** reverses each move using the `origin` and `parked_at` paths stored
in the state entry, restores the companions to their old paths, and removes park
dirs that are now empty.

## Plugins

| harness | disable | enable |
|---|---|---|
| claude | `claude plugin disable demo-plugin@example-marketplace -s <scope>` | `claude plugin enable demo-plugin@example-marketplace -s <scope>` |
| openclaw | `~/.openclaw/openclaw.json`: `plugins.entries.demo-plugin.enabled` `true` → `false` | the previous value is written back |

- A claude plugin, mods included, is never moved. Its files stay under
  `~/.claude/plugins/cache/<marketplace>/<name>/<version>/`, and Claude Code
  records the change in its own settings for that scope:

  | scope | setting changed |
  |---|---|
  | user | `~/.claude/settings.json` → `enabledPlugins["demo-plugin@example-marketplace"]` |
  | project | `~/work/repo/.claude/settings.json` → the same key |
  | local | `~/work/repo/.claude/settings.local.json` → the same key |

- The scope comes from the plugin's one install in
  `~/.claude/plugins/installed_plugins.json`. A project or local install runs the command
  in its `projectPath` (`~/work/repo`), so it does not matter where you run `agent-toggle`.
  Without `-s`, the CLI would pick the scope from the current dir: a project plugin
  enabled from elsewhere would get a new user-scope `true` and stay off in its project.
- The state entry keeps `scope` and, for project or local scope, `cwd` (the project dir).
  `enable` replays both, even when the registry changed meanwhile.
- With several installs of one id, no registry, or a project dir that is gone, the CLI
  picks the scope itself, as before. The state entry then records the dir that disable ran
  in as `cwd`, and `enable` runs there, so the CLI resolves the same scope.
- The plugin id is checked before the command runs. Anything outside
  `A-Z a-z 0-9 . _ @ : / -` is refused.

## MCP servers

The backup is verbatim: auth headers are kept. Backups are mode `0600` and live
in `~/.agent-toggle/mcp-backups/`.

| server | where it lives (enabled) | disable | backup file |
|---|---|---|---|
| claude, user scope | `~/.claude.json` → `mcpServers.example-mcp` | `claude mcp remove -s user example-mcp` | `claude__example-mcp.json` |
| claude, local scope | `~/.claude.json` → `projects["/home/u/work/repo"].mcpServers.example-mcp` | `claude mcp remove -s local example-mcp`, run in that project | `claude__example-mcp.json`; the state entry keeps the project path |
| codex | `~/.codex/config.toml` → `[mcp_servers.example-mcp]` and its sub-tables | the block is cut from the file | `codex__example-mcp.json` |
| grok | `~/.grok/config.toml` → `[mcp_servers.example-mcp]` | the block is cut from the file | `grok__example-mcp.json` |
| copilot | `~/.copilot/mcp-config.json` → `mcpServers.example-mcp` | the entry is cut | `copilot__example-mcp.json` |
| agy | `~/.gemini/config/mcp_config.json` → `mcpServers.example-mcp` | the entry is cut | `agy__example-mcp.json` |
| opencode | `~/.config/opencode/opencode.json` → `mcp.example-mcp.enabled` | `true` → `false`; nothing removed | none (the previous value is in the state entry) |
| project (`--project ~/work/repo`) | `~/work/repo/.mcp.json` → `mcpServers.example-mcp` | the entry is cut; the rest of the file is untouched | `<sha8>__claude__example-mcp.json` |
| claude.ai connector `claude.ai Example` | your claude.ai account | the name is added to `projects["<dir>"].disabledMcpServers` for every project in `~/.claude.json` | none |

**Enable** depends on the server:

- **claude:** runs `claude mcp add-json -s <scope> example-mcp '<saved entry>'`.
  Local scope runs it back in the saved project dir. Then the name is removed
  from every project's `disabledMcpServers` in `~/.claude.json`, where the
  mod's `/mcp disable example-mcp` would otherwise keep it off.
- **codex and grok:** append the saved block to the end of `config.toml`.
- **copilot, agy and project `.mcp.json`:** put the entry back. For a project
  `.mcp.json`, the name also leaves `disabledMcpServers` for that project and the
  dirs inside it (`projects["/home/u/work/repo"]`), never for another repo.
- **opencode:** writes the old flag value back.
- **connectors:** remove the name from `disabledMcpServers`.

Each config edit is verified after writing and rolled back on any mismatch. MCP
changes need a new session, unless the agent-toggle pane mod queues
`/mcp disable|enable <name>` for you.
