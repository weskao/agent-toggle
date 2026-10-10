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

async function refresh($: Engine) {
  const out = await cli($, ['agent-toggle', 'cost', '--json', '--harness', 'claude'])
  if (!out) return
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

async function toggle($: Engine, row: Row) {
  try {
    await exclusive($, async () => {
      await update($, error, () => '')
      // --harness: disable/enable otherwise fall back to the user's settings default_harness
      const out = await cli($, ['agent-toggle', row.enabled ? 'disable' : 'enable', row.type, row.name, '--json', '--harness', 'claude'])
      $.ui.toast(out ? (out.needs_new_session ? 'ok — takes effect in a new session' : 'ok') : await read($, error))
      await refresh($)
    })
  } catch (err) {
    $.ui.toast(String(err))
  }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'agent-toggle', description: 'Toggle Claude Code resources in a pane' })

    return next(e)
  })

  on('command.run', { command: 'agent-toggle' }, async $ => {
    const opened = await $.ui.open({ id: PANE, title: 'agent-toggle' })
    if (!opened.isPlaced) $.ui.toast('agent-toggle: no surface could place the pane')
    await update($, error, () => '')
    await update($, busy, () => false) // state survives a hot reload: a cut-off call would leave busy true and drop every press
    await exclusive($, () => refresh($))

    return { text: 'agent-toggle pane opened.' }
  })

  // A Client's post is input, not fact: toggle only a row the list holds, in the state the list has it.
  on('ui.message', async ($, e, next) => {
    const d = e.data as { op?: string; type?: string; name?: string; mod?: boolean } | null
    if (e.element === 'picker' && d?.op === 'toggle') {
      const row = (await read($, rows)).find(r => r.type === d.type && r.name === d.name && r.mod === (d.mod === true))
      if (row) await toggle($, row)
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
