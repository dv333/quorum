import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import { notify, setBadge } from './attention'
import DebateView from './components/DebateView'
import ErrorBoundary from './components/ErrorBoundary'
import Home from './components/Home'
import Onboarding from './components/Onboarding'
import Settings from './components/Settings'
import ShortcutsHelp from './components/ShortcutsHelp'
import { ResourceSheet } from './components/Resources'
import Sidebar, { ComposeIcon, SidebarIcon } from './components/Sidebar'

// Statuses worth watching: live debates and ones waiting for your answer
const TRACKED = ['intake', 'clarifying', 'confirming', 'running', 'paused', 'concluding', 'researching']

function readCollapsed() {
  try { return localStorage.getItem('quorum.sidebar') === 'hidden' } catch { return false }
}

// Views: home (ask), debate (watch + answer), settings. The URL hash keeps the open debate on reload.
function onboarded() {
  try { return localStorage.getItem('quorum.onboarded') === '1' } catch { return true }
}

function readHash() {
  const h = window.location.hash.slice(1)
  if (h === 'welcome') return { view: 'welcome', id: null }
  if (h === 'settings' || h.startsWith('settings/')) return { view: 'settings', id: null }
  if (h.startsWith('q/')) return { view: 'debate', id: h.slice(2) }
  return { view: 'home', id: null }
}

