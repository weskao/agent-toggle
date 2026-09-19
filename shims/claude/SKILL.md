---
name: agent-toggle
description: Temporarily disable or restore AI-agent resources — skills, agents, commands, plugins, MCP servers — across Claude Code, Codex, Grok CLI and OpenClaw. Nothing is deleted; everything is parked and reversible. Use when the user wants to turn something off, trim startup context cost, re-enable something previously disabled, or asks "how do I disable X" / "停用這個 skill" / "關掉某個 MCP" / "把它加回來".
---

# agent-toggle

Parks and restores agent resources. **Never deletes anything.**

The tool lives outside any single harness:
`__AGENT_TOGGLE_ROOT__/agent_toggle.py`

## Usage

```bash
python3 __AGENT_TOGGLE_ROOT__/agent_toggle.py <command> [args]
```

| command | what it does |
|---|---|
| `status` | harnesses found, types each supports, parked counts, gitignore check |
| `list [type]` | what is currently disabled |
| `disable <type> <name>...` | park one or more items |
| `enable <type> <name>...` | put them back |
| `migrate` | import an older `~/.claude-toggle/` state |

`<type>` = `skill` / `agent` / `command` / `plugin` / `mcp`
`--harness claude|codex|grok|openclaw` (default `claude`)

```bash
agent_toggle.py disable skill   academic-plotting matplotlib
agent_toggle.py disable command orch:batch
agent_toggle.py disable mcp     telegram-mcp
agent_toggle.py disable skill   foo --harness codex
agent_toggle.py enable  mcp     telegram-mcp
```

A colon addresses nesting: `orch:batch` → `commands/orch/batch.md`.

**Run `status` first.** The `claude` CLI is often missing from a hook or agent
PATH; without it the `plugin` and `mcp` actions fail, while `skill`, `agent`
and `command` are unaffected.

## Harness support

| harness | skill | agent | command | plugin | mcp |
|---|:-:|:-:|:-:|:-:|:-:|
| claude | ✓ | ✓ | ✓ | ✓ | ✓ |
| codex | ✓ | ✓ | ✓ | ✓ | ✓ (config.toml) |
| grok | ✓ | — | — | — | — |
| openclaw | ✓ | ✓ | — | — | — (sqlite) |

Unsupported pairs fail loudly rather than doing nothing.

## What to tell the user

- **MCP changes need a new session.** Opening cost is fixed at session start.
- **Companion files**: helpers a skill/command references are parked with it
  only when *nothing else* uses them. Shared helpers are reported and left
  alone — moving `tg-send.sh` because one of its five callers got disabled
  would break the other four.
- **Rollback**: `enable <type> <name>`. MCP configs are backed up verbatim in
  `~/.agent-toggle/mcp-backups/`, so restoring keeps auth fields intact.
  If the state record is ever lost but the backup file survives, re-add it
  manually with `claude mcp add-json --scope user <name> "$(cat <backup>)"`.

## State

Everything in `~/.agent-toggle/` — `state.json`, `log.jsonl`, `mcp-backups/`,
`companions/`. Never marker files next to the targets: the user's `git status`
in their own project must not change because of this tool's bookkeeping.
