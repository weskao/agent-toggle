import type { TestBody } from 'claude-code/testing'
import { describe, expect, mock, test } from 'claude-code/testing'

type T = Parameters<TestBody>[0]
type On = Parameters<TestBody>[1]

const PANE = { title: 'agent-toggle', isFocused: true, bodyColumns: 80, placement: 'inline', scroll: { offset: 0, bodyRows: 20 }, view: {} } as const
const skills = (n: number) => Array.from({ length: n }, (_, i) => ({ type: 'skill', name: `s${String(i).padStart(2, '0')}`, enabled: true, tokens: 10, would_save: 10, mod: false }))

// Opens the pane over a faked `cost --json` holding `n` skills; returns the drawn picker.
async function open($: T, on: On, n: number) {
  mock.env(on, {})
  const stdout = JSON.stringify({ ok: true, command: 'cost', results: skills(n) })
  on('process.run', () => ({ value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  await $.command.run({ command: 'agent-toggle', args: '', origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 120 } })
  const ui = await $.ui.mount({ plugin: 'agent-toggle', surface: 'terminal', component: 'Pane', requestId: 'agent-toggle', props: PANE })
  const lines = async () => (await ui.findAll({ in: 'picker', type: 'Text' })).map(t => t.text)
  return { ui, lines }
}

describe('picker scroll cues', () => {
  test('the status line carries the cursor position, as the curses picker does', async ($, on) => {
    const { ui } = await open($, on, 3)
    const status = async () => (await ui.find({ in: 'picker', type: 'Text', text: / shown/ }))?.text // a Client's elements carry no key
    expect(await status()).toBe(' 3 of 3 shown · 1/3')
    await ui.key({ key: 'down' })
    expect(await status()).toBe(' 3 of 3 shown · 2/3')
    for (const key of 'zzz') await ui.key({ key }) // nothing matches: no position
    expect(await status()).toBe(' 0 of 3 shown')
  })

  test('the first and last list lines count the rows scrolled out above and below', async ($, on) => {
    const { ui, lines } = await open($, on, 30)
    await ui.resize({ in: 'picker', columns: 80, rows: 10 }) // 5 list lines: the heading and s00-s03
    const marks = async () => (await lines()).map(t => /\S.* ([↑↓] \d+)$/.exec(t)?.[1]).filter(Boolean) // whole lines, not the count's own Text
    expect(await marks()).toEqual(['↓ 26'])
    await ui.key({ key: 'end' }) // s25-s29 shown: the heading and s00-s24 above
    expect(await marks()).toEqual(['↑ 25'])
    await ui.key({ key: 'up' })
    await ui.key({ key: 'up' })
    await ui.key({ key: 'up' })
    await ui.key({ key: 'up' })
    await ui.key({ key: 'up' }) // s24-s28: one more above is gone from view, s29 below
    expect(await marks()).toEqual(['↑ 24', '↓ 1'])
  })

  test('a list that fits shows no counts', async ($, on) => {
    const { lines } = await open($, on, 3)
    expect((await lines()).filter(t => /[↑↓] \d+$/.test(t))).toEqual([])
  })
})
