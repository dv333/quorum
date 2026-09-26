import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { displayTitle } from '../agents'
import { ResourceCards } from './Resources'

// Pinned conundrums live in this browser only: a per-viewer convenience, like a bookmark
const PIN_KEY = 'quorum.pinned'
function loadPins() {
  try { return new Set(JSON.parse(localStorage.getItem(PIN_KEY) || '[]')) } catch { return new Set() }
}
function savePins(pins) {
  try { localStorage.setItem(PIN_KEY, JSON.stringify([...pins])) } catch { /* private mode: pins last this session */ }
}

const PAGE = 10
const LIVE = ['running', 'concluding', 'researching', 'intake']

// Day groups in local time; each is a time range the server pages through, newest first
function dayGroups() {
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1)
  const week = new Date(today); week.setDate(today.getDate() - 7)
  const iso = (d) => d.toISOString()
  return [
    { key: 'today', label: 'Today', after: iso(today), before: null },
    { key: 'yesterday', label: 'Yesterday', after: iso(yesterday), before: iso(today) },
    { key: 'week', label: 'Previous 7 days', after: iso(week), before: iso(yesterday) },
    { key: 'earlier', label: 'Earlier', after: null, before: iso(week) },
  ]
}

// Which groups are open: Today by default, the rest collapsed; remembered in this browser
const OPEN_KEY = 'quorum.sidebar.open'
function loadOpen() {
  try { return { today: true, ...JSON.parse(localStorage.getItem(OPEN_KEY) || '{}') } } catch { return { today: true } }
}
function saveOpen(open) {
  try { localStorage.setItem(OPEN_KEY, JSON.stringify(open)) } catch { /* private mode */ }
}

// The sidebar's history, loaded from the server a page at a time: counts for every group, items only for open
// groups (and more on "Show more"), search results, and pinned conundrums. `refreshKey` reloads what's shown.
function useHistory({ query, pins, open, refreshKey, polling }) {
  const [groups, setGroups] = useState({})
  const [pinned, setPinned] = useState([])
  const [results, setResults] = useState(null) // { items, count } while searching
  const [error, setError] = useState(null)
  const defs = useMemo(dayGroups, [refreshKey]) // eslint-disable-line react-hooks/exhaustive-deps
  const pinList = useMemo(() => [...pins], [pins])
  const loaded = useRef({}) // how many items each group shows, so a refresh keeps "Show more" pages
  const q = query.trim()

  const fetchGroup = useCallback(async (g, limit, before) => {
    const params = { after: g.after, before: before || g.before, limit, exclude: pinList }
    const [items, count] = await Promise.all([
      api.listDebates(params),
      before ? null : api.countDebates({ after: g.after, before: g.before, exclude: pinList }),
    ])
    return { items, count }
  }, [pinList])

  const refresh = useCallback(async () => {
    try {
      if (q) {
        const size = Math.max(PAGE * 2, loaded.current.results || 0)
        const [items, count] = await Promise.all([api.listDebates({ q, limit: size }), api.countDebates({ q })])
        setResults({ items, count })
      } else {
        setResults(null)
        const next = {}
        await Promise.all(defs.map(async (g) => {
          if (open[g.key]) {
            const { items, count } = await fetchGroup(g, Math.max(PAGE, loaded.current[g.key] || 0))
            next[g.key] = { items, count }
          } else {
            next[g.key] = { items: [], count: await api.countDebates({ after: g.after, before: g.before, exclude: pinList }) }
          }
        }))
        setGroups(next)
      }
      setPinned(pinList.length ? await api.listDebates({ ids: pinList }) : [])
      setError(null)
    } catch (e) {
      setError(e.message)
    }
  }, [q, defs, open, fetchGroup, pinList])

  // Search waits for a pause in typing; everything else loads at once
  useEffect(() => {
    const t = setTimeout(refresh, q ? 250 : 0)
    return () => clearTimeout(t)
  }, [refresh, q, refreshKey])

  // While a debate is live, keep statuses and rounds fresh (only what's loaded)
  useEffect(() => {
    if (!polling) return undefined
    const t = setInterval(refresh, 2500)
    return () => clearInterval(t)
  }, [polling, refresh])

  const more = useCallback(async (key) => {
    try {
      if (key === 'results') {
        const last = results?.items[results.items.length - 1]
        const items = await api.listDebates({ q, limit: PAGE * 2, before: last?.created_at })
        loaded.current.results = (results?.items.length || 0) + items.length
        setResults((r) => ({ ...r, items: [...r.items, ...items] }))
        return
      }
      const g = defs.find((x) => x.key === key)
      const have = groups[key]?.items || []
      const { items } = await fetchGroup(g, PAGE, have[have.length - 1]?.created_at)
      loaded.current[key] = have.length + items.length
      setGroups((s) => ({ ...s, [key]: { ...s[key], items: [...(s[key]?.items || []), ...items] } }))
    } catch (e) {
      setError(e.message)
    }
  }, [defs, groups, results, q, fetchGroup])

  // A group that's closed starts again from its first page when reopened
  const reset = useCallback((key) => { loaded.current[key] = 0 }, [])

  return { defs, groups, pinned, results, error, more, reset }
}

