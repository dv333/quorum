import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, formatGB } from '../api'
import { RESEARCHER, modelShort } from '../agents'
import { THEMES, getTheme, setTheme } from '../theme'

const TABS = [['models', 'Models'], ['providers', 'Providers'], ['search', 'Web search'], ['appearance', 'Appearance']]
const FIT = { fits: ['ok', 'Fits'], too_big: ['bad', 'Too big'], unknown: ['', 'Unknown size'], cloud: ['', 'Cloud'] }

// A settings row: what it is and why on the left, the control on the right
function SettingRow({ label, desc, children }) {
  return (
    <div className="set-row">
      <div className="set-label"><b>{label}</b>{desc && <p>{desc}</p>}</div>
      <div className="set-control">{children}</div>
    </div>
  )
}

function Section({ title, desc, children }) {
  return (
    <section className="set-section">
      <div className="set-section-head"><h2>{title}</h2>{desc && <p>{desc}</p>}</div>
      {children}
    </section>
  )
}

// A ring that fills with the share of setup checks that pass
function Ring({ value }) {
  const r = 15
  const c = 2 * Math.PI * r
  return (
    <svg className="ring" width="40" height="40" viewBox="0 0 40 40" aria-hidden="true">
      <circle cx="20" cy="20" r={r} fill="none" strokeWidth="4" className="ring-track" />
      <circle cx="20" cy="20" r={r} fill="none" strokeWidth="4" strokeLinecap="round" className="ring-fill"
        strokeDasharray={`${c * value} ${c}`} transform="rotate(-90 20 20)" />
    </svg>
  )
}

// What Quorum needs to work well, at a glance, with one way to fix the first problem
function SetupStatus({ inv, research, onChoose, onRunSetup }) {
  const [hidden, setHidden] = useState(false)
  if (!inv || hidden) return null
  const fits = inv.models.filter((m) => m.fit === 'fits').length
  // [when fine, is it fine, where to fix it, the button, when not fine]
  const checks = [
    ['Model server running', inv.endpoints.some((e) => e.enabled && e.reachable), 'providers', 'Check providers', 'No model server is running. Start Ollama or add a provider.'],
    [`${fits} models fit in memory`, fits >= 3, 'models', 'Get models', `Only ${fits} model${fits === 1 ? ' fits' : 's fit'} in memory; three or more make a good council.`],
    ['Web search ready', !!research?.ready, 'search', 'Set up web search', `${RESEARCHER} can't search the web, so answers won't be checked against sources.`],
  ]
  const passing = checks.filter(([, ok]) => ok).length
  const firstProblem = checks.find(([, ok]) => !ok)
  return (
    <div className={`setup-status ${firstProblem ? 'warn' : ''}`}>
      <Ring value={passing / checks.length} />
      <div className="grow">
        <b>{firstProblem ? `${checks.length - passing} of ${checks.length} setup checks need attention` : 'Quorum is ready'}</b>
        <p>{firstProblem ? firstProblem[4] : checks.map(([t]) => t).join(' · ')}</p>
      </div>
      {firstProblem ? (
        <button className="btn primary small" onClick={() => onChoose(firstProblem[2])}>{firstProblem[3]}</button>
      ) : (
        <>
          <button className="btn small" onClick={() => setHidden(true)}>Dismiss</button>
          <button className="btn small" onClick={onRunSetup}>Run setup again</button>
        </>
      )}
    </div>
  )
}

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

function AppearanceTab() {
  const [theme, choose] = useState(getTheme)
  return (
    <Section title="Appearance" desc="How Quorum looks on this computer.">
      <SettingRow label="Theme" desc="System follows your computer's light or dark setting. Kept in this browser.">
      <div className="appearance" role="radiogroup" aria-label="Theme">
        {THEMES.map(([k, label]) => (
          <button key={k} role="radio" aria-checked={theme === k} className={`theme-card ${theme === k ? 'on' : ''}`}
            onClick={() => { setTheme(k); choose(k) }}>
            <span className={`swatch ${k}`}><i /></span>
            {label}
          </button>
        ))}
      </div>
      </SettingRow>
    </Section>
  )
}

function matches(q, ...fields) {
  const needle = q.trim().toLowerCase()
  return !needle || fields.some((f) => (f || '').toLowerCase().includes(needle))
}

const GB = 1024 ** 3 // as the backend and formatGB count it
const TIERS = [['Small', 'under 5 GB', 0, 5 * GB], ['Medium', '5 to 15 GB', 5 * GB, 15 * GB], ['Large', '15 GB and up', 15 * GB, Infinity]]

