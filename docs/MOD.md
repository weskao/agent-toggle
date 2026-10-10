# agent-toggle mod — Design

A Claude Code mod that shows the picker as a pane inside Claude, driven by the
installed `agent-toggle` CLI. Companion to `DESIGN.md` §5.10 (the curses picker).

- Status: **draft** (2026-10-10)
- Scope: Claude Code only, `claude` harness only. The curses picker stays the
  cross-harness UI.

---

## 1. Why a mod, not the curses picker

The curses picker needs a real TTY. A mod pane is a declarative element tree
(`Box`, `Text`, `Button`, `Client`) that the engine draws; there is no PTY or
raw-terminal element, and `$.process.spawn` only streams text. So the picker
cannot be hosted as-is. The mod redraws the *view* (a `Client` surface module,
which also receives keys) and keeps the CLI as the *backend*: every read is `agent-toggle ... --json`, every write is
`agent-toggle disable|enable ... --json`. No Python changes.

## 2. Where it lives

Same repo, one subfolder:

```
mod/
  .claude-plugin/plugin.json     name, version, description, "types": "./types/index.d.ts"
  hooks/hooks.json               { "modules": ["./register.tsx"] }
  hooks/register.tsx             the hooks module: CLI calls, state, the `ui.message` hook
  hooks/picker.tsx               the surface module: the picker's look, keys and scrolling
  hooks/register.test.ts         claude plugin test
  hooks/picker.test.ts           claude plugin test: what the picker draws
  types/index.d.ts               $.state contract
```

Reasons (decided 2026-10-10): the only coupling is the CLI's `--json` schema,
so a schema change and the mod's fix land in one PR and one CI run; the mod is
~150 lines, too small to carry its own repo; one version number for both.
`pyproject.toml` does not package `mod/`. The repo root holds `.claude-plugin/marketplace.json`
(marketplace `agent-toggle`, one plugin, `"source": "./mod"`), so others install with
`/plugin install agent-toggle --marketplace weskao/agent-toggle`.

## 3. User-facing behaviour

| action | result |
|---|---|
| `/agent-toggle` | opens the pane titled `agent-toggle`; a pane the person asked for is placed at any width, and if no surface can place it a toast says so (`isPlaced` false) |
| pane opens | runs `agent-toggle cost --json --harness claude`, draws the curses picker's look: bold title with the live-token total, a search line with the sort label, rows grouped by type (`model.py` GROUPS order, a heading with count and rule per group), green `●` live / yellow `○` parked, tokens (`(N)` for a parked row) and a bar, the cursor row in reverse video, a key-chip footer; the status line ends with the cursor position (`· 7/23`, as the curses picker), and the first and last list lines end with the count of rows scrolled out above (`↑ 4`) and below (`↓ 12`), in place of a scrollbar |
| keys (after a click on the list) | `↑ ↓ PgUp PgDn Home End Ctrl-P Ctrl-N` move; `Space`/`Enter` toggle; `Tab` toggles and advances; `/` or a letter starts a search; `s` cycles sort (name, cost); `n` toggles every type with the row's name (`<disable\|enable> all <name>`, one batch; only rows in the cursor row's state move); `Backspace`, `Ctrl-U` edit; a click moves the cursor |
| mouse wheel over the list (no click needed) | moves the cursor a row per tick, as the wheel sends Up/Down to the curses picker |
| toggle a row | runs `agent-toggle <disable\|enable> <type> <name> --json --harness claude`, then re-runs `cost --json` and redraws; a toast carries `ok` or the error line. It applies at once: no staging, no Enter-to-apply |
| search | terms ANDed over `type/name` and the group (`mod`, `mods`); with no hit, letters-in-order on the name (`ctxmd` finds `context-md`, status line says `≈ fuzzy match`); a second `Space` toggles. Command letters (`s`, `n`) act only while the query is empty |
| `/agent-toggle` again | refreshes the list (re-open is idempotent) |

Out of scope for v1, by decision: harness tabs, the `t` `p` `a` keys, staging,
`--project` scope, undo, dry-run preview. Each maps to CLI calls and a key in
`picker.tsx` without changing the shape below. `mobile` and `vscode` draw no
`Client`: there the pane is one `Button` per row and the surface scrolls it.

## 4. Data flow

```
command.run /agent-toggle
  └─ $.ui.open({ id: "agent-toggle", title: "agent-toggle" })
  └─ refresh()
       └─ $.process.run(["agent-toggle", "cost", "--json", "--harness", "claude"])
       └─ update($, rows, parse(stdout).results)      # redraws the pane

ui.render { component: "Pane", requestId: "agent-toggle" }
  └─ read($, rows), read($, busy), read($, error)
  └─ terminal/desktop: <Client module="./picker.tsx" props={{ rows, busy, error }} />
       picker.tsx keeps cursor, scroll top, query and sort in its own state
       key/click → surface.post({ op: "toggle", type, name, mod })
  └─ other surfaces: a Button per row

ui.message (the Client's post)            # input, not fact
  └─ find the row in `rows` by type/name/mod; none → ignored
  └─ toggle(row), using the list's own `enabled`
  └─ $.process.run(["agent-toggle", row.enabled ? "disable" : "enable",
                    row.type, row.name, "--json", "--harness", "claude"])
  └─ $.ui.toast(ok ? (needs_new_session ? "ok — takes effect in a new session" : "ok") : error)
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
export type Row = { type: string; name: string; enabled: boolean; tokens: number; save: number; mod: boolean }

declare module 'claude-code' {
  interface PluginState {
    'agent-toggle': { rows: Row[]; busy: boolean; error: string; wheel: number }
  }
}
```

