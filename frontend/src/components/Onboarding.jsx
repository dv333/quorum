import { useCallback, useEffect, useRef, useState } from 'react'
import { api, formatGB } from '../api'
import { AGENTS, RESEARCHER } from '../agents'

// First-launch walkthrough: Ollama → starter models → web search (optional) → try a conundrum.

const STEPS = ['Welcome', 'Ollama', 'Models', 'Web search', 'Try it']

const INSTALL = {
  Darwin: { label: 'macOS', steps: [['Download the app', 'https://ollama.com/download/mac'], ['or with Homebrew', 'brew install ollama']] },
  Linux: { label: 'Linux', steps: [['Install with one command', 'curl -fsSL https://ollama.com/install.sh | sh']] },
  Windows: { label: 'Windows', steps: [['Download the installer', 'https://ollama.com/download/windows']] },
}

function Copyable({ text }) {
  const [copied, setCopied] = useState(false)
  if (text.startsWith('http')) {
    return <a className="btn small blue" href={text} target="_blank" rel="noreferrer noopener">Open download page</a>
  }
  return (
    <span className="copyable">
      <code>{text}</code>
      <button className="btn small" onClick={() => { navigator.clipboard?.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500) }}>
        {copied ? 'Copied' : 'Copy'}
      </button>
    </span>
  )
}

function Check({ ok, children }) {
  return <div className={`check ${ok ? 'ok' : ''}`}><span className="tick">{ok ? '✓' : '•'}</span>{children}</div>
}

function OllamaStep({ status, next }) {
  const os = INSTALL[status.os] || INSTALL.Darwin
  const o = status.ollama
  return (
    <>
      <h2>Your models run on Ollama</h2>
      <p className="lead">Ollama keeps every model on this {status.os === 'Darwin' ? 'Mac' : 'computer'}. Nothing you ask leaves it.</p>
      <div className="list">
        <div className="list-row"><Check ok={o.installed}>Ollama installed</Check></div>
        <div className="list-row"><Check ok={o.running}>Ollama running{o.version ? ` (version ${o.version})` : ''}</Check></div>
      </div>
      {!o.installed && (
        <div className="group">
          <h3>Install Ollama for {os.label}</h3>
          <div className="list">
            {os.steps.map(([label, cmd]) => (
              <div className="list-row stack-sm" key={cmd}><div className="grow">{label}</div><Copyable text={cmd} /></div>
            ))}
          </div>
          <p>This page checks again every few seconds.</p>
        </div>
      )}
      {o.installed && !o.running && (
        <div className="group">
          <h3>Start Ollama</h3>
          <div className="list">
            <div className="list-row stack-sm">
              <div className="grow">{status.os === 'Darwin' ? 'Open the Ollama app from Applications, or run' : 'Run'}</div>
              <Copyable text="ollama serve" />
            </div>
          </div>
        </div>
      )}
      <div className="ob-actions">
        <button className="btn blue" disabled={!o.running} onClick={next}>Continue</button>
      </div>
    </>
  )
}