function Chips({ value, onChange, options }) {
  return (
    <div className="chips" role="tablist" aria-label="Show">
      {options.map(([k, label, n]) => (
        <button key={k} role="tab" aria-selected={value === k} className={`chip ${value === k ? 'on' : ''}`} onClick={() => onChange(k)}>
          {label} <span className="n">{n}</span>
        </button>
      ))}
    </div>
  )
}

function InstalledRow({ m, loaded, inCouncil, onRemove }) {
  const [tone, label] = FIT[m.fit] || FIT.unknown
  return (
    <div className="list-row model-row">
      <div className="grow">
        <b>{modelShort(m.model)}</b>
        {m.thinking && <span className="pill think">reasons</span>}
        {inCouncil && <span className="pill">in council</span>}
        {!m.chat && <span className="pill">not for chat</span>}
        <div className="sub">{[m.local ? null : m.endpoint_name, m.family, m.params, m.quant].filter(Boolean).join(' · ')}</div>
      </div>
      {m.local && <span className="small muted size">{formatGB(m.est_bytes)}</span>}
      {loaded && <span className="pill ok">In memory</span>}
      <span className={`pill ${tone}`}>{label}</span>
      {onRemove && (
        <button className="icon-btn remove" aria-label={`Remove ${m.model}`} title="Remove from this Mac" onClick={() => onRemove(m)}>
          <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true"><path d="M3.5 5.5h13M8 5.5V4a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1v1.5M5.5 5.5l.7 10a1.5 1.5 0 0 0 1.5 1.4h4.6a1.5 1.5 0 0 0 1.5-1.4l.7-10" /></svg>
        </button>
      )}
    </div>
  )
}

