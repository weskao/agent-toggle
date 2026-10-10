import type { TestBody } from 'claude-code/testing'
import { describe, expect, mock, test } from 'claude-code/testing'

type T = Parameters<TestBody>[0]
type On = Parameters<TestBody>[1]

const row = (type: string, name: string, enabled: boolean, tokens: number, mod = false) => ({ type, name, enabled, tokens, would_save: tokens, mod })
const cost = (...rows: object[]) => JSON.stringify({ ok: true, command: 'cost', results: [...rows, { type: null, name: null, detail: 'total', tokens: 150 }] })
const COST = cost(row('skill', 'a', true, 100), row('skill', 'b', false, 50))
const done = (exitCode: number, stdout: string) => ({ exitCode, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false })
const OK = done(0, JSON.stringify({ ok: true, results: [{ status: 'ok' }] }))
const PANE = { title: 'agent-toggle', isFocused: true, bodyColumns: 80, placement: 'inline', scroll: { offset: 0, bodyRows: 20 }, view: {} } as const

// Opens the pane against a faked CLI: every run answers `list` except a disable/enable, which answers `toggle`.
async function open($: T, on: On, list = COST, toggle = OK, env: Record<string, string> = {}) {
  mock.env(on, env) // not the host's: TERM and OS decide whether toasts carry emoji
  const argvs: string[][] = []
  const toasts: string[] = []
  on('process.run', async (_$, e) => {
    argvs.push([...e.argv])
    if (e.argv[1] === 'cost') return { value: done(0, list) }
    for (let i = 0; i < 50; i++) await Promise.resolve() // keep a toggle in flight while a second key lands
    return { value: toggle }
  })
  on('ui.toast', (_$, e) => {
    toasts.push(e.text)
    return { value: undefined }
  })
  on('ui.open', () => ({ value: { isPlaced: true } }))
  await $.command.run({ command: 'agent-toggle', args: '', origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 120 } })
  const ui = await $.ui.mount({ plugin: 'agent-toggle', surface: 'terminal', component: 'Pane', requestId: 'agent-toggle', props: PANE })
  const tick = async () => {
    for (let i = 0; i < 500; i++) await Promise.resolve() // lets a toggle and its refresh finish
  }
  const toggles = () => argvs.filter(a => a[1] === 'disable' || a[1] === 'enable').map(a => a.slice(1, 4).join(' '))
  const type = async (s: string) => {
    for (const key of s) await ui.key({ key })
  }
  return { argvs, toasts, ui, toggles, type, tick }
}