export default function App() {
  const [config, setConfig] = useState(null)
  // Only conundrums that are live or waiting for you are tracked here (for alerts and the dock badge); the sidebar
  // pages through history from the server itself, so millions of conundrums cost nothing up front
  const [live, setLive] = useState([])
  const [refreshKey, setRefreshKey] = useState(0)
  const refreshSidebar = useCallback(() => setRefreshKey((k) => k + 1), [])
  const [route, setRoute] = useState(readHash)
  const [drawer, setDrawer] = useState(false)
  const [series, setSeries] = useState(null)
  const [resource, setResource] = useState(null)
  const [collapsed, setCollapsed] = useState(readCollapsed)
  const [backendError, setBackendError] = useState(null)
  const [unseen, setUnseen] = useState(() => new Set()) // answers that arrived while you were elsewhere
  const [help, setHelp] = useState(false)
  const [pendingDelete, setPendingDelete] = useState(null) // { id, title, timer }: deleted after a few seconds unless undone
  const prevStatus = useRef(null)
  const routeRef = useRef(route)
  routeRef.current = route

  const toggleSidebar = useCallback(() => {
    setCollapsed((c) => {
      try { localStorage.setItem('quorum.sidebar', c ? 'shown' : 'hidden') } catch { /* private mode */ }
      return !c
    })
  }, [])

  const go = useCallback((view, id = null) => {
    window.location.hash = view === 'debate' ? `q/${id}` : view === 'settings' ? 'settings' : view === 'welcome' ? 'welcome' : ''
    setRoute({ view, id })
    setDrawer(false)
    setResource(null)
  }, [])

  useEffect(() => {
    const onHash = () => { setRoute(readHash()); setResource(null) }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const loadDebates = useCallback(async () => {
    try {
      const prevIds = Object.keys(prevStatus.current || {})
      const current = await api.listDebates({ status: TRACKED })
      // Conundrums that were live and aren't any more: fetch them to see how they ended (answered, stopped)
      const ended = prevIds.filter((id) => !current.some((d) => d.id === id))
      const list = ended.length ? [...current, ...(await api.listDebates({ ids: ended }))] : current
      setLive(current)
      setBackendError(null)
      // Alert on transitions: the chair needs you, or an answer is ready
      const prev = prevStatus.current
      if (prev) {
        for (const d of list) {
          const was = prev[d.id]
          const label = d.title || d.question || 'Your conundrum'
          const open = () => { window.location.hash = `q/${d.id}` }
          if (['clarifying', 'confirming'].includes(d.status) && was && was !== d.status) {
            notify(d.status === 'clarifying' ? 'The chair has a question' : 'Check the assumptions', label, open)
          }
          if (d.status === 'concluded' && was && was !== 'concluded' && was !== 'idle') {
            notify('Your answer is ready', label, open)
            if (!(routeRef.current.view === 'debate' && routeRef.current.id === d.id && document.hasFocus())) {
              setUnseen((u) => new Set(u).add(d.id))
            }
          }
        }
      }
      const before = prevStatus.current
      prevStatus.current = Object.fromEntries(current.map((d) => [d.id, d.status]))
      // Any change in what's live (new, ended, moved on) refreshes the sidebar's loaded pages
      if (!before || ended.length || current.some((d) => before[d.id] !== d.status)) refreshSidebar()
    } catch (e) {
      setBackendError(e.message)
    }
  }, [refreshSidebar])

  useEffect(() => {
    api.config().then(setConfig, (e) => setBackendError(e.message))
    // First launch (never set up, nothing asked yet): start with the walkthrough
    api.listDebates({ limit: 1 }).then((list) => {
      if (!onboarded() && list.length === 0 && !window.location.hash) go('welcome')
    }, () => {})
    loadDebates()
  }, [loadDebates, go])

  // Live Memory / CPU / GPU samples for the sidebar cards (the backend samples every 2s)
  useEffect(() => {
    const load = () => api.systemSeries().then(setSeries, () => {})
    const tick = () => { if (!document.hidden) load() }
    load() // always once, even if the tab opens in the background
    const t = setInterval(tick, 2000)
    document.addEventListener('visibilitychange', tick)
    return () => { clearInterval(t); document.removeEventListener('visibilitychange', tick) }
  }, [])

  // Live debates change status/title without navigation; keep the sidebar fresh
  const anyActive = live.some((d) => ['running', 'concluding', 'researching', 'intake'].includes(d.status))
  useEffect(() => {
    if (!anyActive) return undefined
    const t = setInterval(loadDebates, 2500)
    return () => clearInterval(t)
  }, [anyActive, loadDebates])

  // Opening a conundrum marks its answer as seen
  useEffect(() => {
    if (route.view === 'debate' && unseen.has(route.id)) {
      setUnseen((u) => { const n = new Set(u); n.delete(route.id); return n })
    }
  }, [route, unseen])

  const waiting = live.filter((d) => ['clarifying', 'confirming'].includes(d.status)).map((d) => d.id)
  const attention = new Set([...waiting, ...unseen])
  useEffect(() => { if (config) setBadge(attention.size, config.app_name) }, [attention.size, config])

  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'n') { e.preventDefault(); go('home') }
      if (e.metaKey && e.ctrlKey && e.key.toLowerCase() === 's') { e.preventDefault(); toggleSidebar() }
      if (e.key === 'Escape') setDrawer(false)
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName) || document.activeElement?.isContentEditable
      if (e.key === '?' && !typing && !e.metaKey && !e.ctrlKey) { e.preventDefault(); setHelp(true) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [go, toggleSidebar])

  // Deleting hides the conundrum at once and only removes it for good after a few seconds, so it can be undone
  const finishDelete = useCallback(async (pending) => {
    clearTimeout(pending.timer)
    try { await api.deleteDebate(pending.id) } catch { /* already gone */ }
    setPendingDelete((p) => (p && p.id === pending.id ? null : p))
    loadDebates()
    refreshSidebar()
  }, [loadDebates, refreshSidebar])

  const requestDelete = (id, title) => {
    if (pendingDelete) finishDelete(pendingDelete) // one undo at a time
    const pending = { id, title: title || 'Conundrum' }
    pending.timer = setTimeout(() => finishDelete(pending), 6000)
    setPendingDelete(pending)
    if (id === route.id) go('home')
  }

  const undoDelete = useCallback(() => {
    setPendingDelete((p) => { if (p) clearTimeout(p.timer); return null })
  }, [])

  useEffect(() => {
    if (!pendingDelete) return undefined
    // ⌘Z undoes (outside text fields); closing the page still completes the delete
    const onKey = (e) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName) || document.activeElement?.isContentEditable
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z' && !e.shiftKey && !typing) { e.preventDefault(); undoDelete() }
    }
    const onHide = () => { fetch(`/api/debates/${pendingDelete.id}`, { method: 'DELETE', keepalive: true }).catch(() => {}) }
    window.addEventListener('keydown', onKey)
    window.addEventListener('pagehide', onHide)
    return () => { window.removeEventListener('keydown', onKey); window.removeEventListener('pagehide', onHide) }
  }, [pendingDelete, undoDelete])

  if (backendError && !config) {
    return (
      <div className="empty-state">
        <div className="mark big" style={{ margin: '0 auto' }}><span /><span /><span /></div>
        <h2>Can't reach Quorum's engine</h2>
        <p>Start it with <code>./start.sh</code> from the project folder. It runs on port 8002.</p>
        <p className="error">{backendError}</p>
      </div>
    )
  }
  if (!config) return <div className="empty-state"><div className="typing"><i /><i /><i /></div></div>

  if (route.view === 'welcome') {
    return (
      <Onboarding appName={config.app_name} onDone={() => go('home')}
        onStartQuestion={async (q) => { const snap = await api.createDebate({ question: q }); loadDebates(); refreshSidebar(); go('debate', snap.debate.id) }} />
    )
  }

  const mobileBar = (
    <div className="mobile-bar">
      <button className="icon-btn" onClick={() => setDrawer(true)} aria-label="Show questions">☰</button>
      <div className="brand"><div className="mark"><span /><span /><span /></div><b>{config.app_name}</b></div>
      <button className="icon-btn" onClick={() => go('home')} aria-label="New conundrum">✎</button>
    </div>
  )

  return (
    <div className={`app ${drawer ? 'drawer' : ''} ${collapsed ? 'collapsed' : ''}`}>
      <div className="ambient" />
      <Sidebar
        appName={config.app_name}
        refreshKey={refreshKey}
        polling={anyActive}
        hiddenId={pendingDelete?.id}
        currentId={route.id}
        view={route.view}
        series={series}
        attention={attention}
        onOpenResource={setResource}
        onCollapse={toggleSidebar}
        onSelect={(id) => go('debate', id)}
        onNew={() => go('home')}
        onSettings={() => go('settings')}
        onDelete={requestDelete}
      />
      <div className="scrim" onClick={() => setDrawer(false)} />
      <main className={`main ${route.view === 'home' ? 'plain' : ''}`}>
        {collapsed && (
          <>
            <button className="icon-btn expand-btn" onClick={toggleSidebar} aria-label="Show sidebar" title="Show sidebar (⌃⌘S)"><SidebarIcon /></button>
            <button className="icon-btn expand-btn new" onClick={() => go('home')} aria-label="New conundrum" title="New conundrum (⌘N)"><ComposeIcon /></button>
          </>
        )}
        <ErrorBoundary resetKey={`${route.view}/${route.id}`}>
        {route.view === 'home' && (
          <Home config={config} mobileBar={mobileBar} onOpenSettings={() => go('settings')}
            onCreated={(id) => { loadDebates(); refreshSidebar(); go('debate', id) }} />
        )}
        {route.view === 'debate' && route.id && (
          <DebateView key={route.id} debateId={route.id} onChanged={() => { loadDebates(); refreshSidebar() }} mobileBar={mobileBar} />
        )}
        {route.view === 'settings' && <Settings mobileBar={mobileBar} onRunSetup={() => go('welcome')} />}
        </ErrorBoundary>
      </main>
      {resource && <ResourceSheet series={series} focus={resource} onClose={() => setResource(null)} />}
      {help && <ShortcutsHelp onClose={() => setHelp(false)} />}
      {pendingDelete && (
        <div className="toast" role="status">
          <span className="toast-text">Deleted “{pendingDelete.title.length > 48 ? `${pendingDelete.title.slice(0, 47)}…` : pendingDelete.title}”</span>
          <button className="toast-action" onClick={undoDelete} title="Undo (⌘Z)">Undo</button>
        </div>
      )}
    </div>
  )
}