const STATUS = {
  intake: 'Reading', clarifying: 'Needs your answer', confirming: 'Review assumptions',
  running: 'Debating', paused: 'Paused', concluding: 'Writing answer', researching: 'Researching', concluded: 'Answered', idle: 'New',
}

export function SidebarIcon() {
  // Two-pane glyph, like the macOS "Show/Hide Sidebar" toolbar icon
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <rect x="2.5" y="3.5" width="15" height="13" rx="3" />
      <line x1="7.5" y1="3.5" x2="7.5" y2="16.5" />
    </svg>
  )
}

export function GearIcon({ size = 18 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="3.2" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1.03 1.56V21a2 2 0 1 1-4 0v-.09A1.7 1.7 0 0 0 8.9 19.4a1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.56-1.03H3a2 2 0 1 1 0-4h.09A1.7 1.7 0 0 0 4.6 8.9a1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 8.9 4.6 1.7 1.7 0 0 0 9.93 3.04V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1.03 1.56 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87V9a1.7 1.7 0 0 0 1.56 1.03H21a2 2 0 1 1 0 4h-.09A1.7 1.7 0 0 0 19.4 15z" />
    </svg>
  )
}

export function ComposeIcon() {
  return (
    <svg width="17" height="17" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M9 3.5H5.5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V11" />
      <path d="M14.6 2.9a1.5 1.5 0 0 1 2.1 2.1L10 11.7l-2.8.7.7-2.8z" />
    </svg>
  )
}

