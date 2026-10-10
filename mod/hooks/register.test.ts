import type { TestBody } from 'claude-code/testing'
import { describe, expect, test } from 'claude-code/testing'

type T = Parameters<TestBody>[0]
type On = Parameters<TestBody>[1]

const row = (type: string, name: string, enabled: boolean, tokens: number, mod = false) => ({ type, name, enabled, tokens, would_save: tokens, mod })
const cost = (...rows: object[]) => JSON.stringify({ ok: true, command: 'cost', results: [...rows, { type: null, name: null, detail: 'total', tokens: 150 }] })
const COST = cost(row('skill', 'a', true, 100), row('skill', 'b', false, 50))
const done = (exitCode: number, stdout: string) => ({ exitCode, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false })
const OK = done(0, JSON.stringify({ ok: true, results: [{ status: 'ok' }] }))
const PANE = { title: 'agent-toggle', isFocused: true, bodyColumns: 80, placement: 'inline', scroll: { offset: 0, bodyRows: 20 }, view: {} } as const

// Opens the pane against a faked CLI: every run answers `list` except a disable/enable, which answers `toggle`.
async function open($: T, on: On, list = COST, toggle = OK) {
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

  test('a pane with no list yet spins a Loading resources line, a frame at a time', async ($, _on) => {
    const ui = await $.ui.mount({ plugin: 'agent-toggle', surface: 'terminal', component: 'Pane', requestId: 'agent-toggle', props: PANE })
    const text = async () => (await ui.find({ type: 'Text', text: /Loading resources/, in: 'picker' }))?.text
    expect(await text()).toMatch(/⠋ Loading resources…/)
    await ui.advance(80)
    expect(await text()).toMatch(/⠙ Loading resources…/)
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
    const closed: string[] = []
    let opened = 0
    let runs = 0
    on('ui.close', (_$, e) => (closed.push(e.id), { value: undefined }))
    on('ui.open', () => ((opened += 1), { value: { isPlaced: true } }))
    on('process.run', () => ((runs += 1), { value: done(0, COST) }))
    const r = await $.command.run({ command: 'agent-toggle', args: '  Close mods pane', origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 120 } })
    expect({ text: r.text, closed, opened, runs }).toEqual({ text: 'agent-toggle pane closed.', closed: ['agent-toggle'], opened: 0, runs: 0 })
  })

  test('other arguments still open the pane', async ($, on) => {
    on('ui.open', () => ({ value: { isPlaced: true } }))
    on('process.run', () => ({ value: done(0, COST) }))
    const r = await $.command.run({ command: 'agent-toggle', args: 'closet', origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 120 } })
    expect(r.text).toBe('agent-toggle pane opened.')
  })
})
