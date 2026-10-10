import { atom, read, update } from 'claude-code'
import type { EngineInterface as Engine, Register } from 'claude-code'

import type { Row } from '../types'

const PANE = 'agent-toggle'
const rows = atom({ plugin: 'agent-toggle', key: 'rows' } as const, [])
const busy = atom({ plugin: 'agent-toggle', key: 'busy' } as const, false)
const error = atom({ plugin: 'agent-toggle', key: 'error' } as const, '')
// the wheel ticks over the picker, summed: picker.tsx moves its cursor by the change
const wheel = atom({ plugin: 'agent-toggle', key: 'wheel' } as const, 0)

type Result = { ok?: boolean; needs_new_session?: boolean; results?: Array<Record<string, unknown>> }

// picker group order (agent_toggle/ui/model.py GROUPS): mods sit right after plugins; picker.tsx keeps its own copy
const GROUPS = ['skill', 'agent', 'command', 'rule', 'plugin', 'mod', 'mcp']
const groupOf = (r: Row) => (r.mod ? 'mod' : r.type)
const rank = (r: Row) => {
  const i = GROUPS.indexOf(groupOf(r))
  return i < 0 ? GROUPS.length : i
}

// Runs the CLI (argv, no shell), parses its JSON; on failure stores `error` and returns null.
async function cli($: Engine, argv: string[]): Promise<Result | null> {
  try {
    const run = await $.process.run(argv)
    let json: Result | null = null
    try {
      json = JSON.parse(run.stdout) as Result
    } catch {}
    if (run.exitCode === 0 && json) return json
    // a failed disable/enable prints its reason as JSON on stdout, not stderr
    const why = run.stderr.split('\n')[0] || String(json?.results?.[0]?.detail ?? '')
    await update($, error, () => why || (json ? `exit ${run.exitCode}` : 'could not parse agent-toggle output'))
    return null
  } catch (err) {
    await update($, error, () => String(err))
    return null
  }
}

// Holds `busy` for the whole of fn (toggle + refresh); a call while busy is dropped.
// The claim is made inside update's ifVersion retry loop, so simultaneous presses cannot both get it.
async function exclusive($: Engine, fn: () => Promise<void>) {
  let got = false
  await update($, busy, b => ((got = !b), true))
  if (!got) return
  try {
    await fn()
  } finally {
    await update($, busy, () => false)
  }
}

// What `/agent-toggle <action>` and the model's `pane` tool both do; an empty argument opens.
const ACTIONS = ['open', 'close', 'toggle']
type Action = 'open' | 'close' | 'toggle'
const isAction = (s: string): s is Action => ACTIONS.includes(s)
const USAGE = 'usage: /agent-toggle [open | close | toggle | disable|enable <type> <name>... | undo | list | status | cost | doctor]'
const TOOL = 'mcp__agent-toggle__pane'

async function pane($: Engine, action: Action): Promise<string> {
  const isOpen = action === 'toggle' && (await $.ui.panes()).some(p => p.id === PANE)
  if (action === 'close' || isOpen) {
    await $.ui.close({ id: PANE })
    return 'agent-toggle pane closed.'
  }
  await update($, rows, () => [])
  const opened = await $.ui.open({ id: PANE, title: 'agent-toggle' })
  if (!opened.isPlaced) $.ui.toast('agent-toggle: no surface could place the pane')
  await update($, error, () => '')
  await update($, busy, () => false) // state survives a hot reload: a cut-off call would leave busy true and drop every press
  await exclusive($, () => refresh($))
  return 'agent-toggle pane opened.'
}

// Bumped by each toggle: a refresh that started before one would draw that row's old state.
// A module variable, not an atom: every read of one dispatch sees one moment. A hot reload resets it, harmlessly.
let seq = 0

// Each row moves as cost --json reports it: a parked row costs 0 and would save what it cost. Applied twice it undoes itself.
const flip = (rows: Row[]) => (list: Row[]) =>
  list.map(r => (rows.some(x => x.type === r.type && x.name === r.name && x.mod === r.mod) ? { ...r, enabled: !r.enabled, tokens: r.save, save: r.tokens } : r))

