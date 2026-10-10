import type { ClientModule } from 'claude-code'

import type { Row } from '../types'

// The curses picker's look and keys (agent_toggle/ui/picker.py), drawn by the engine.
// Runs in a surface module: no `$`, so a toggle is posted to register.tsx's `ui.message` hook.
type Props = { rows: Row[]; busy: boolean; error: string; wheel: number }
type State = { cursor: number; top: number; query: string; typing: boolean; sort: 'name' | 'cost'; wheel: number; frame: number }
type Item = { head: string; count: number } | { row: Row }

// model.py GROUPS and type_label: mods sit right after plugins
const GROUPS = ['skill', 'agent', 'command', 'rule', 'plugin', 'mod', 'mcp']
const LABEL: Record<string, string> = { skill: 'Skills', agent: 'Agents', command: 'Commands', rule: 'Rules', plugin: 'Plugins', mod: 'Mods', mcp: 'MCP' }
const SPIN = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏' // spinner.py BRAILLE, what `agent-toggle ui` shows while it loads
const TICK = 80 // spinner.py INTERVAL
const EIGHTHS = ['▏', '▎', '▍', '▌', '▋', '▊', '▉']
const INIT: State = { cursor: 0, top: 0, query: '', typing: false, sort: 'name', wheel: 0, frame: 0 }
const CHROME = 5 // title, search, status, chips, and one spare for the error line

const groupOf = (r: Row) => (r.mod ? 'mod' : r.type)
const rank = (r: Row) => {
  const i = GROUPS.indexOf(groupOf(r))
  return i < 0 ? GROUPS.length : i
}
const tok = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n))
const cut = (s: string, n: number) => (s.length > n ? s.slice(0, Math.max(0, n - 1)) + '…' : s.padEnd(n))

// theme.cost_bar: eighth-cell resolution, any value > 0 shows a sliver
function bar(value: number, max: number, width: number) {
  const e = max > 0 && value > 0 ? Math.max(1, Math.min(width * 8, Math.round((Math.min(value, max) / max) * width * 8))) : 0
  return ('█'.repeat(Math.floor(e / 8)) + (e % 8 ? EIGHTHS[(e % 8) - 1] : '')).padEnd(width)
}

// Terms are ANDed over type/name and the group; with no hit, letters-in-order (fuzzy) on the name.
function filtered(rows: Row[], query: string) {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean)
  const hit = rows.filter(r => terms.every(t => `${r.type}/${r.name} ${r.mod ? 'mod mods' : ''}`.toLowerCase().includes(t)))
  if (hit.length || !terms.length) return { list: hit, fuzzy: false }
  const f = terms.join('')
  const list = rows.filter(r => {
    let i = 0
    for (const c of r.name.toLowerCase()) if (c === f[i]) i++
    return i === f.length
  })
  return { list, fuzzy: list.length > 0 }
}

// Whether the last draw has something to wait for; the timer below advances the frame only then, so an idle pane draws nothing.
let spinning = false