// Any model in Ollama's library by exact name: its size and fit before downloading
function GetByName({ endpoints, reload, query }) {
  const [name, setName] = useState('')
  const [found, setFound] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const check = async () => {
    if (!name.trim()) return
    setBusy(true); setError(null); setFound(null)
    try { setFound(await api.lookupModel(name.trim())) } catch (e) { setError(e.message) }
    setBusy(false)
  }
  const [tone, label] = found?.found ? (FIT[found.fit] || FIT.unknown) : []
  const browse = `https://ollama.com/search${query.trim() ? `?q=${encodeURIComponent(query.trim())}` : ''}`
  return (
    <div className="get-by-name">
      <div className="list-row">
        <div className="grow"><b>Get any Ollama model</b><div className="sub">Type its name and tag, like <code>qwen3:30b</code>, to see its size and whether it fits.</div></div>
        <input className="input" value={name} placeholder="model:tag" aria-label="Model name" autoComplete="off" spellCheck={false}
          onChange={(e) => { setName(e.target.value); setFound(null) }} onKeyDown={(e) => e.key === 'Enter' && check()} />
        <button className="btn small" disabled={busy || !name.trim()} onClick={check}>{busy ? 'Checking…' : 'Check'}</button>
      </div>
      {found && !found.found && <div className="list-row"><span className="muted small">No model called “{found.model}” in Ollama's library. Check the name and tag.</span></div>}
      {found?.found && (
        <div className="list-row">
          <div className="grow"><b>{found.model}</b><div className="sub">{formatGB(found.size_bytes)} download</div></div>
          <span className={`pill ${tone}`}>{label}</span>
          {found.fit !== 'too_big' && <PullButton endpoints={endpoints} model={found.model} onDone={reload} />}
        </div>
      )}
      {error && <div className="list-row"><span className="error small">{error}</span></div>}
      <p className="small muted browse">Not sure of the name? <a href={browse} target="_blank" rel="noreferrer noopener">Browse Ollama's library{query.trim() ? ` for “${query.trim()}”` : ''} ↗</a></p>
    </div>
  )
}

function ModelsTab({ inv, catalog, reload }) {
  const [view, setView] = useState('installed')
  const [q, setQ] = useState('')
  const [sort, setSort] = useState('size')
  const [council, setCouncil] = useState(() => new Set())
  const [showTooBig, setShowTooBig] = useState(false)
  const [error, setError] = useState(null)
  useEffect(() => {
    api.autoCouncil(8192).then((c) => setCouncil(new Set((c.seats || []).map((s) => s.model))), () => {})
  }, [inv])

  const local = inv.models.filter((m) => m.local)
  const cloud = inv.models.filter((m) => !m.local)
  const suggested = catalog.filter((c) => !c.installed)
  const byName = (a, b) => a.model.localeCompare(b.model)
  const bySize = (a, b) => (b.est_bytes || 0) - (a.est_bytes || 0)
  const installed = local.filter((m) => matches(q, m.model, m.family)).sort(sort === 'name' ? byName : bySize)
  const fits = suggested.filter((c) => c.fit !== 'too_big' && matches(q, c.model, c.family, c.strengths)).sort((a, b) => a.size_bytes - b.size_bytes)
  const tooBig = suggested.filter((c) => c.fit === 'too_big' && matches(q, c.model, c.family, c.strengths))
  const cloudGroups = Object.entries(
    cloud.filter((m) => matches(q, m.model, m.endpoint_name)).reduce((g, m) => ({ ...g, [m.endpoint_name]: [...(g[m.endpoint_name] || []), m] }), {}),
  )
  const inCouncil = local.filter((m) => council.has(m.model)).length

  const remove = async (m) => {
    if (!confirm(`Remove ${m.model} from this Mac? It frees about ${formatGB(m.size_bytes || m.est_bytes)} of disk; you can download it again later.`)) return
    setError(null)
    try { await api.deleteModel(m.endpoint_id, m.model); reload() } catch (e) { setError(e.message) }
  }

  const options = [['installed', 'Installed', local.length], ['get', 'Get more', suggested.length]]
  if (cloud.length) options.push(['cloud', 'Cloud', cloud.length])

  return (
    <>
      <div className="models-bar">
        <Chips value={view} onChange={setView} options={options} />
        <SearchField value={q} onChange={setQ} placeholder={view === 'get' ? 'Filter suggestions' : 'Search models'} />
      </div>
      {error && <p className="error">{error}</p>}

      {view === 'installed' && (
        <div className="group">
          <div className="group-head">
            <span className="muted small">{local.length} installed · {inCouncil} in your council · {formatGB(inv.system.usable_bytes)} memory for models</span>
            <select className="input select-sm" value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort models">
              <option value="size">Largest first</option>
              <option value="name">Name</option>
            </select>
          </div>
          {installed.length > 0 ? (
            <div className="list">
              {installed.map((m) => (
                <InstalledRow key={m.key} m={m} inCouncil={council.has(m.model)}
                  loaded={inv.loaded.some((l) => l.endpoint_id === m.endpoint_id && l.model === m.model)}
                  onRemove={inv.endpoints.find((e) => e.id === m.endpoint_id)?.kind === 'ollama' ? remove : null} />
              ))}
            </div>
          ) : (
            <p className="muted">{q ? `No installed models match “${q}”.` : 'No models yet. '}{!q && <button className="linkish" onClick={() => setView('get')}>Get a few</button>}</p>
          )}
        </div>
      )}

      {view === 'get' && (
        <>
          <div className="group"><div className="list"><GetByName endpoints={inv.endpoints} reload={reload} query={q} /></div></div>
          {TIERS.map(([tier, hint, lo, hi]) => {
            const items = fits.filter((c) => c.size_bytes >= lo && c.size_bytes < hi)
            if (!items.length) return null
            return (
              <div className="group" key={tier}>
                <h3>{tier} <span className="faint">· {hint}</span></h3>
                <div className="list">
                  {items.map((c) => {
                    const [tone, label] = FIT[c.fit] || FIT.unknown
                    return (
                      <div className="list-row" key={c.model}>
                        <div className="grow"><b>{c.model}</b> <span className="faint small">{c.family}</span><div className="sub">{c.strengths}</div></div>
                        <span className="small muted size">{formatGB(c.size_bytes)}</span>
                        <span className={`pill ${tone}`}>{label}</span>
                        <PullButton endpoints={inv.endpoints} model={c.model} onDone={reload} />
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })}
          {tooBig.length > 0 && (
            <div className="group">
              <button className="linkish" onClick={() => setShowTooBig(!showTooBig)}>
                {showTooBig ? 'Hide' : 'Show'} {tooBig.length} too big for this machine
              </button>
              {showTooBig && (
                <div className="list" style={{ marginTop: 8 }}>
                  {tooBig.map((c) => (
                    <div className="list-row" key={c.model}>
                      <div className="grow"><b>{c.model}</b><div className="sub">{c.strengths}</div></div>
                      <span className="small muted size">{formatGB(c.size_bytes)}</span>
                      <span className="pill bad">Too big</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          {fits.length === 0 && tooBig.length === 0 && q && <p className="muted">No suggestions match “{q}”. Try a name above, or browse Ollama's library.</p>}
          <p className="small muted">Mixing families (Qwen, Gemma, Llama, Mistral…) makes for a more genuine debate than copies of one model.</p>
        </>
      )}

      {view === 'cloud' && cloudGroups.map(([name, ms]) => <CloudGroup key={name} name={name} models={ms} council={council} />)}
    </>
  )
}

// A cloud provider can list hundreds of models: show 20, then more on request
function CloudGroup({ name, models, council }) {
  const [shown, setShown] = useState(20)
  return (
    <div className="group">
      <h3>{name} <span className="faint">· {models.length}</span></h3>
      <div className="list">
        {models.slice(0, shown).map((m) => <InstalledRow key={m.key} m={m} inCouncil={council.has(m.model)} loaded={false} onRemove={null} />)}
      </div>
      {models.length > shown && (
        <button className="more-link" onClick={() => setShown(shown + 40)}>Show more <span>· {models.length - shown} more</span></button>
      )}
    </div>
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
    <Section title="Web search"
      desc={<>{RESEARCHER} uses <a href="https://github.com/firecrawl/firecrawl" target="_blank" rel="noreferrer noopener">Firecrawl</a> to search and read pages, then writes cited briefs for the council.</>}>
      <SettingRow label="Search with" desc="Self-hosted is free and private. Cloud needs a Firecrawl key and uses credits.">
        <div className="seg">
          <button className={status.mode === 'self' ? 'on' : ''} onClick={() => save({ firecrawl_mode: 'self' })}>Self-hosted</button>
          <button className={status.mode === 'cloud' ? 'on' : ''} onClick={() => save({ firecrawl_mode: 'cloud' })}>Cloud</button>
        </div>
      </SettingRow>
      {status.mode === 'self' ? (
        <SettingRow label="Firecrawl address" desc={<>Start it with <code>scripts/firecrawl.sh up</code> (needs Docker).</>}>
          <input className="input" type="url" value={url} aria-label="Firecrawl address" onChange={(e) => setUrl(e.target.value)} />
          <button className="btn small" onClick={() => save({ firecrawl_url: url })}>Save</button>
        </SettingRow>
      ) : (
        <SettingRow label="Firecrawl API key"
          desc={status.api_key_set ? `Saved (${status.api_key_hint}).` : <a href="https://www.firecrawl.dev/app/api-keys" target="_blank" rel="noreferrer noopener">Get a key</a>}>
          <input className="input" type="password" value={key} placeholder="fc-…" autoComplete="off" aria-label="Firecrawl API key" onChange={(e) => setKey(e.target.value)} />
          <button className="btn small" disabled={!key.trim()} onClick={() => { save({ firecrawl_api_key: key }); setKey('') }}>Save</button>
          {status.api_key_set && <button className="btn small ghost danger" onClick={() => save({ firecrawl_api_key: '' })}>Remove</button>}
        </SettingRow>
      )}
      <SettingRow label="Status" desc={status.ready ? `${RESEARCHER} can search the web.` : status.error}>
        {status.ready ? <span className="pill ok">✓ Ready</span> : <span className="pill warn">Unavailable</span>}
        <button className="btn small" disabled={busy || !status.ready} onClick={runTest}>{busy ? 'Searching…' : 'Test'}</button>
      </SettingRow>
      {error && <p className="error">{error}</p>}
      {test && (
        <div className="srcs">
          {test.map((r) => <a key={r.url} className="src" href={r.url} target="_blank" rel="noreferrer noopener" style={{ paddingLeft: 10 }}><span className="d">{r.title}</span></a>)}
          {test.length === 0 && <span className="muted small">No results</span>}
        </div>
      )}
    </Section>
  )
}

export default function Settings({ mobileBar, onRunSetup }) {
  const [tab, setTab] = useState(() => (window.location.hash.split('/')[1] || 'models'))
  const [inv, setInv] = useState(null)
  const [catalog, setCatalog] = useState([])
  const [error, setError] = useState(null)
  const [research, setResearch] = useState(null)
  useEffect(() => { api.researchStatus().then(setResearch, () => {}) }, [tab])

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
            <div>
              <h1>Settings</h1>
              <p className="lead">
                Models, providers, web search and appearance
                {inv && <> · {inv.system.label} · {formatGB(inv.system.usable_bytes)} available for local models</>}
              </p>
            </div>
          </div>
          <div className="tabs-line" role="tablist" aria-label="Settings sections">
            {TABS.map(([k, label]) => (
              <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? 'on' : ''} onClick={() => choose(k)}>{label}</button>
            ))}
          </div>
          <SetupStatus inv={inv} research={research} onChoose={choose} onRunSetup={onRunSetup} />
          {error && <p className="error">{error}</p>}
          {tab === 'search' && <WebSearchTab />}
          {tab === 'appearance' && <AppearanceTab />}
          {!['search', 'appearance'].includes(tab) && !inv && <p className="muted">Checking your models…</p>}
          {tab === 'models' && inv && <ModelsTab inv={inv} catalog={catalog} reload={() => load(true)} />}
          {tab === 'providers' && inv && <ProvidersTab inv={inv} reload={() => load(true)} />}
        </div>
      </div>
    </>
  )
}