async function refresh($: Engine) {
  const at = seq
  const out = await cli($, ['agent-toggle', 'cost', '--json', '--harness', 'claude'])
  if (!out || at !== seq) return
  // cost --json ends with a summary row (type/name null): not a resource
  const list: Row[] = (out.results ?? []).filter(r => r.type != null && r.name != null).map(r => ({
    type: String(r.type),
    name: String(r.name),
    enabled: r.enabled === true,
    tokens: Number(r.tokens) || 0,
    save: Number(r.would_save) || 0,
    mod: r.mod === true,
  }))
  await update($, rows, () => list)
}

// `same`: every row named like `row` that is in `row`'s state, as the curses `n` key (`disable|enable all <name>`, one batch for undo)
async function toggle($: Engine, row: Row, same?: Row[]) {
  const moved = same ?? [row]
  try {
    let ran = false
    await exclusive($, async () => {
      ran = true
      seq++
      await update($, error, () => '')
      await update($, rows, flip(moved)) // drawn now, not after the CLI and the refresh
      // --harness: disable/enable otherwise fall back to the user's settings default_harness
      const out = await cli($, ['agent-toggle', row.enabled ? 'disable' : 'enable', same ? 'all' : row.type, row.name, '--json', '--harness', 'claude'])
      const type = same ? 'all' : row.mod ? 'mod' : row.type
      if (out) {
        const note = out.needs_new_session ? 'takes effect in a new session' : ''
        // a toast is plain text (no colour option, ANSI codes show as garbage): an emoji and an ASCII tag mark the state;
        // the tag still reads where the emoji draws as a box (old Windows consoles)
        $.ui.toast(`${row.enabled ? '⛔ [DISABLED]' : '✅ [ENABLED]'} ${type}: ${row.name}${note && ` (${note})`}`)
      } else {
        await update($, rows, flip(moved))
        $.ui.toast(`❌ [FAILED] ${row.enabled ? 'disable' : 'enable'} ${type}: ${row.name} - ${await read($, error)}`)
      }
    })
    // outside busy: the next press need not wait ~1s for `claude plugin list`; seq drops this refresh if a toggle lands
    if (ran) await refresh($)
  } catch (err) {
    $.ui.toast(String(err))
  }
}

// `/agent-toggle <verb> ...` runs the CLI instead of opening the pane; any other argument still opens it.
// `--` spellings too: the CLI accepts both (CLAUDE.md "Every command has two spellings").
const VERB = /^(?:--)?(disable|enable|undo|list|status|cost|doctor)$/i