describe('agent-toggle pane', () => {
  test('Space disables the highlighted row and toasts ok', async ($, on) => {
    const { argvs, toasts, ui, tick } = await open($, on)
    await ui.key({ key: ' ' })
    await tick()
    expect(argvs[0]).toEqual(['agent-toggle', 'cost', '--json', '--harness', 'claude'])
    expect(argvs[1]).toEqual(['agent-toggle', 'disable', 'skill', 'a', '--json', '--harness', 'claude'])
    expect(toasts).toContain('⛔ [DISABLED] skill: a')
  })

  test('the arrows and End move the cursor; the cost summary row (null type/name) is not a row', async ($, on) => {
    const { toggles, ui, tick } = await open($, on)
    await ui.key({ key: 'down' })
    await ui.key({ key: ' ' })
    await tick()
    await ui.key({ key: 'end' })
    await ui.key({ key: ' ' })
    await tick()
    expect(toggles()).toEqual(['enable skill b', 'enable skill b']) // End stops on the last real row
  })

  test('a failed disable toasts the reason from stdout', async ($, on) => {
    const { toasts, ui, tick } = await open($, on, COST, done(1, JSON.stringify({ ok: false, results: [{ status: 'error', detail: 'not found' }] })))
    await ui.key({ key: ' ' })
    await tick()
    expect(toasts).toContain('❌ [FAILED] disable skill: a - not found')
  })

  test('a toggle that needs a new session says so', async ($, on) => {
    const { toasts, ui, tick } = await open($, on, COST, done(0, JSON.stringify({ ok: true, needs_new_session: true, results: [{ status: 'ok' }] })))
    await ui.key({ key: ' ' })
    await tick()
    expect(toasts).toContain('⛔ [DISABLED] skill: a (takes effect in a new session)')
  })

  test('two simultaneous keys run exactly one toggle', async ($, on) => {
    const { toggles, ui, tick } = await open($, on)
    await Promise.all([ui.key({ key: ' ' }), ui.key({ key: 'return' })])
    await tick()
    expect(toggles()).toHaveLength(1)
  })

  test('rows of mixed types walk in GROUPS order', async ($, on) => {
    const mixed = cost(row('mcp', 'x', true, 1), row('plugin', 'p', true, 1), row('skill', 's', true, 1), row('agent', 'g', true, 1), row('plugin', 'm', true, 1, true))
    const { toggles, ui, tick } = await open($, on, mixed)
    for (let i = 0; i < 5; i++) {
      await ui.key({ key: 'home' })
      for (let j = 0; j < i; j++) await ui.key({ key: 'down' })
      await ui.key({ key: ' ' })
      await tick()
    }
    expect(toggles()).toEqual(['disable skill s', 'disable agent g', 'disable plugin p', 'disable plugin m', 'disable mcp x'])
  })

  test('typing filters: terms are ANDed and a second Space toggles', async ($, on) => {
    const { toggles, type, ui, tick } = await open($, on)
    await type('ki b ') // "ki" matches both skills, "b" narrows to skill/b; the trailing Space is the toggle
    await ui.key({ key: ' ' })
    await tick()
    expect(toggles()).toEqual(['enable skill b'])
  })

  test('letters in order find a name when nothing matches exactly', async ($, on) => {
    const { toggles, type, ui, tick } = await open($, on, cost(row('skill', 'context-md', true, 5), row('skill', 'other', true, 5)))
    await type('ctxmd')
    await ui.key({ key: 'return' })
    await tick()
    expect(toggles()).toEqual(['disable skill context-md'])
  })

  test('s sorts by cost, biggest first, while no filter is typed', async ($, on) => {
    const { toggles, type, ui, tick } = await open($, on, cost(row('skill', 'a', true, 100), row('skill', 'b', true, 900)))
    await type('s')
    await ui.key({ key: ' ' })
    await tick()
    expect(toggles()).toEqual(['disable skill b'])
  })

  test('a posted toggle for a row the list does not hold is ignored', async ($, on) => {
    const { toggles, ui, tick } = await open($, on)
    await ui.post({ op: 'toggle', type: 'skill', name: 'nope', mod: false })
    await tick()
    expect(toggles()).toEqual([])
  })

  test('a failed toggle leaves its error in the picker after the refresh succeeds', async ($, on) => {
    const { ui, tick } = await open($, on, COST, done(1, JSON.stringify({ ok: false, results: [{ status: 'error', detail: 'not found' }] })))
    await ui.key({ key: ' ' })
    await tick()
    expect(((await ui.drawn()) as any).props.props.error).toBe('not found')
  })

  test('a toggled row flips before the CLI answers, and back when it fails', async ($, on) => {
    let release = () => {}
    const gate = new Promise<void>(r => (release = r))
    on('process.run', async (_$, e) => {
      if (e.argv[1] === 'cost') return { value: done(0, COST) }
      await gate // the disable is in flight until the test lets it answer
      return { value: done(1, JSON.stringify({ ok: false, results: [{ status: 'error', detail: 'not found' }] })) }
    })
    on('ui.toast', () => ({ value: undefined }))
    on('ui.open', () => ({ value: { isPlaced: true } }))
    await $.command.run({ command: 'agent-toggle', args: '', origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 120 } })
    const ui = await $.ui.mount({ plugin: 'agent-toggle', surface: 'terminal', component: 'Pane', requestId: 'agent-toggle', props: PANE })
    const first = async () => ((await ui.drawn()) as any).props.props.rows[0]
    const pressed = ui.key({ key: ' ' })
    for (let i = 0; i < 500; i++) await Promise.resolve() // the toggle reaches the CLI
    const mid = await first()
    release()
    await pressed
    expect({ mid: [mid.enabled, mid.save], end: (await first()).enabled }).toEqual({ mid: [false, 100], end: true })
  })

  test('n toggles every type with the row name, as one disable all, flipping only rows in its state', async ($, on) => {
    const same = cost(row('skill', 'demo', true, 10), row('command', 'demo', true, 10), row('mcp', 'demo', false, 10), row('skill', 'other', true, 10))
    let release = () => {}
    const gate = new Promise<void>(r => (release = r))
    const argvs: string[][] = []
    on('process.run', async (_$, e) => {
      argvs.push([...e.argv])
      if (e.argv[1] === 'cost') return { value: done(0, same) }
      await gate
      return { value: OK }
    })
    on('ui.toast', () => ({ value: undefined }))
    on('ui.open', () => ({ value: { isPlaced: true } }))
    await $.command.run({ command: 'agent-toggle', args: '', origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 120 } })
    const ui = await $.ui.mount({ plugin: 'agent-toggle', surface: 'terminal', component: 'Pane', requestId: 'agent-toggle', props: PANE })
    const pressed = ui.key({ key: 'n' }) // the cursor starts on skill/demo
    for (let i = 0; i < 500; i++) await Promise.resolve()
    const mid = ((await ui.drawn()) as any).props.props.rows.map((r: { type: string; name: string; enabled: boolean }) => `${r.type}/${r.name} ${r.enabled}`)
    release()
    await pressed
    expect(argvs[1]).toEqual(['agent-toggle', 'disable', 'all', 'demo', '--json', '--harness', 'claude'])
    expect(mid).toEqual(['skill/demo false', 'command/demo false', 'mcp/demo false', 'skill/other true'])
  })

  test('n types into a filter once one is started', async ($, on) => {
    const { toggles, type, ui, tick } = await open($, on, cost(row('skill', 'an', true, 5), row('skill', 'b', true, 5)))
    await type('/an')
    await ui.key({ key: 'return' })
    await tick()
    expect(toggles()).toEqual(['disable skill an'])
  })

  test('a pane with no list yet spins a Loading resources line, a frame at a time', async ($, _on) => {
    const ui = await $.ui.mount({ plugin: 'agent-toggle', surface: 'terminal', component: 'Pane', requestId: 'agent-toggle', props: PANE })
    const text = async () => (await ui.find({ type: 'Text', text: /Loading resources/, in: 'picker' }))?.text
    expect(await text()).toMatch(/⠋ Loading resources…/)
    await ui.advance(80)
    expect(await text()).toMatch(/⠙ Loading resources…/)
  })

  test('a legacy Windows console toasts the tag alone, no emoji', async ($, on) => {
    const { toasts, ui, tick } = await open($, on, COST, OK, { OS: 'Windows_NT' })
    await ui.key({ key: ' ' })
    await tick()
    expect(toasts).toEqual(['[DISABLED] skill: a'])
  })

  test('Windows Terminal keeps the emoji', async ($, on) => {
    const { toasts, ui, tick } = await open($, on, COST, OK, { OS: 'Windows_NT', WT_SESSION: 'test-session' })
    await ui.key({ key: ' ' })
    await tick()
    expect(toasts).toEqual(['⛔ [DISABLED] skill: a'])
  })

  test('the Linux console gets [FAILED] in place of the emoji in CLI output', async ($, on) => {
    mock.env(on, { TERM: 'linux' })
    on('process.run', () => ({ value: done(1, JSON.stringify({ ok: false, results: [{ type: 'skill', name: 'x', status: 'error', detail: 'not found' }] })) }))
    const r = await $.command.run({ command: 'agent-toggle', args: 'enable skill x', origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 120 } })
    expect(r.text).toBe('[FAILED] agent-toggle exited 1\nerror skill x - not found')
  })

  test('a toast names a mod as a mod, with no escape codes', async ($, on) => {
    const { toasts, ui, tick } = await open($, on, cost(row('plugin', 'm', false, 7, true)))
    await ui.key({ key: ' ' })
    await tick()
    expect(toasts).toEqual(['✅ [ENABLED] mod: m'])
    expect(/[\x00-\x1f]/.test(toasts[0]!)).toBe(false) // an ANSI colour code would show as garbage
  })

  test('a surface with no Client draws one Button per row', async ($, on) => {
    const { argvs, tick } = await open($, on)
    const ui = await $.ui.mount({ plugin: 'agent-toggle', surface: 'vscode', component: 'Pane', requestId: 'agent-toggle', props: PANE })
    await ui.press({ key: 'skill/a' })
    await tick()
    expect(argvs[1]).toEqual(['agent-toggle', 'disable', 'skill', 'a', '--json', '--harness', 'claude'])
  })

  test('a wheel tick over the picker moves the cursor; over a list taller than the pane it scrolls as usual', async ($, on) => {
    const reached: number[] = []
    on('ui.scroll', (_$, e) => (reached.push(e.by), {}))
    const { toggles, ui, tick } = await open($, on)
    const tickDown = (contentRows: number) => $.ui.scroll({ component: 'Pane', requestId: 'agent-toggle', offset: 0, by: 1, bodyRows: 20, contentRows, origin: { kind: 'person' } })
    await ui.advance(16) // a frame: the picker records where the wheel stood when it opened
    await tickDown(9) // the picker fits the pane: the tick is the picker's
    await tick()
    await ui.advance(16)
    await ui.key({ key: ' ' })
    await tick()
    await tickDown(40) // the Button list on vscode/mobile: the engine scrolls it
    expect({ toggles: toggles(), reached }).toEqual({ toggles: ['enable skill b'], reached: [1] })
  })

  test('/agent-toggle close closes the pane without opening it or running the CLI', async ($, on) => {
    const s = panes(on)
    const text = await cmd($, '  Close mods pane')
    expect({ text, closed: s.closed, opened: s.opened, runs: s.runs }).toEqual({ text: 'agent-toggle pane closed.', closed: ['agent-toggle'], opened: 0, runs: 0 })
  })

  test('no argument and open both open the pane', async ($, on) => {
    const s = panes(on)
    expect([await cmd($, ''), await cmd($, 'OPEN')]).toEqual(['agent-toggle pane opened.', 'agent-toggle pane opened.'])
    expect(s.opened).toBe(2)
  })

  test('toggle opens a closed pane and closes an open one', async ($, on) => {
    const s = panes(on)
    expect([await cmd($, 'toggle'), await cmd($, 'toggle')]).toEqual(['agent-toggle pane opened.', 'agent-toggle pane closed.'])
    expect({ opened: s.opened, closed: s.closed }).toEqual({ opened: 1, closed: ['agent-toggle'] })
  })

  test('an unknown argument shows the usage and touches no pane', async ($, on) => {
    const s = panes(on)
    expect(await cmd($, 'clsoe')).toBe('usage: /agent-toggle [open | close | toggle | disable|enable <type> <name>... | undo | list | status | cost | doctor]')
    expect({ opened: s.opened, closed: s.closed }).toEqual({ opened: 0, closed: [] })
  })

  test('the model opens and closes the pane through its pane tool', async ($, on) => {
    const s = panes(on)
    const call = (action: string) => $.tool.call({ tool: 'mcp__agent-toggle__pane', action } as never)
    const a = await call('open')
    const b = await call('close')
    const c = await call('explode')
    expect({ a: a.result, b: b.result, c: c.deny, opened: s.opened, closed: s.closed }).toEqual({
      a: 'agent-toggle pane opened.',
      b: 'agent-toggle pane closed.',
      c: 'action must be one of: open, close, toggle',
      opened: 1,
      closed: ['agent-toggle'],
    })
  })

  test('/agent-toggle disable runs the CLI with every name and reports each row, without opening the pane', async ($, on) => {
    const argvs: string[][] = []
    let opened = 0
    on('ui.open', () => ((opened += 1), { value: { isPlaced: true } }))
    on('process.run', (_$, e) => {
      argvs.push([...e.argv])
      return { value: done(0, JSON.stringify({ ok: true, results: [{ type: 'skill', name: 'a', status: 'disabled' }, { type: 'skill', name: 'b', status: 'disabled' }] })) }
    })
    const r = await $.command.run({ command: 'agent-toggle', args: ' disable skill a  b ', origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 120 } })
    expect({ argvs, opened, text: r.text }).toEqual({
      argvs: [['agent-toggle', 'disable', 'skill', 'a', 'b', '--json', '--harness', 'claude']],
      opened: 0,
      text: 'disabled skill a\ndisabled skill b',
    })
  })

  test('a failed CLI run from arguments says why; an explicit --harness is kept', async ($, on) => {
    const argvs: string[][] = []
    on('process.run', (_$, e) => {
      argvs.push([...e.argv])
      return { value: done(1, JSON.stringify({ ok: false, results: [{ type: 'skill', name: 'x', status: 'error', detail: 'not found' }] })) }
    })
    mock.env(on, {})
    const r = await $.command.run({ command: 'agent-toggle', args: '--enable skill x --harness codex', origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 120 } })
    expect({ argvs, text: r.text }).toEqual({
      argvs: [['agent-toggle', '--enable', 'skill', 'x', '--harness', 'codex', '--json']],
      text: '❌ agent-toggle exited 1\nerror skill x - not found',
    })
  })

  test('a toggle from arguments refreshes an open pane', async ($, on) => {
    const { argvs, tick } = await open($, on)
    await tick()
    await $.command.run({ command: 'agent-toggle', args: 'disable skill a', origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 120 } })
    await tick()
    expect(argvs.map(a => a[1])).toEqual(['cost', 'disable', 'cost'])
  })
})

// Fakes the pane surface and the CLI: tracks which panes are open, opens and closes, and CLI runs.
function panes(on: On) {
  const s = { open: new Set<string>(), opened: 0, closed: [] as string[], runs: 0 }
  on('ui.open', (_$, e) => (s.open.add(e.id), (s.opened += 1), { value: { isPlaced: true } }))
  on('ui.close', (_$, e) => (s.open.delete(e.id), s.closed.push(e.id), { value: undefined }))
  on('ui.panes', () => ({ value: [...s.open].map(id => ({ id, title: id, isShown: true })) as never }))
  on('process.run', () => ((s.runs += 1), { value: done(0, COST) }))
  return s
}

async function cmd($: T, args: string) {
  return (await $.command.run({ command: 'agent-toggle', args, origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 120 } })).text
}
