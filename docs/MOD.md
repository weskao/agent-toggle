# agent-toggle mod — Design

A Claude Code mod that shows the picker as a pane inside Claude, driven by the
installed `agent-toggle` CLI. Companion to `DESIGN.md` §5.10 (the curses picker).

- Status: **draft** (2026-10-10)
- Scope: Claude Code only, `claude` harness only. The curses picker stays the
  cross-harness UI.

---

## 1. Why a mod, not the curses picker

The curses picker needs a real TTY. A mod pane is a declarative element tree
(`Box`, `Text`, `Button`, `Input`) that the engine draws; there is no PTY or
raw-terminal element, and `$.process.spawn` only streams text. So the picker
cannot be hosted as-is. The mod redraws the *view* and keeps the CLI as the
*backend*: every read is `agent-toggle ... --json`, every write is
`agent-toggle disable|enable ... --json`. No Python changes.

## 2. Where it lives

Same repo, one subfolder:

```
mod/
  .claude-plugin/plugin.json     name, version, description, "types": "./types/index.d.ts"
  hooks/hooks.json               { "modules": ["./register.tsx"] }
  hooks/register.tsx             the hooks module
  hooks/register.test.ts         claude plugin test
  types/index.d.ts               $.state contract
```

Reasons (decided 2026-10-10): the only coupling is the CLI's `--json` schema,
so a schema change and the mod's fix land in one PR and one CI run; the mod is
~150 lines, too small to carry its own repo; one version number for both.
`pyproject.toml` does not package `mod/`. A root `.claude-plugin/marketplace.json`
pointing at `./mod` is added only when install-by-marketplace is wanted.

## 3. User-facing behaviour

| action | result |
|---|---|
| `/agent-toggle` | opens the pane titled `agent-toggle`; below 110 columns it stays undrawn and a toast says to widen the terminal |
| pane opens | runs `agent-toggle cost --json --harness claude`, draws one row per resource, grouped by type, with a tokens column |
| press a row | runs `agent-toggle <disable\|enable> <type> <name> --json`, then re-runs `cost --json` and redraws; a toast carries `ok` or the error line |
| type in the filter `Input` | substring match on `type/name`, same rule as the curses filter (`model.py`) |
| `/agent-toggle` again | refreshes the list (re-open is idempotent) |

Out of scope for v1, by decision: profiles, `--project` scope, sort by cost,
harnesses other than claude, undo, dry-run preview. Each maps to one more CLI
call and can be added as a Button without changing the shape below.

## 4. Data flow

```
command.run /agent-toggle
  └─ $.ui.open({ id: "agent-toggle", title: "agent-toggle" })
  └─ refresh()
       └─ $.process.run(["agent-toggle", "cost", "--json", "--harness", "claude"])
       └─ update($, rows, parse(stdout).results)      # redraws the pane

ui.render { component: "Pane", requestId: "agent-toggle" }
  └─ read($, rows), read($, filter)  →  <Box> Input + Button per row </Box>

Button onPress(row)
  └─ $.process.run(["agent-toggle", row.enabled ? "disable" : "enable",
                    row.type, row.name, "--json"])
  └─ $.ui.toast(result.ok ? "ok" : result.error)
  └─ refresh()
```

`cost --json` is used instead of `list --json` because its rows already carry
`enabled`, `tokens` and `would_save`, so one call fills the whole row. Fields
read: `type`, `name`, `enabled`, `tokens`, `mod`. Anything else is ignored, so
additive schema changes do not break the mod.

The CLI is located by name on `PATH` (`$.process.run` takes an argv, no shell).
If `exitCode !== 0` or stdout does not parse, the pane shows the first stderr
line and a hint to run `agent-toggle doctor`.

## 5. State contract (`types/index.d.ts`)

```ts
export type Row = { type: string; name: string; enabled: boolean; tokens: number; mod: boolean }

declare module 'claude-code' {
  interface PluginState {
    'agent-toggle': { rows: Row[]; filter: string; busy: boolean; error: string }
  }
}
```

`rows` and `filter` live in `$.state` so a hot reload keeps the list; `busy`
disables every Button while a CLI call runs, so two presses cannot race the
CLI's lock; `error` is the last failure text or `""`.

## 6. Hooks module shape (`hooks/register.tsx`)

- `session.start`: `$.command.register({ name: "agent-toggle", description })`. Does **not** open the pane unasked.
- `command.run { command: "agent-toggle" }`: open pane, `refresh()`, return `{ text: "agent-toggle pane opened." }`.
- `ui.render { component: "Pane", requestId: "agent-toggle" }`: draw. Rows are
  sliced to `viewport.rows - 3` after filtering; the header shows
  `shown/total` and `Σ tokens` of enabled rows.
- Row label: `[x] skill/my-skill   788` for enabled, `[ ] ...` dim for disabled;
  `mod` rows get a `mod` tag, matching the curses picker's wording.
- One helper `cli(argv)` wraps `$.process.run`, sets `busy`, parses JSON, and
  stores `error`. Every call goes through it.

## 7. Verification

| check | command |
|---|---|
| manifest + hook surface | `claude plugin validate mod` |
| types | `tsc -p mod` (after first load lays `.claude-plugin/types/`) |
| behaviour | `claude plugin test mod` — one test: a fake `process.run` returning two rows, press the first, assert the second `process.run` argv is `["agent-toggle","disable","skill","a","--json"]` and the toast is `ok` |
| manual | `claude --plugin-dir ./mod`, `/agent-toggle`, toggle one skill, confirm with `agent-toggle status` |

CI: one extra job `mod` on `ubuntu-latest` that runs validate + test. It
needs the `claude` CLI on the runner; if that is not available in CI the job
is marked `continue-on-error` until it is.

## 8. Release

`mod/.claude-plugin/plugin.json` `version` tracks `pyproject.toml`; the
release skill bumps both. README gets a short "Mod" section: what it is, the
`--plugin-dir` way to try it, and the marketplace install line once §2's
`marketplace.json` exists.

## 9. Decisions log

- 2026-10-10 — pane mod, not curses embedding: no PTY element in the mod API.
- 2026-10-10 — same repo, `mod/` subfolder: coupling is the `--json` schema; see §2.
- 2026-10-10 — `cost --json` as the one read: carries enabled + tokens in one call.
- 2026-10-10 — claude harness only in v1: the pane runs inside Claude; other
  harnesses keep the curses picker.
- 2026-10-10 — v1 drops search-by-key, sort, profiles, project scope: each is a
  later Button or Input, none changes the data flow.

## 10. Open questions

- Does the terminal surface move focus between Buttons with arrow keys, or
  only Tab? Decides whether a `hotkey` per row is needed for keyboard use.
- Is `claude` available on GitHub-hosted runners for `claude plugin test`? Decides
  whether the CI job is blocking.
