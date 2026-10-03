# Security policy

## Scope

agent-toggle is a same-user, local tool. It defends against **untrusted
input** (names from the CLI, an agent or a profile file; resource contents; a
tampered `state.json`) and against **accidental exposure** (secrets, supply
chain). It does not defend against malware running as the same user or against
root; that actor can already edit every file the tool touches.

## Supported versions

Only the latest minor release (currently 0.1.x) receives security fixes.

## What agent-toggle stores

All bookkeeping lives under `~/.agent-toggle/`:

- `state.json` records which resources are parked, where they came from and
  where they went. It contains local file paths and resource names.
- `mcp-backups/` holds a verbatim copy of every MCP server entry removed by
  `disable mcp`, so `enable mcp` can restore it exactly. **These entries can
  contain credentials**: `Authorization` headers (for example
  `Bearer <token>`), API keys in `env` blocks, and tokens embedded in URLs.
  Treat the directory like any other secrets store.

## File-mode policy

Backups and state files are written with mode `0600` (owner read/write only)
and the backup directory with `0700`. Do not loosen these modes, do not
commit `~/.agent-toggle/` to version control, and do not paste backup
contents into issues or logs. `agent-toggle status --json` is safe to share;
it reports names and paths, never backed-up values.

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub private security
advisories: open the repository's **Security** tab and choose
**Report a vulnerability**. Do not open a public issue for security
problems. Include the harness, the `agent-toggle` version, and the minimal
steps to reproduce, using synthetic data only.

You can expect an acknowledgement within a few days. Fixes ship in a patch
release and are credited in `CHANGELOG.md` unless you ask otherwise.