`rows` lives in `$.state` so a hot reload keeps the list (`save` is the CLI's `would_save`, what restoring a parked row loads); the cursor, query and sort live in the `Client`'s own state, kept across redraws; `busy`
is claimed atomically (inside `update`'s versioned retry) for a toggle plus its
refresh, so two presses cannot race the CLI's lock: a press while busy is
dropped, and the picker says `working…`; `error` is the last failure text or `""`.

## 6. Hooks module shape (`hooks/register.tsx`)

- `session.start`: `$.command.register({ name: "agent-toggle", description })`. Does **not** open the pane unasked.
- `command.run { command: "agent-toggle" }`: open pane, `refresh()`, return `{ text: "agent-toggle pane opened." }`.
- `ui.message`: a `Client` post `{ op: "toggle", type, name, mod }` toggles that row if the list holds it.
- `ui.scroll` on the pane: a `Client` gets no wheel events, and the picker never overflows its pane, so the
  engine has nothing to scroll. The hook keeps the window, adds `by` to the `wheel` atom, and the picker
  moves its cursor by the change. A list taller than the pane (the Button list) scrolls as usual.
- `ui.render { component: "Pane", requestId: "agent-toggle" }`: on terminal and
  desktop (decided by `e.surface`: the element table lists `Client` everywhere) a
  `Client` of `picker.tsx` as tall as the pane's `scroll.bodyRows` or the content,
  whichever is less. The picker's window follows the cursor and keeps a group
  heading above its first row. Elsewhere, a `Button` per row.
- `picker.tsx` is a surface module: no `$`, so it only draws and posts. It
  keeps its own copy of GROUPS (a hooks module and a surface module share no code).
- One helper `cli(argv)` wraps `$.process.run`, parses JSON, and
  stores `error`. Every call goes through it.

## 7. Verification

| check | command |
|---|---|
| manifest + hook surface | `claude plugin validate mod` |
| types | `tsc -p mod` (after first load lays `.claude-plugin/types/`) |
| behaviour | `claude plugin test mod` — `hooks/*.test.ts`, driven by `ui.key`, reading the picker's lines with `ui.findAll({ in: 'picker' })`; the core one: a fake `process.run` returning two rows, press Space on the first, assert the second `process.run` argv is `["agent-toggle","disable","skill","a","--json","--harness","claude"]` and the toast is `ok` |
| manual | `claude --plugin-dir ./mod`, `/agent-toggle`, toggle one skill, confirm with `agent-toggle status` |

CI: one extra job `mod` on `ubuntu-latest` that runs validate + test. It
needs the `claude` CLI on the runner; if that is not available in CI the job
is marked `continue-on-error` until it is.

## 8. Release

`mod/.claude-plugin/plugin.json` `version` tracks `__version__` in
`agent_toggle/__init__.py`; nothing bumps or checks it yet, so bump it by hand
at release: a GitHub install runs the copy made at install time, and `claude plugin update`
only fetches a new one when the version changes. README carries the marketplace install line
under Install and the `--plugin-dir` way to run a checkout under "Claude Code pane mod".

## 9. Decisions log

- 2026-10-10 — pane mod, not curses embedding: no PTY element in the mod API.
- 2026-10-10 — same repo, `mod/` subfolder: coupling is the `--json` schema; see §2.
- 2026-10-10 — `cost --json` as the one read: carries enabled + tokens in one call.
- 2026-10-10 — claude harness only in v1: the pane runs inside Claude; other
  harnesses keep the curses picker.
- 2026-10-10 — v1 drops search-by-key, sort, profiles, project scope: each is a
  later Button or Input, none changes the data flow.
- 2026-10-10 — the pane is a `Client` surface module, not Buttons + Input: it
  is the only way to get arrow keys, Space, `/` search, a cursor row and the
  picker's colours. Cost: one click to focus, and a second file with its own copy of GROUPS.
- 2026-10-10 — root `marketplace.json` added so the mod installs with one `/plugin install`
  line; the repo is the marketplace, no separate repo.

## 10. Open questions

- A `Client` takes keys only after a click gives it focus, and Esc returns the
  focus to the prompt (so Esc cannot clear the query). Is there a way to focus it
  when the pane opens, so no click is needed?
- The test harness reads the text `picker.tsx` draws (`ui.findAll({ in: 'picker' })`), but not
  its elements' keys or colours: colour is still checked by eye.
- Is `claude` available on GitHub-hosted runners for `claude plugin test`? Decides
  whether the CI job is blocking.
