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
- A project `.mcp.json` backup holds only the server that was disabled: the
  entry, its exact text and the names of the servers next to it -- never
  another server's entry or the whole file. The user-scope JSON MCP backend
  (copilot's `mcp-config.json`) still backs up the whole file text before and
  after the edit, so that backup can include other servers' headers. A project
  backup written by an earlier version may still hold the whole file text
  (including other servers' headers); it keeps working, and disabling that
  server again (after `enable`) replaces it with the per-entry form.

## File-mode policy

Backups and state files are written with mode `0600` (owner read/write only)
and the backup directory with `0700`. Do not loosen these modes, do not
commit `~/.agent-toggle/` to version control, and do not paste backup
contents into issues or logs. `agent-toggle status --json` is safe to share;
it reports names and paths, never backed-up values.

## Network use: the update check

agent-toggle has no telemetry and never uploads your resources, paths or state. It makes
one network call, a default-on update check:

- `GET https://pypi.org/pypi/agent-toggle/json`, on a background thread with a 0.8 s timeout;
  nothing is sent beyond a normal HTTP GET with the User-Agent `agent-toggle-update-check`.
- The answer is cached for 10 minutes in `~/.agent-toggle/update-check.json` (mode `0600`,
  written atomically, never following a symlink).
- Redirects are followed over https only; the response body is capped at 1 MB; version
  strings are validated, and control characters are stripped before anything is printed.
- A newer release is reported on stderr only; it never changes stdout or the exit code.
  `Update now` runs `uv tool upgrade agent-toggle` as a fixed argument list, with no shell.
- Opt out with `AGENT_TOGGLE_UPDATE_CHECK=0` or the `update_check` setting
  (`agent-toggle config`).

Settings live in `~/.agent-toggle/config.json` (mode `0600`, atomic). The Telegram bot
token is not stored there: it is kept in the OS keystore through telegram-kit.

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub private security
advisories: open the repository's **Security** tab and choose
**Report a vulnerability**. Do not open a public issue for security
problems. Include the harness, the `agent-toggle` version, and the minimal
steps to reproduce, using synthetic data only.

You can expect an acknowledgement within a few days. Fixes ship in a patch
release and are credited in `CHANGELOG.md` unless you ask otherwise.