function Chevron({ open }) {
  return (
    <svg className={`chev ${open ? 'open' : ''}`} width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
      <path d="M3.5 2 7 5 3.5 8" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export default function Sidebar({ refreshKey, polling, hiddenId, currentId, view, series, attention, onSelect, onNew, onDelete, onSettings, onCollapse, onOpenResource, appName }) {
  const [query, setQuery] = useState('')
  const [pins, setPins] = useState(loadPins)
  const [open, setOpen] = useState(loadOpen)
  const togglePin = (id) => setPins((prev) => {
    const next = new Set(prev)
    if (next.has(id)) next.delete(id); else next.add(id)
    savePins(next)
    return next
  })
  const toggleGroup = (key) => {
    if (open[key]) reset(key)
    setOpen((o) => { const n = { ...o, [key]: !o[key] }; saveOpen(n); return n })
  }
  const searchRef = useRef(null)
  const { defs, groups, pinned, results, error, more, reset } = useHistory({ query, pins, open, refreshKey, polling })
  const visible = (items) => items.filter((d) => d.id !== hiddenId)
  const total = defs.reduce((n, g) => n + (groups[g.key]?.count || 0), 0) + pinned.length

  // ⌘K (or / outside a text field) jumps to the search box
  useEffect(() => {
    const onKey = (e) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName) || document.activeElement?.isContentEditable
      if (((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') || (e.key === '/' && !typing)) {
        e.preventDefault()
        searchRef.current?.focus()
        searchRef.current?.select()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const item = (d) => {
    const live = LIVE.includes(d.status)
    const title = displayTitle(d.title, d.question)
    return (
      <div key={d.id} className={`hist ${view === 'debate' && d.id === currentId ? 'on' : ''} ${pins.has(d.id) ? 'pinned' : ''}`}
        role="button" tabIndex={0} onClick={() => onSelect(d.id)} onKeyDown={(e) => e.key === 'Enter' && onSelect(d.id)}>
        <div className="t">{title}</div>
        {attention?.has(d.id) && <i className="badge" aria-label="Needs your attention" />}
        <div className="s">{live && <i className="live-dot" />}{STATUS[d.status] || d.status}{d.round ? ` · ${d.round} round${d.round === 1 ? '' : 's'}` : ''}</div>
        <button className={`icon-btn pin ${pins.has(d.id) ? 'on' : ''}`} aria-pressed={pins.has(d.id)}
          aria-label={pins.has(d.id) ? 'Unpin' : 'Pin to top'} title={pins.has(d.id) ? 'Unpin' : 'Pin to top'}
          onClick={(e) => { e.stopPropagation(); togglePin(d.id) }}>
          <svg width="12" height="12" viewBox="0 0 16 16" fill={pins.has(d.id) ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
            <path d="M5.5 1.5h5l-.8 4.3 2.8 2.7v1h-4v5l-.5 1-.5-1v-5h-4v-1l2.8-2.7z" strokeLinejoin="round" />
          </svg>
        </button>
        <button className="icon-btn x" aria-label="Delete"
          onClick={(e) => { e.stopPropagation(); onDelete(d.id, title) }}>✕</button>
      </div>
    )
  }

  const moreButton = (key, shown, count) => shown < count && (
    <button className="side-more" onClick={() => more(key)}>Show more <span>· {(count - shown).toLocaleString()} more</span></button>
  )

  return (
    <aside className="sidebar" aria-label="Conundrums">
      <div className="brand">
        <div className="mark"><span /><span /><span /></div><b>{appName}</b>
        <button className="icon-btn brand-new" onClick={onNew} aria-label="New conundrum" title="New conundrum (⌘N)"><ComposeIcon /></button>
        <button className="icon-btn collapse-btn" onClick={onCollapse} aria-label="Hide sidebar" title="Hide sidebar (⌃⌘S)"><SidebarIcon /></button>
      </div>
      <button className="new-q" onClick={onNew} title="New conundrum (⌘N)"><ComposeIcon /> New conundrum</button>
      {(total > 0 || query) && (
        <div className="side-search">
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            <circle cx="7" cy="7" r="4.8" /><line x1="10.6" y1="10.6" x2="14" y2="14" strokeLinecap="round" />
          </svg>
          <input ref={searchRef} type="search" value={query} placeholder="Search" aria-label="Search conundrums"
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') { setQuery(''); e.currentTarget.blur() }
              if (e.key === 'Enter' && results?.items.length) onSelect(results.items[0].id)
            }} />
          {query ? <button className="clear" aria-label="Clear search" onClick={() => { setQuery(''); searchRef.current?.focus() }}>✕</button>
            : <kbd aria-hidden="true">⌘K</kbd>}
        </div>
      )}
      <nav className="history">
        {results ? (
          <div>
            <div className="side-h">{results.count ? `${results.count.toLocaleString()} result${results.count === 1 ? '' : 's'}` : ''}</div>
            {visible(results.items).map(item)}
            {moreButton('results', results.items.length, results.count)}
            {results.count === 0 && <div className="side-empty">No conundrums match “{query.trim()}”.</div>}
          </div>
        ) : (
          <>
            {pinned.length > 0 && (
              <div>
                <div className="side-h">Pinned</div>
                {visible(pinned).map(item)}
              </div>
            )}
            {defs.map((g) => {
              const s = groups[g.key]
              if (!s || !s.count) return null
              const isOpen = !!open[g.key]
              return (
                <div key={g.key} className="side-group">
                  <button className="side-h toggle" aria-expanded={isOpen} onClick={() => toggleGroup(g.key)}>
                    <Chevron open={isOpen} />{g.label}<span className="count">{s.count.toLocaleString()}</span>
                  </button>
                  {isOpen && visible(s.items).map(item)}
                  {isOpen && moreButton(g.key, s.items.length, s.count)}
                </div>
              )
            })}
            {total === 0 && !error && <div className="side-h" style={{ fontWeight: 400 }}>Your conundrums will appear here.</div>}
          </>
        )}
        {error && <div className="side-empty">Couldn't load conundrums: {error}</div>}
      </nav>
      <div className="side-foot">
        <ResourceCards series={series} onOpen={onOpenResource} />
        <button className={`settings-btn ${view === 'settings' ? 'on' : ''}`} onClick={onSettings}>
          <GearIcon size={20} /> Settings
        </button>
      </div>
    </aside>
  )
}