const Picker: ClientModule<Props, State> = (props, surface) => {
  const { Box, Text } = surface.elements
  const s = surface.state ?? INIT
  const w = surface.columns || 80
  const body = Math.max(3, (surface.rows || 24) - CHROME)

  const { list, fuzzy } = filtered(props.rows, s.query)
  const rows = [...list].sort((a, b) => rank(a) - rank(b) || (s.sort === 'cost' ? (b.enabled ? b.tokens : b.save) - (a.enabled ? a.tokens : a.save) : 0) || a.name.localeCompare(b.name))
  const items: Item[] = rows.flatMap((r, i) => (i === 0 || groupOf(rows[i - 1]!) !== groupOf(r) ? [{ head: groupOf(r), count: rows.filter(x => groupOf(x) === groupOf(r)).length }, { row: r }] : [{ row: r }]))
  const lineOf = (c: number) => items.findIndex(it => 'row' in it && it.row === rows[c])
  const cursor = Math.min(s.cursor, Math.max(0, rows.length - 1))
  // the window scrolls only as far as the cursor needs, and keeps the group heading above the first row
  const fit = (c: number, top: number) => {
    const at = lineOf(c)
    const t = at <= top ? (items[at - 1] && 'head' in items[at - 1]! ? at - 1 : at) : at >= top + body ? at - body + 1 : top
    return Math.max(0, Math.min(t, Math.max(0, items.length - body)))
  }
  const top = fit(cursor, s.top)
  const go = (c: number, more: Partial<State> = {}) => {
    const next = Math.max(0, Math.min(c, rows.length - 1))
    surface.setState({ ...s, ...more, cursor: next, top: fit(next, s.top) })
  }
  const toggle = (op = 'toggle') => {
    const r = rows[cursor]
    if (r && !props.busy) surface.post({ op, type: r.type, name: r.name, mod: r.mod })
  }
  // the wheel moves the cursor, as it sends Up/Down to the curses picker; ticks from before this instance are not ours
  const loading = props.rows.length === 0 && props.error === ''
  spinning = loading || props.busy
  const spin = SPIN[s.frame % SPIN.length]
  if (!surface.state) {
    surface.setState({ ...INIT, wheel: props.wheel })
    surface.every(TICK, () => {
      const cur = surface.state ?? INIT
      if (spinning) surface.setState({ ...cur, frame: cur.frame + 1 })
    })
  } else if (props.wheel !== s.wheel) go(cursor + props.wheel - s.wheel, { wheel: props.wheel })

  surface.onKey(e => {
    const k = e.key
    const searching = s.typing || s.query !== ''
    if (k === 'up' || (e.ctrl && k === 'p')) return go(cursor - 1)
    if (k === 'down' || (e.ctrl && k === 'n')) return go(cursor + 1)
    if (k === 'pageup') return go(cursor - body)
    if (k === 'pagedown') return go(cursor + body)
    if (k === 'home') return go(0)
    if (k === 'end') return go(rows.length - 1)
    if (k === 'return') return toggle()
    if (k === 'tab') return toggle(), go(cursor + 1)
    // a trailing Space is only the term separator: the rows, and so the cursor, stay
    if (k === 'backspace') return go(s.query.endsWith(' ') ? cursor : 0, { query: s.query.slice(0, -1), typing: s.query.length > 1 })
    if (e.ctrl && k === 'u') return go(0, { query: '', typing: false })
    if (k === ' ' || k === 'space') {
      if (!s.query.trim()) return toggle() // as the curses picker: no term yet, nothing to separate
      if (s.query.endsWith(' ')) return toggle(), go(cursor, { query: s.query.trimEnd() }) // second Space toggles
      return go(cursor, { query: s.query + ' ' })
    }
    if (k.length !== 1 || e.ctrl || e.meta) return
    if (!searching && k === 's') return go(0, { sort: s.sort === 'name' ? 'cost' : 'name' })
    if (!searching && k === 'n') return toggle('same') // every type with this row's name, as the curses `n`
    if (!searching && k === '/') return go(0, { typing: true })
    go(0, { query: s.query + k, typing: true })
  })
  surface.onPointer(e => {
    const at = top + e.y - 2 // below the title and search lines
    const it = e.type === 'down' && e.button === 'left' ? items[at] : undefined
    if (it && 'row' in it) go(rows.indexOf(it.row))
  })

  const topCost = Math.max(0, ...rows.map(r => (r.enabled ? r.tokens : r.save)))
  const live = props.rows.reduce((n, r) => n + (r.enabled ? r.tokens : 0), 0)
  const summary = `${props.rows.length} resources · ~${tok(live)} tok live`
  const chips: Array<[string, string]> = [['Space', 'toggle'], ['/', 'search'], ['s', 'sort'], ['n', 'same name'], ['↑↓', 'move'], ['^U', 'clear']]

  // rows scrolled out of view, counted on the first and last list lines: the pane draws no scrollbar
  const shown = items.slice(top, top + body)
  const hidden = (its: Item[]) => its.filter(it => 'row' in it).length
  const above = hidden(items.slice(0, top))
  const below = hidden(items.slice(top + body))
  const markOf = (j: number) => (j === 0 && above ? ` ↑ ${above}` : j === shown.length - 1 && below ? ` ↓ ${below}` : '')

  const line = (it: Item, i: number, mark: string) => {
    const m = mark.length
    const tail = m ? <Text dimColor>{mark}</Text> : null
    if ('head' in it) {
      const label = ` ${LABEL[it.head] ?? it.head} `
      return (
        <Text key={`h:${it.head}`} wrap="truncate">
          <Text bold>{label}</Text>
          <Text dimColor>{it.count} {'─'.repeat(Math.max(0, w - label.length - String(it.count).length - 1 - m))}</Text>
          {tail}
        </Text>
      )
    }
    const r = it.row
    const cur = rows.indexOf(r) === cursor
    const cost = (r.enabled ? tok(r.tokens) : `(${tok(r.save)})`).padStart(8)
    const b = bar(r.enabled ? r.tokens : r.save, topCost, 5)
    const name = cut(r.name, Math.max(4, w - 6 - 8 - 6 - m))
    const key = `r:${r.type}/${r.name}/${i}`
    if (cur)
      return (
        <Text key={key} wrap="truncate">
          <Text bold inverse>{`› ${r.enabled ? '●' : '○'}   ${name}${cost} ${b}`.padEnd(w - m)}</Text>
          {tail}
        </Text>
      )
    return (
      <Text key={key} wrap="truncate">
        {'  '}
        <Text color={r.enabled ? 'green' : 'yellow'}>{r.enabled ? '●' : '○'}</Text>
        {'   '}
        <Text dimColor={!r.enabled}>{name}</Text>
        <Text dimColor={!r.enabled}>{cost}</Text>
        {' '}
        <Text bold={r.enabled} dimColor={!r.enabled} color={r.enabled ? 'blue' : undefined}>{b}</Text>
        {tail}
      </Text>
    )
  }

  return (
    <Box flexDirection="column">
      <Text key="title" wrap="truncate">
        <Text bold> agent-toggle</Text>
        <Text dimColor>{summary.padStart(Math.max(0, w - 13))}</Text>
      </Text>
      <Text key="search" wrap="truncate">
        <Text color="cyan">/ </Text>
        {s.query !== '' ? <Text>{s.query}</Text> : <Text dimColor>Type to search (name, group, fuzzy)</Text>}
        {s.typing && <Text dimColor>▏</Text>}
        <Text dimColor>{`sort: ${s.sort}`.padStart(Math.max(0, w - 2 - (s.query || 'Type to search (name, group, fuzzy)').length - (s.typing ? 1 : 0)))}</Text>
      </Text>
      {shown.map((it, j) => line(it, top + j, markOf(j)))}
      {loading && (
        <Text key="loading" wrap="truncate">
          <Text color="cyan"> {spin}</Text> Loading resources…
        </Text>
      )}
      {props.error !== '' && <Text key="err" color="red" bold wrap="truncate">{props.error}  (run `agent-toggle doctor`)</Text>}
      <Text key="status" dimColor wrap="truncate">
        {` ${rows.length} of ${props.rows.length} shown`}
        {rows.length ? ` · ${cursor + 1}/${rows.length}` : ''}
        {fuzzy ? '  ≈ fuzzy match' : ''}
        {props.busy ? `  ${spin} working…` : ''}
      </Text>
      <Text key="chips" wrap="truncate">
        {chips.map(([k, l]) => (
          <Text key={k}>
            <Text color="cyan" bold> {k}</Text>
            <Text dimColor> {l} </Text>
          </Text>
        ))}
      </Text>
    </Box>
  )
}

export default Picker
