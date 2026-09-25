import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, formatGB } from '../api'
import { RESEARCHER, modelShort } from '../agents'

const TABS = [['models', 'Models'], ['providers', 'Providers'], ['search', 'Web search']]
const FIT = { fits: ['ok', 'Fits'], too_big: ['bad', 'Too big'], unknown: ['', 'Unknown size'], cloud: ['', 'Cloud'] }

function SearchField({ value, onChange, placeholder }) {
  return (
    <div className="search-field">
      <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><circle cx="8.5" cy="8.5" r="5.5" /><path d="m13 13 4 4" strokeLinecap="round" /></svg>
      <input value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} aria-label={placeholder} />
      {value && <button className="clear" onClick={() => onChange('')} aria-label="Clear search">✕</button>}
    </div>
  )
}

function PullButton({ endpoints, model, onDone }) {
  const ollama = endpoints.find((e) => e.kind === 'ollama' && e.enabled && e.reachable)
  const [progress, setProgress] = useState(null)
  const [error, setError] = useState(null)
  const pull = async () => {
    setError(null)
    setProgress({ status: 'starting' })
    try {
      await api.pullModel(ollama.id, model, (ev) => (ev.error ? setError(ev.error) : setProgress(ev)))
      onDone()
    } catch (e) {
      setError(e.message)
    }
    setProgress(null)
  }
  if (error) return <span className="error small">{error}</span>
  if (progress) {
    const pct = progress.total ? Math.round((100 * (progress.completed || 0)) / progress.total) : null
    return (
      <span className="small muted" style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
        {pct != null && <span className="progress-line"><i style={{ width: `${pct}%` }} /></span>}
        {pct != null ? `${pct}%` : progress.status}
      </span>
    )
  }
  return <button className="btn small" disabled={!ollama} title={ollama ? '' : 'Ollama is not running'} onClick={pull}>Get</button>
}

function matches(q, ...fields) {
  const needle = q.trim().toLowerCase()
  return !needle || fields.some((f) => (f || '').toLowerCase().includes(needle))
}

function ModelsTab({ inv, catalog, reload }) {
  const [q, setQ] = useState('')
  const groups = useMemo(() => {
    const g = {}
    for (const m of inv.models) {
      if (!matches(q, m.model, m.family, m.endpoint_name)) continue
      const name = m.local ? 'On this Mac' : m.endpoint_name
      ;(g[name] = g[name] || []).push(m)
    }
    return Object.entries(g).sort(([a], [b]) => (a === 'On this Mac' ? -1 : b === 'On this Mac' ? 1 : a.localeCompare(b)))
  }, [inv.models, q])
  const suggested = catalog.filter((c) => !c.installed && matches(q, c.model, c.family, c.strengths))

  return (
    <>
      <SearchField value={q} onChange={setQ} placeholder="Search models" />
      {groups.map(([name, ms]) => (
        <div className="group" key={name}>
          <h3>{name} <span className="faint">· {ms.length}</span></h3>
          <div className="list">
            {ms.map((m) => {
              const loaded = inv.loaded.find((l) => l.endpoint_id === m.endpoint_id && l.model === m.model)
              const [tone, label] = FIT[m.fit] || FIT.unknown
              return (
                <div className="list-row" key={m.key}>
                  <div className="grow">
                    <b>{modelShort(m.model)}</b> {m.thinking && <span className="pill think">reasons</span>} {!m.chat && <span className="pill">not for chat</span>}
                    <div className="sub">{[m.local ? m.endpoint_name : null, m.family, m.params, m.quant].filter(Boolean).join(' · ')}</div>
                  </div>
                  {m.local && <span className="small muted">{formatGB(m.est_bytes)}</span>}
                  {loaded && <span className="pill ok">In memory</span>}
                  <span className={`pill ${tone}`}>{label}</span>
                </div>
              )
            })}
          </div>
        </div>
      ))}
      {groups.length === 0 && !q && <p className="muted">No models yet. Get a few below, or add a cloud provider.</p>}
      {suggested.length > 0 && (
        <div className="group">
          <h3>Get more models</h3>
          <div className="list">
            {suggested.map((c) => {
              const [tone, label] = FIT[c.fit] || FIT.unknown
              return (
                <div className="list-row" key={c.model}>
                  <div className="grow"><b>{c.model}</b><div className="sub">{c.strengths}</div></div>
                  <span className="small muted">{formatGB(c.size_bytes)}</span>
                  {c.fit === 'too_big' ? <span className={`pill ${tone}`}>{label}</span>
                    : <PullButton endpoints={inv.endpoints} model={c.model} onDone={reload} />}
                </div>
              )
            })}
          </div>
          <p>Mixing families (Qwen, Gemma, Llama, Mistral…) makes for a more genuine debate than copies of one model.</p>
        </div>
      )}
      {q && groups.length === 0 && suggested.length === 0 && <p className="muted">No models match “{q}”.</p>}
    </>
  )
}

