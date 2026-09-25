import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import { notify, setBadge } from './attention'
import DebateView from './components/DebateView'
import ErrorBoundary from './components/ErrorBoundary'
import Home from './components/Home'
import Onboarding from './components/Onboarding'
import Settings from './components/Settings'
import { ResourceSheet } from './components/Resources'
import Sidebar, { SidebarIcon } from './components/Sidebar'

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
  const [debates, setDebates] = useState([])
  const [route, setRoute] = useState(readHash)
  const [drawer, setDrawer] = useState(false)
  const [series, setSeries] = useState(null)
  const [resource, setResource] = useState(null)
  const [collapsed, setCollapsed] = useState(readCollapsed)
  const [backendError, setBackendError] = useState(null)
  const [unseen, setUnseen] = useState(() => new Set()) // answers that arrived while you were elsewhere
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
      const list = await api.listDebates()
      setDebates(list)
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
      prevStatus.current = Object.fromEntries(list.map((d) => [d.id, d.status]))
    } catch (e) {
      setBackendError(e.message)
    }
  }, [])

  useEffect(() => {
    api.config().then(setConfig, (e) => setBackendError(e.message))
    // First launch (never set up, nothing asked yet): start with the walkthrough
    api.listDebates().then((list) => {
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
  const anyActive = debates.some((d) => ['running', 'concluding', 'researching', 'intake'].includes(d.status))
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

  const waiting = debates.filter((d) => ['clarifying', 'confirming'].includes(d.status)).map((d) => d.id)
  const attention = new Set([...waiting, ...unseen])
  useEffect(() => { if (config) setBadge(attention.size, config.app_name) }, [attention.size, config])

  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'n') { e.preventDefault(); go('home') }
      if (e.metaKey && e.ctrlKey && e.key.toLowerCase() === 's') { e.preventDefault(); toggleSidebar() }
      if (e.key === 'Escape') setDrawer(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [go, toggleSidebar])

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
        onStartQuestion={async (q) => { const snap = await api.createDebate({ question: q }); loadDebates(); go('debate', snap.debate.id) }} />
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
        debates={debates}
        currentId={route.id}
        view={route.view}
        series={series}
        attention={attention}
        onOpenResource={setResource}
        onCollapse={toggleSidebar}
        onSelect={(id) => go('debate', id)}
        onNew={() => go('home')}
        onSettings={() => go('settings')}
        onDelete={async (id) => {
          await api.deleteDebate(id)
          if (id === route.id) go('home')
          loadDebates()
        }}
      />
      <div className="scrim" onClick={() => setDrawer(false)} />
      <main className={`main ${route.view === 'home' ? 'plain' : ''}`}>
        {collapsed && (
          <button className="icon-btn expand-btn" onClick={toggleSidebar} aria-label="Show sidebar" title="Show sidebar (⌃⌘S)"><SidebarIcon /></button>
        )}
        <ErrorBoundary resetKey={`${route.view}/${route.id}`}>
        {route.view === 'home' && (
          <Home config={config} mobileBar={mobileBar} onOpenSettings={() => go('settings')}
            onCreated={(id) => { loadDebates(); go('debate', id) }} />
        )}
        {route.view === 'debate' && route.id && (
          <DebateView key={route.id} debateId={route.id} onChanged={loadDebates} mobileBar={mobileBar} />
        )}
        {route.view === 'settings' && <Settings mobileBar={mobileBar} onRunSetup={() => go('welcome')} />}
        </ErrorBoundary>
      </main>
      {resource && <ResourceSheet series={series} focus={resource} onClose={() => setResource(null)} />}
    </div>
  )
}
