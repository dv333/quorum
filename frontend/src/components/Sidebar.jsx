import { useEffect, useRef, useState } from 'react'
import { displayTitle } from '../agents'
import { ResourceCards } from './Resources'

function groupByDay(debates) {
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1)
  const week = new Date(today); week.setDate(today.getDate() - 7)
  const groups = [['Today', []], ['Yesterday', []], ['Previous 7 days', []], ['Earlier', []]]
  for (const d of debates) {
    const t = new Date(d.created_at)
    const g = t >= today ? 0 : t >= yesterday ? 1 : t >= week ? 2 : 3
    groups[g][1].push(d)
  }
  return groups.filter(([, items]) => items.length)
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

// Every word of the query has to appear in the title or the question (any order, any case)
function matches(d, query) {
  const hay = `${displayTitle(d.title, d.question)} ${d.question || ''}`.toLowerCase()
  return query.toLowerCase().split(/\s+/).filter(Boolean).every((w) => hay.includes(w))
}

export default function Sidebar({ debates, currentId, view, series, attention, onSelect, onNew, onDelete, onSettings, onCollapse, onOpenResource, appName }) {
  const [query, setQuery] = useState('')
  const searchRef = useRef(null)
  const shown = query.trim() ? debates.filter((d) => matches(d, query)) : debates

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

  return (
    <aside className="sidebar" aria-label="Conundrums">
      <div className="brand">
        <div className="mark"><span /><span /><span /></div><b>{appName}</b>
        <button className="icon-btn collapse-btn" onClick={onCollapse} aria-label="Hide sidebar" title="Hide sidebar (⌃⌘S)"><SidebarIcon /></button>
      </div>
      <button className="new-q" onClick={onNew} title="New conundrum (⌘N)"><span aria-hidden="true">✎</span> New conundrum</button>
      {debates.length > 0 && (
        <div className="side-search">
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            <circle cx="7" cy="7" r="4.8" /><line x1="10.6" y1="10.6" x2="14" y2="14" strokeLinecap="round" />
          </svg>
          <input ref={searchRef} type="search" value={query} placeholder="Search" aria-label="Search conundrums"
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') { setQuery(''); e.currentTarget.blur() }
              if (e.key === 'Enter' && shown.length) onSelect(shown[0].id)
            }} />
          {query ? <button className="clear" aria-label="Clear search" onClick={() => { setQuery(''); searchRef.current?.focus() }}>✕</button>
            : <kbd aria-hidden="true">⌘K</kbd>}
        </div>
      )}
      <nav className="history">
        {groupByDay(shown).map(([label, items]) => (
          <div key={label}>
            <div className="side-h">{label}</div>
            {items.map((d) => {
              const live = ['running', 'concluding', 'researching', 'intake'].includes(d.status)
              return (
                <div key={d.id} className={`hist ${view === 'debate' && d.id === currentId ? 'on' : ''}`}
                  role="button" tabIndex={0} onClick={() => onSelect(d.id)} onKeyDown={(e) => e.key === 'Enter' && onSelect(d.id)}>
                  <div className="t">{displayTitle(d.title, d.question)}</div>
                  {attention?.has(d.id) && <i className="badge" aria-label="Needs your attention" />}
                  <div className="s">{live && <i className="live-dot" />}{STATUS[d.status] || d.status}{d.round ? ` · ${d.round} round${d.round === 1 ? '' : 's'}` : ''}</div>
                  <button className="icon-btn x" aria-label="Delete"
                    onClick={(e) => { e.stopPropagation(); if (confirm('Delete this conundrum and its debate?')) onDelete(d.id) }}>✕</button>
                </div>
              )
            })}
          </div>
        ))}
        {debates.length === 0 && <div className="side-h" style={{ fontWeight: 400 }}>Your conundrums will appear here.</div>}
        {debates.length > 0 && shown.length === 0 && (
          <div className="side-empty">No conundrums match “{query.trim()}”.</div>
        )}
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