function KeyRow({ ep, onSave, onRemove, onToggle }) {
  const [editing, setEditing] = useState(false)
  const [key, setKey] = useState('')
  return (
    <div className="list-row stack-sm">
      <div className="grow">
        <b>{ep.name}</b>
        <div className="sub">
          {!ep.enabled ? 'Off' : ep.error ? <span className="error small">{ep.error}</span> : `${ep.models} models`}
          {ep.has_key && ` · key ${ep.key_hint}`}
        </div>
      </div>
      {editing ? (
        <>
          <input className="input" type="password" autoComplete="off" placeholder="New API key" value={key} onChange={(e) => setKey(e.target.value)} style={{ width: 200 }} />
          <button className="btn small blue" disabled={!key.trim()} onClick={() => { onSave(key); setEditing(false); setKey('') }}>Save</button>
          <button className="btn small ghost" onClick={() => setEditing(false)}>Cancel</button>
        </>
      ) : (
        <>
          <button className="btn small" onClick={() => setEditing(true)}>{ep.has_key ? 'Change key' : 'Add key'}</button>
          <label className="switch"><input type="checkbox" checked={ep.enabled} onChange={onToggle} aria-label={`Use ${ep.name}`} /></label>
          <button className="icon-btn" onClick={onRemove} aria-label={`Remove ${ep.name}`}>✕</button>
        </>
      )}
    </div>
  )
}