// Runs `/agent-toggle <args>` as the CLI (argv, no shell) and answers with one line per result row.
async function runArgs($: Engine, words: string[]): Promise<string> {
  const argv = ['agent-toggle', ...words, '--json']
  // like the pane: without --harness the CLI falls back to the user's settings default_harness
  if (!words.some(w => w === '--harness' || w.startsWith('--harness='))) argv.push('--harness', 'claude')
  try {
    const run = await $.process.run(argv)
    let out: (Result & { warnings?: unknown[] }) | null = null
    try {
      out = JSON.parse(run.stdout)
    } catch {}
    if (!out) return `❌ agent-toggle exited ${run.exitCode}: ${run.stderr.trim() || run.stdout.trim() || 'no output'}`
    const lines = (out.results ?? [])
      .filter(r => r.type != null || r.name != null) // cost/list end with a summary row
      .map(r => [r.status, r.type, r.name, r.detail && `- ${r.detail}`].filter(v => v != null && v !== '').join(' '))
    for (const w of out.warnings ?? []) lines.push(`⚠️ ${typeof w === 'string' ? w : JSON.stringify(w)}`)
    if (out.needs_new_session) lines.push('Takes effect in a new session.')
    if (run.exitCode !== 0) lines.unshift(`❌ agent-toggle exited ${run.exitCode}${run.stderr.trim() && `: ${run.stderr.trim().split('\n')[0]}`}`)
    return lines.join('\n') || `agent-toggle ${words[0]}: nothing to report.`
  } catch (err) {
    return `❌ ${String(err)}`
  }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    // Left deferred (behind ToolSearch): no schema cost in every prompt, the name is enough to find it.
    await $.tool.register({
      name: 'pane',
      description: "Opens, closes or toggles the agent-toggle picker pane in the person's Claude Code session. Use when they ask to open/show or close/hide agent-toggle.",
      inputSchema: { type: 'object', properties: { action: { type: 'string', enum: ACTIONS } }, required: ['action'] },
    })
    // Refused when the install-shims skill already owns /agent-toggle; command.run still reaches this mod then.
    await $.command.register({
      name: 'agent-toggle',
      description: 'Toggle Claude Code resources in a pane',
      argumentHint: '[open | close | toggle | disable|enable <type> <name>... | undo | list | status | cost | doctor]',
    }).catch(() => {})

    return next(e)
  })

  on('command.run', { command: 'agent-toggle' }, async ($, e) => {
    const words = e.args.trim().split(/\s+/)
    if (VERB.test(words[0] ?? '')) {
      const text = await runArgs($, words)
      // a toggle: an open pane would keep drawing the old state; seq drops a pane refresh already in flight
      if (/^(?:--)?(disable|enable|undo)$/i.test(words[0]!)) {
        seq++
        if ((await read($, rows)).length > 0) await exclusive($, () => refresh($))
      }
      return { text }
    }
    const action = words[0].toLowerCase() || 'open'
    return { text: isAction(action) ? await pane($, action) : USAGE }
  })

  // the model's `pane` tool (registered in session.start): "open/close agent-toggle" in a prompt lands here
  on('tool.call', { tool: TOOL }, async ($, e) => {
    const action = String((e as { action?: unknown }).action) // a tool's input fields sit on e itself
    return isAction(action) ? { result: await pane($, action) } : { deny: `action must be one of: ${ACTIONS.join(', ')}` }
  })

  // A Client's post is input, not fact: toggle only a row the list holds, in the state the list has it.
  on('ui.message', async ($, e, next) => {
    const d = e.data as { op?: string; type?: string; name?: string; mod?: boolean } | null
    if (e.element === 'picker' && (d?.op === 'toggle' || d?.op === 'same')) {
      const list = await read($, rows)
      const row = list.find(r => r.type === d.type && r.name === d.name && r.mod === (d.mod === true))
      if (row) await toggle($, row, d.op === 'same' ? list.filter(r => r.name === row.name && r.enabled === row.enabled) : undefined)
    }

    return next(e)
  })

  // the picker is no taller than its pane, so the engine has nothing to scroll: hand the ticks to the picker
  on('ui.scroll', { requestId: PANE }, async ($, e, next) => {
    if (e.contentRows > e.bodyRows) return next(e) // the Button list elsewhere scrolls as usual
    await update($, wheel, n => n + e.by)
    return {}
  }).catch(($, e, next) => (next.called ? next(e) : {})) // a failed tick is dropped, not handed to the engine

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const ui = $.ui.resolve(e)
    const { Box, Button, Text } = ui
    const [all, isBusy, err, w] = await Promise.all([read($, rows), read($, busy), read($, error), read($, wheel)])

    // terminal and desktop: the picker itself, drawn with its own keys and scrolling (picker.tsx)
    if ('Client' in ui && (e.surface === 'terminal' || e.surface === 'desktop')) { // the table reports Client on every surface, so the surface decides
      const need = all.length + GROUPS.length + 5 // rows, a heading per group, the chrome
      return <ui.Client key="picker" module="./picker.tsx" width="100%" height={Math.min(e.props.scroll?.bodyRows ?? 24, need)} props={{ rows: all, busy: isBusy, error: err, wheel: w }} />
    }

    // mobile and vscode draw no Client: one Button per row, the surface scrolls
    const shown = [...all].sort((a, b) => rank(a) - rank(b)) // stable
    return (
      <Box flexDirection="column">
        <Text dimColor>
          {shown.length} resources{isBusy ? '  working...' : ''}
        </Text>
        {err !== '' && <Text color="red">{err}  (run `agent-toggle doctor`)</Text>}
        {shown.map(r => (
          <Button key={`${r.type}/${r.name}`} dimColor={isBusy || !r.enabled} onPress={() => void toggle($, r)}>
            {r.enabled ? '●' : '○'} {r.type}/{r.name}  {r.enabled ? r.tokens : `(${r.save})`}
          </Button>
        ))}
      </Box>
    )
  })
}