function ModelsStep({ status, refresh, next }) {
  const [picked, setPicked] = useState(() => new Set(status.starter.filter((m) => !m.installed).map((m) => m.model)))
  const [progress, setProgress] = useState({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const toGet = status.starter.filter((m) => picked.has(m.model) && !m.installed)
  const total = toGet.reduce((sum, m) => sum + (m.size_bytes || 0), 0)

  const download = async () => {
    setBusy(true)
    setError(null)
    for (const m of toGet) {
      try {
        await api.pullModel(status.ollama.endpoint_id, m.model, (ev) => {
          if (ev.error) throw new Error(ev.error)
          const pct = ev.total ? Math.round((100 * (ev.completed || 0)) / ev.total) : null
          setProgress((p) => ({ ...p, [m.model]: { pct, status: ev.status } }))
        })
        setProgress((p) => ({ ...p, [m.model]: { pct: 100, status: 'done' } }))
      } catch (e) {
        setError(`${m.model}: ${e.message}`)
        break
      }
    }
    await refresh()
    setBusy(false)
  }

  const haveEnough = status.models.count >= 2
  return (
    <>
      <h2>Pick your first council</h2>
      <p className="lead">
        Three different model families that fit together in {formatGB(status.system.usable_bytes)} of memory.
        Different families disagree in useful ways.
      </p>
      <div className="list">
        {status.starter.map((m, i) => {
          const p = progress[m.model]
          const name = Object.keys(AGENTS)[i]
          return (
            <label className="list-row" key={m.model} style={{ cursor: m.installed ? 'default' : 'pointer' }}>
              <input type="checkbox" className="ob-check" checked={m.installed || picked.has(m.model)} disabled={m.installed || busy}
                onChange={(e) => setPicked((s) => { const n = new Set(s); e.target.checked ? n.add(m.model) : n.delete(m.model); return n })} />
              <span className="ob-emoji" aria-hidden="true">{AGENTS[name].emoji}</span>
              <div className="grow"><b>{m.model}</b><div className="sub">{m.strengths}</div></div>
              {m.installed || p?.status === 'done' ? <span className="pill ok">Installed</span>
                : p ? <span className="small muted" style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
                    <span className="progress-line"><i style={{ width: `${p.pct || 0}%` }} /></span>{p.pct != null ? `${p.pct}%` : p.status}
                  </span>
                : <span className="small muted">{formatGB(m.size_bytes)}</span>}
            </label>
          )
        })}
      </div>
      {status.models.count > 0 && <p className="small muted">You already have {status.models.count} model{status.models.count === 1 ? '' : 's'}: {status.models.names.join(', ')}.</p>}
      {error && <p className="error">{error}</p>}
      <div className="ob-actions">
        {toGet.length > 0 && (
          <button className="btn blue" disabled={busy} onClick={download}>
            {busy ? 'Downloading…' : `Download ${toGet.length} model${toGet.length === 1 ? '' : 's'} · ${formatGB(total)}`}
          </button>
        )}
        <button className={`btn ${toGet.length ? '' : 'blue'}`} disabled={busy} onClick={next}>{haveEnough || !toGet.length ? 'Continue' : 'Skip for now'}</button>
      </div>
    </>
  )
}

function SearchStep({ status, refresh, next }) {
  const [key, setKey] = useState('')
  const [error, setError] = useState(null)
  const job = status.firecrawl_job
  const ready = status.research.ready
  const start = async () => {
    setError(null)
    try { await api.startFirecrawl(); await refresh() } catch (e) { setError(e.message) }
  }
  const saveKey = async () => {
    setError(null)
    try { await api.saveSettings({ firecrawl_mode: 'cloud', firecrawl_api_key: key }); setKey(''); await refresh() } catch (e) { setError(e.message) }
  }
  return (
    <>
      <h2>Let {RESEARCHER} search the web <span className="faint">(optional)</span></h2>
      <p className="lead">{AGENTS[RESEARCHER].emoji} {RESEARCHER} looks up current facts, cites sources and fact-checks the final answer. Local models don't know recent news without it.</p>
      {ready ? (
        <div className="list"><div className="list-row"><Check ok>Web search is ready</Check></div></div>
      ) : (
        <div className="list">
          <div className="list-row stack-sm">
            <div className="grow"><b>Run it on this Mac</b>
              <div className="sub">
                {!status.docker.installed ? <>Needs <a href="https://www.docker.com/products/docker-desktop/" target="_blank" rel="noreferrer noopener">Docker Desktop</a>. Free and private; about 4 GB to download.</>
                  : !status.docker.running ? 'Start Docker Desktop, then try again.'
                  : 'Free and private. First start downloads about 4 GB.'}
              </div>
            </div>
            <button className="btn small blue" disabled={!status.docker.running || job.state === 'running'} onClick={start}>
              {job.state === 'running' ? 'Starting…' : job.state === 'failed' ? 'Try again' : 'Start'}
            </button>
          </div>
          {job.log.length > 0 && <pre className="ob-log">{job.log.join('\n')}</pre>}
          <div className="list-row stack-sm">
            <div className="grow"><b>Or use Firecrawl cloud</b><div className="sub">Paste a key from <a href="https://www.firecrawl.dev/app/api-keys" target="_blank" rel="noreferrer noopener">firecrawl.dev</a>. Searches use credits.</div></div>
            <input className="input" type="password" autoComplete="off" placeholder="fc-…" value={key} onChange={(e) => setKey(e.target.value)} style={{ width: 180 }} />
            <button className="btn small" disabled={!key.trim()} onClick={saveKey}>Save</button>
          </div>
        </div>
      )}
      {error && <p className="error">{error}</p>}
      <div className="ob-actions">
        <button className={`btn ${ready ? 'blue' : ''}`} onClick={next}>{ready ? 'Continue' : 'Skip for now'}</button>
      </div>
    </>
  )
}

function TryStep({ status, onStart, onFinish }) {
  const [busy, setBusy] = useState(null)
  return (
    <>
      <h2>You're ready</h2>
      <p className="lead">Ask anything. If your conundrum needs more detail, the chair will ask you first, one question at a time.</p>
      <div className="ob-samples">
        {status.samples.map((q) => (
          <button key={q} className="ob-sample" disabled={!!busy || status.models.count === 0} onClick={async () => { setBusy(q); await onStart(q) }}>
            {busy === q ? 'Starting…' : q}<span aria-hidden="true">→</span>
          </button>
        ))}
      </div>
      {status.models.count === 0 && <p className="error">Download at least one model first.</p>}
      <div className="ob-actions"><button className="btn" onClick={onFinish}>I'll write my own</button></div>
    </>
  )
}

export default function Onboarding({ appName, onDone, onStartQuestion }) {
  const [step, setStep] = useState(0)
  const [status, setStatus] = useState(null)
  const [error, setError] = useState(null)
  const stepRef = useRef(step)
  stepRef.current = step

  const refresh = useCallback(async () => {
    try { setStatus(await api.setupStatus()); setError(null) } catch (e) { setError(e.message) }
  }, [])
  useEffect(() => { refresh() }, [refresh])
  // Keep checking while waiting on Ollama or on web search to come up
  useEffect(() => {
    const t = setInterval(() => { if ([1, 3].includes(stepRef.current)) refresh() }, 3000)
    return () => clearInterval(t)
  }, [refresh])

  const next = () => setStep((s) => Math.min(s + 1, STEPS.length - 1))
  const finish = () => { try { localStorage.setItem('quorum.onboarded', '1') } catch { /* private mode */ } onDone() }

  return (
    <div className="onboarding">
      <div className="ob-card">
        <div className="ob-steps" aria-label={`Step ${step + 1} of ${STEPS.length}`}>
          {STEPS.map((label, i) => (
            <button key={label} className={`ob-dot ${i === step ? 'on' : ''} ${i < step ? 'done' : ''}`} onClick={() => i < step && setStep(i)} disabled={i > step} title={label} />
          ))}
          <button className="btn ghost small ob-skip" onClick={finish}>Skip setup</button>
        </div>
        {error && <p className="error">{error}</p>}
        {step === 0 && (
          <div className="ob-welcome">
            <div className="mark big alive"><span /><span /><span /></div>
            <h1>Welcome to {appName}</h1>
            <p className="tagline">Many minds. One answer.</p>
            <div className="ob-points">
              <div><span>{AGENTS.Otter.emoji}{AGENTS.Panda.emoji}{AGENTS.Koala.emoji}</span>Several AI models debate your conundrum, then agree on one answer.</div>
              <div><span>🔒</span>Everything runs on your own computer: private, free and offline-friendly.</div>
              <div><span>{AGENTS.Beagle.emoji}</span>Optional web research keeps answers current and cited.</div>
            </div>
            <div className="ob-actions"><button className="btn blue" onClick={next} disabled={!status}>Set up in 2 minutes</button></div>
          </div>
        )}
        {step > 0 && !status && <p className="muted">Checking your computer…</p>}
        {step === 1 && status && <OllamaStep status={status} next={next} />}
        {step === 2 && status && <ModelsStep status={status} refresh={refresh} next={next} />}
        {step === 3 && status && <SearchStep status={status} refresh={refresh} next={next} />}
        {step === 4 && status && (
          <TryStep status={status} onFinish={finish} onStart={async (q) => { finish(); await onStartQuestion(q) }} />
        )}
      </div>
    </div>
  )
}