function ProvidersTab({ inv, reload }) {
  const [presets, setPresets] = useState([])
  const [presetId, setPresetId] = useState('openai')
  const [key, setKey] = useState('')
  const [custom, setCustom] = useState({ name: '', base_url: '' })
  const [local, setLocal] = useState({ name: '', base_url: 'http://localhost:', kind: 'openai_compat' })
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { api.providers().then(setPresets, () => {}) }, [])

  const act = async (fn) => {
    setError(null)
    setBusy(true)
    try { await fn(); await reload() } catch (e) { setError(e.message) }
    setBusy(false)
  }
  const preset = presets.find((p) => p.id === presetId)
  const cloud = inv.endpoints.filter((e) => !e.local)
  const locals = inv.endpoints.filter((e) => e.local)
  const addCloud = () => act(async () => {
    const body = presetId === 'custom'
      ? { name: custom.name.trim(), base_url: custom.base_url.trim(), api_key: key.trim() }
      : { name: preset.name, base_url: preset.base_url, api_key: key.trim() }
    await api.addEndpoint({ ...body, kind: 'openai_compat' })
    setKey('')
  })

  return (
    <>
      <div className="group">
        <h3>Cloud providers</h3>
        <div className="list">
          {cloud.map((e) => (
            <KeyRow key={e.id} ep={e}
              onSave={(k) => act(() => api.updateEndpoint(e.id, { api_key: k }))}
              onToggle={() => act(() => api.updateEndpoint(e.id, { enabled: !e.enabled }))}
              onRemove={() => { if (confirm(`Remove ${e.name} and its key?`)) act(() => api.deleteEndpoint(e.id)) }} />
          ))}
          <div className="list-row stack-sm">
            <div className="grow"><b>Add a provider</b>
              <div className="sub">{preset?.key_url ? <a href={preset.key_url} target="_blank" rel="noreferrer noopener">Get a {preset.name} key</a> : 'Any OpenAI-compatible API'}</div>
            </div>
            <select className="input" value={presetId} onChange={(e) => setPresetId(e.target.value)}>
              {presets.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              <option value="custom">Other…</option>
            </select>
          </div>
          {presetId === 'custom' && (
            <div className="list-row stack-sm">
              <input className="input" placeholder="Name" value={custom.name} onChange={(e) => setCustom({ ...custom, name: e.target.value })} style={{ width: 140 }} />
              <input className="input" placeholder="https://api.example.com/v1" value={custom.base_url} onChange={(e) => setCustom({ ...custom, base_url: e.target.value })} style={{ flex: 1 }} />
            </div>
          )}
          <div className="list-row stack-sm">
            <input className="input" type="password" autoComplete="off" placeholder="API key" value={key} onChange={(e) => setKey(e.target.value)} style={{ flex: 1 }} />
            <button className="btn blue small" disabled={busy || !key.trim() || (presetId === 'custom' && (!custom.name.trim() || !custom.base_url.trim()))} onClick={addCloud}>Add</button>
          </div>
        </div>
        <p>Keys are stored only in Quorum's local database on this Mac and are never shown again in full. Cloud models are never picked automatically; add them to a council in Customize. They cost money and your question leaves this machine.</p>
        {error && <p className="error">{error}</p>}
      </div>

      <div className="group">
        <h3>On this Mac</h3>
        <div className="list">
          {locals.map((e) => (
            <div className="list-row" key={e.id}>
              <div className="grow"><b>{e.name}</b><div className="sub">{e.base_url} · {e.kind === 'ollama' ? 'Ollama' : 'OpenAI-compatible'}{e.enabled && e.reachable ? ` · ${e.models} models` : ''}</div></div>
              {!e.enabled ? <span className="pill">Off</span> : e.reachable ? <span className="pill ok">Running</span> : <span className="pill">Not running</span>}
              <label className="switch"><input type="checkbox" checked={e.enabled} onChange={() => act(() => api.updateEndpoint(e.id, { enabled: !e.enabled }))} aria-label={`Use ${e.name}`} /></label>
            </div>
          ))}
          <div className="list-row stack-sm">
            <input className="input" placeholder="Name" value={local.name} onChange={(e) => setLocal({ ...local, name: e.target.value })} style={{ width: 120 }} />
            <input className="input" placeholder="http://localhost:8000" value={local.base_url} onChange={(e) => setLocal({ ...local, base_url: e.target.value })} style={{ flex: 1, minWidth: 150 }} />
            <select className="input" value={local.kind} onChange={(e) => setLocal({ ...local, kind: e.target.value })}>
              <option value="openai_compat">OpenAI-compatible</option>
              <option value="ollama">Ollama</option>
            </select>
            <button className="btn small" disabled={!local.name.trim() || !local.base_url.trim()}
              onClick={() => act(async () => { await api.addEndpoint(local); setLocal({ name: '', base_url: 'http://localhost:', kind: 'openai_compat' }) })}>Add</button>
          </div>
        </div>
        <p>Ollama, LM Studio and llama.cpp are detected automatically on their usual ports.</p>
      </div>
    </>
  )
}

function WebSearchTab() {
  const [status, setStatus] = useState(null)
  const [url, setUrl] = useState('')
  const [key, setKey] = useState('')
  const [test, setTest] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const load = useCallback(async () => {
    const st = await api.researchStatus()
    setStatus(st)
    setUrl(st.self_url)
  }, [])
  useEffect(() => { load().catch((e) => setError(e.message)) }, [load])
  const save = async (body) => {
    setError(null); setTest(null)
    try { await api.saveSettings(body); await load() } catch (e) { setError(e.message) }
  }
  const runTest = async () => {
    setBusy(true); setError(null); setTest(null)
    try { setTest(await api.researchTest('latest stable Python release')) } catch (e) { setError(e.message) }
    setBusy(false)
  }
  if (!status) return <p className="muted">{error || 'Checking…'}</p>
  return (
    <div className="group">
      <div className="list">
        <div className="list-row stack-sm">
          <div className="grow"><b>Search with</b><div className="sub">Self-hosted is free and private. Cloud needs a Firecrawl key and uses credits.</div></div>
          <div className="seg">
            <button className={status.mode === 'self' ? 'on' : ''} onClick={() => save({ firecrawl_mode: 'self' })}>Self-hosted</button>
            <button className={status.mode === 'cloud' ? 'on' : ''} onClick={() => save({ firecrawl_mode: 'cloud' })}>Cloud</button>
          </div>
        </div>
        {status.mode === 'self' ? (
          <div className="list-row stack-sm">
            <div className="grow"><b>Firecrawl address</b><div className="sub">Start it with <code>scripts/firecrawl.sh up</code> (needs Docker)</div></div>
            <input className="input" type="url" value={url} onChange={(e) => setUrl(e.target.value)} style={{ width: 220 }} />
            <button className="btn small" onClick={() => save({ firecrawl_url: url })}>Save</button>
          </div>
        ) : (
          <div className="list-row stack-sm">
            <div className="grow"><b>Firecrawl API key</b><div className="sub">{status.api_key_set ? `Saved (${status.api_key_hint})` : <a href="https://www.firecrawl.dev/app/api-keys" target="_blank" rel="noreferrer noopener">Get a key</a>}</div></div>
            <input className="input" type="password" value={key} placeholder="fc-…" autoComplete="off" onChange={(e) => setKey(e.target.value)} style={{ width: 200 }} />
            <button className="btn small" disabled={!key.trim()} onClick={() => { save({ firecrawl_api_key: key }); setKey('') }}>Save</button>
            {status.api_key_set && <button className="btn small ghost danger" onClick={() => save({ firecrawl_api_key: '' })}>Remove</button>}
          </div>
        )}
        <div className="list-row">
          <div className="grow"><b>Status</b><div className="sub">{status.ready ? `${RESEARCHER} can search the web` : status.error}</div></div>
          {status.ready ? <span className="pill ok">Ready</span> : <span className="pill">Unavailable</span>}
          <button className="btn small" disabled={busy || !status.ready} onClick={runTest}>{busy ? 'Searching…' : 'Test'}</button>
        </div>
      </div>
      <p>{RESEARCHER} uses <a href="https://github.com/firecrawl/firecrawl" target="_blank" rel="noreferrer noopener">Firecrawl</a> to search and read pages, then writes cited briefs for the council.</p>
      {error && <p className="error">{error}</p>}
      {test && (
        <div className="srcs">
          {test.map((r) => <a key={r.url} className="src" href={r.url} target="_blank" rel="noreferrer noopener" style={{ paddingLeft: 10 }}><span className="d">{r.title}</span></a>)}
          {test.length === 0 && <span className="muted small">No results</span>}
        </div>
      )}
    </div>
  )
}

export default function Settings({ mobileBar, onRunSetup }) {
  const [tab, setTab] = useState(() => (window.location.hash.split('/')[1] || 'models'))
  const [inv, setInv] = useState(null)
  const [catalog, setCatalog] = useState([])
  const [error, setError] = useState(null)

  const load = useCallback(async (refresh = false) => {
    try {
      const [i, c] = await Promise.all([api.inventory(8192, refresh), api.catalog(8192)])
      setInv(i)
      setCatalog(c)
    } catch (e) {
      setError(e.message)
    }
  }, [])
  useEffect(() => { load() }, [load])
  const choose = (t) => { setTab(t); window.history.replaceState(null, '', `#settings/${t}`) }

  return (
    <>
      {mobileBar}
      <div className="page">
        <div className="page-inner">
          <div className="settings-head">
            <h1>Settings</h1>
            <div className="seg tabs" role="tablist">
              {TABS.map(([k, label]) => (
                <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? 'on' : ''} onClick={() => choose(k)}>{label}</button>
              ))}
            </div>
          </div>
          {inv && (
            <p className="lead">
              {inv.system.label} · {formatGB(inv.system.usable_bytes)} available for local models ·{' '}
              <button className="linkish" onClick={onRunSetup}>Run setup again</button>
            </p>
          )}
          {error && <p className="error">{error}</p>}
          {tab === 'search' && <WebSearchTab />}
          {tab !== 'search' && !inv && <p className="muted">Checking your models…</p>}
          {tab === 'models' && inv && <ModelsTab inv={inv} catalog={catalog} reload={() => load(true)} />}
          {tab === 'providers' && inv && <ProvidersTab inv={inv} reload={() => load(true)} />}
        </div>
      </div>
    </>
  )
}
