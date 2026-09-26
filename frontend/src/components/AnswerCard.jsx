import { useCallback, useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

// Models write "~$12k" for approximations; only ~~double~~ tildes should strike through
const GFM = [[remarkGfm, { singleTilde: false }]]
import { api, exportMarkdown, exportUrl } from '../api'
import { RESEARCHER, agentFor, formatDuration, formatTime, formatTokens, modelShort } from '../agents'
import { CopyButton, Markdown, Orb, copyText } from './Message'
import Mermaid from './Mermaid'

const LEVELS = [['simple', 'Simple'], ['standard', 'Standard'], ['expert', 'Expert']]

// Splits the chair's structured answer (BOTTOM LINE + ## sections) into display parts.
// Falls back to plain markdown when the model ignored the format.
export function parseAnswer(text) {
  const out = { bottom: null, sections: [], diagrams: [], dissent: null }
  if (!text) return out
  // Some models write footnote-style citations ([^1]); show them like the rest ([1])
  let rest = text.replace(/\[\^(\d+)\]/g, '[$1]')
  const bl = rest.match(/^\s*[*_#]*\s*BOTTOM\s*LINE\s*[*_]*\s*[:：]\s*(.+)$/im)
  if (bl) {
    let bottom = bl[1].trim()
    // Models sometimes leave a bold marker unclosed; drop them all rather than show asterisks
    if ((bottom.match(/\*\*/g) || []).length % 2) bottom = bottom.replace(/\*\*/g, '')
    out.bottom = bottom.replace(/^\*\*([^*]*)\*\*\.?$/, '$1').trim()
    rest = rest.replace(bl[0], '')
  }
  rest = rest.replace(/```mermaid\s*([\s\S]*?)```/gi, (_, code) => { out.diagrams.push(code.trim()); return '' })
  const parts = rest.split(/^##\s+(.+)$/m)
  if (parts[0].trim()) out.sections.push({ title: null, content: parts[0].trim() })
  for (let i = 1; i < parts.length; i += 2) {
    const title = parts[i].trim()
    const content = (parts[i + 1] || '').trim()
    if (/diagram/i.test(title)) continue
    if (/differ|dissent|disagree/i.test(title)) {
      if (content && !/^(none|nothing significant)\b/i.test(content)) out.dissent = content
      continue
    }
    if (content) out.sections.push({ title, content })
  }
  return out
}

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`

function headline(verdict, finalStances, seatsCount, factChecked, chairName) {
  const agreeN = finalStances.filter((s) => s === 'AGREE').length
  let text, tone
  if (!verdict) return null
  if (verdict.reason === 'direct') {
    text = `Answered by ${chairName} · no debate needed`
    tone = 'agree'
  } else if (verdict.reason === 'consensus') {
    text = `All ${seatsCount} agreed in round ${verdict.rounds}`
    tone = 'agree'
  } else if (verdict.reason === 'manual') {
    text = `You called it after ${plural(verdict.rounds, 'round')}`
    tone = agreeN === seatsCount ? 'agree' : 'refine'
  } else {
    text = `${agreeN} of ${seatsCount} agreed after ${plural(verdict.rounds, 'round')}`
    tone = agreeN === seatsCount ? 'agree' : 'refine'
  }
  return (
    <>
      <b className={tone}>● {text}</b>
      {factChecked && <span>· facts checked by {RESEARCHER}</span>}
      <span>· {formatTime(verdict.created_at)}</span>
    </>
  )
}

function Behind({ metrics, seats, chairHandle, rounds }) {
  if (!metrics?.actors?.length) return null
  const order = [...seats.map((s) => s.handle), 'Chair', RESEARCHER]
  const actors = [...metrics.actors].sort((a, b) => {
    const ia = order.indexOf(a.actor), ib = order.indexOf(b.actor)
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib)
  })
  const max = Math.max(...actors.map((a) => a.duration_ms), 1)
  const t = metrics.totals
  const modelsCount = new Set(actors.flatMap((a) => a.models).filter((m) => m !== 'firecrawl')).size
  return (
    <details className="behind" open>
      <summary>
        Behind the answer
        <span>· {modelsCount} model{modelsCount === 1 ? '' : 's'} · {rounds} round{rounds === 1 ? '' : 's'} · {formatDuration(t.duration_ms)} of model time</span>
      </summary>
      <div className="stats">
        <div className="stat"><div>{formatTokens(t.prompt_tokens + t.output_tokens)}</div><small>tokens in and out</small></div>
        <div className="stat"><div>{formatDuration(t.duration_ms)}</div><small>total model time</small></div>
        <div className="stat"><div>{t.searches}</div><small>web searches</small></div>
        <div className="stat"><div>{t.pages}</div><small>pages read</small></div>
      </div>
      <div className="bars">
        {actors.map((a) => {
          const models = a.models.filter((m) => m !== 'firecrawl').map(modelShort).join(', ')
          const detail = a.actor === RESEARCHER
            ? `${formatDuration(a.duration_ms)} · ${formatTokens(a.prompt_tokens + a.output_tokens)} tok · ${a.searches} searches`
            : `${formatDuration(a.duration_ms)} · ${formatTokens(a.prompt_tokens + a.output_tokens)} tok${a.tok_per_s ? ` · ${Math.round(a.tok_per_s)} t/s` : ''}`
          return (
            <FragmentRow key={a.actor} actor={a.actor} chair={a.actor === chairHandle} models={models}
              width={(100 * a.duration_ms) / max} detail={detail}
              title={`${a.calls} model calls · ${a.prompt_tokens.toLocaleString()} tokens in · ${a.output_tokens.toLocaleString()} out`} />
          )
        })}
      </div>
    </details>
  )
}

function FragmentRow({ actor, chair, models, width, detail, title }) {
  const color = agentFor(actor).color
  return (
    <>
      <div className="n" title={models}>
        <Orb handle={actor} size="sm" />{actor}{chair && ' ★'} <small>{models}</small>
      </div>
      <div className="track" style={{ '--c': `var(--${color})` }} title={title}><i style={{ width: `${Math.max(width, 2)}%` }} /></div>
      <div className="v">{detail}</div>
    </>
  )
}

// "Why?": select any passage of the answer to see who argued for it, who pushed back, and the sources behind it
function useSelection(cardRef, enabled) {
  const [sel, setSel] = useState(null) // { text, x, y }
  useEffect(() => {
    if (!enabled) return undefined
    const check = () => {
      const s = window.getSelection()
      const text = s?.toString().trim() || ''
      const card = cardRef.current
      if (!card || !s?.rangeCount || text.length < 3 || text.length > 600) { setSel(null); return }
      const range = s.getRangeAt(0)
      const body = card.querySelector('.answer-text')
      if (!body?.contains(range.commonAncestorContainer)) { setSel(null); return }
      const r = range.getBoundingClientRect()
      const c = card.getBoundingClientRect()
      setSel({ text, x: r.left + r.width / 2 - c.left, y: r.top - c.top, below: r.bottom - c.top })
    }
    const onUp = () => setTimeout(check, 0)
    document.addEventListener('mouseup', onUp)
    document.addEventListener('keyup', onUp)
    document.addEventListener('touchend', onUp)
    return () => {
      document.removeEventListener('mouseup', onUp)
      document.removeEventListener('keyup', onUp)
      document.removeEventListener('touchend', onUp)
    }
  }, [cardRef, enabled])
  return [sel, setSel]
}

function WhyPanel({ state, x, y, onClose }) {
  const { loading, data, error, text, chair } = state
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])
  const empty = data && !data.support.length && !data.challenges.length && !data.sources.length
  return (
    <div className="why-panel" style={{ '--x': `${x}px`, top: y }} role="dialog" aria-label="Why the council said this">
      <div className="why-head">
        <span>“{text.length > 90 ? `${text.slice(0, 90)}…` : text}”</span>
        <button className="icon-btn" onClick={onClose} aria-label="Close">×</button>
      </div>
      {loading && <div className="why-loading"><span className="typing"><i /><i /><i /></span> {chair} is tracing this through the debate…</div>}
      {error && <div className="why-loading error">{error}</div>}
      {data && (
        <>
          {data.summary && <p className="why-summary">{data.summary}</p>}
          {data.support.length > 0 && (
            <div className="why-group"><small>Argued for by</small>
              {data.support.map((s, i) => <div className="why-row" key={i}><Orb handle={s.agent} size="sm" /><b>{s.agent}</b><span>{s.point}</span></div>)}
            </div>
          )}
          {data.challenges.length > 0 && (
            <div className="why-group"><small>Challenged by</small>
              {data.challenges.map((s, i) => <div className="why-row" key={i}><Orb handle={s.agent} size="sm" /><b>{s.agent}</b><span>{s.point}</span></div>)}
            </div>
          )}
          {data.sources.length > 0 && (
            <div className="why-group"><small>Sources</small>
              {data.sources.map((s, i) => <a key={i} className="why-src" href={s.url} target="_blank" rel="noreferrer">{s.title}</a>)}
            </div>
          )}
          {empty && <p className="why-summary faint">The debate doesn't clearly trace this passage to one agent or source; the chair likely combined several points.</p>}
        </>
      )}
    </div>
  )
}

// Export: Markdown to the clipboard or a file (with or without the debate), or print / save as PDF
function ExportMenu({ debateId, level, onPrint }) {
  const [open, setOpen] = useState(false)
  const [note, setNote] = useState(null)
  const ref = useRef(null)
  useEffect(() => {
    if (!open) return
    const close = (e) => { if (e.type === 'keydown' ? e.key === 'Escape' : !ref.current?.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', close)
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', close) }
  }, [open])
  const flash = (text) => { setNote(text); setOpen(false); setTimeout(() => setNote(null), 1600) }
  const copy = async () => {
    try { await copyText(await exportMarkdown(debateId, { level })); flash('Copied') } catch (e) { flash(e.message) }
  }
  return (
    <div className="export" ref={ref}>
      <button className={`copy-btn ${open ? 'on' : ''}`} aria-label="Export" title={note || 'Export'} aria-haspopup="menu"
        aria-expanded={open} onClick={() => setOpen(!open)}>
        <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M10 12.5V2.5m0 0L6.5 6M10 2.5 13.5 6" strokeLinecap="round" strokeLinejoin="round" /><path d="M5 9.5H4.5a2 2 0 0 0-2 2v4a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-4a2 2 0 0 0-2-2H15" strokeLinecap="round" /></svg>
      </button>
      {note && <span className="export-note">{note}</span>}
      {open && (
        <div className="menu" role="menu">
          <button role="menuitem" onClick={copy}>Copy as Markdown</button>
          <a role="menuitem" href={exportUrl(debateId, { level, download: true })} onClick={() => setOpen(false)}>Download Markdown</a>
          <a role="menuitem" href={exportUrl(debateId, { level, debate: true, download: true })} onClick={() => setOpen(false)}>Download with the debate</a>
          <hr />
          <button role="menuitem" onClick={() => { setOpen(false); onPrint() }}>Print or save as PDF</button>
        </div>
      )}
    </div>
  )
}

// The claim ledger behind the answer: each material claim, what the sources say about it, and the exact quote
const CLAIM_LABEL = { supported: 'Supported', partly: 'Partly supported', contradicted: 'Contradicted', unknown: 'Unverified' }

function EvidenceSummary({ claims, evidence }) {
  if (!claims.length) return null
  const n = (s) => claims.filter((c) => c.status === s).length
  const parts = ['supported', 'partly', 'contradicted', 'unknown'].filter((s) => n(s)).map((s) => (
    <span key={s} className={`ev-count ev-${s}`}>{n(s)} {CLAIM_LABEL[s].toLowerCase()}</span>
  ))
  return (
    <div className="ev-summary">
      Evidence: {parts.reduce((acc, p, i) => (i ? [...acc, ' · ', p] : [p]), [])}
      {evidence?.revised && <span className="ev-fixed"> · answer corrected to match the evidence</span>}
    </div>
  )
}

function Evidence({ claims }) {
  if (!claims.length) return null
  return (
    <details className="evidence">
      <summary>Evidence checked <span>· {claims.length} claim{claims.length === 1 ? '' : 's'} the answer relies on</span></summary>
      <ul>
        {claims.map((c, i) => (
          <li key={c.id} id={`evidence-${c.id}`}>
            <span className={`ev-chip ev-${c.status}`}><b className="ev-n">{i + 1}</b> {CLAIM_LABEL[c.status] || 'Unverified'}</span>
            <div>
              <div className="ev-claim">{c.claim}</div>
              {c.caveat && <div className="ev-caveat">{c.caveat}</div>}
              {c.quote && <blockquote>“{c.quote}”</blockquote>}
              {c.source_url && <a href={c.source_url} target="_blank" rel="noreferrer">{c.source_title || c.source_url}</a>}
            </div>
          </li>
        ))}
      </ul>
    </details>
  )
}

export default function AnswerCard({ debateId, msg, verdict, seats, chairHandle, metrics, finalStances, factChecked, question, claims = [] }) {
  const [level, setLevel] = useState('standard')
  const [versions, setVersions] = useState({})
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)
  const [diagramFailed, setDiagramFailed] = useState(false)
  const cardRef = useRef(null)
  const onDiagramFail = useCallback(() => setDiagramFailed(true), [])
  const canTrace = !!verdict && verdict.reason !== 'direct' && msg?.status === 'done'
  const [sel, setSel] = useSelection(cardRef, canTrace)
  const [why, setWhy] = useState(null) // { text, x, y, loading, data, error, chair }
  const closeWhy = useCallback(() => setWhy(null), [])
  const traceSelection = async () => {
    const at = sel
    setSel(null)
    window.getSelection()?.removeAllRanges()
    setWhy({ ...at, loading: true, chair: chairHandle || 'The chair' })
    try {
      const data = await api.why(debateId, verdict.id, at.text)
      setWhy((w) => w && w.text === at.text && { ...w, loading: false, data })
    } catch (e) {
      setWhy((w) => w && w.text === at.text && { ...w, loading: false, error: e.message })
    }
  }

  // Print just this answer: a copy goes into a plain top-level container (the app itself scrolls inside a
  // fixed-height window, which would cut the printout at one page), and print CSS hides everything else.
  const print = () => {
    if (!cardRef.current) return
    const holder = document.createElement('div')
    holder.id = 'print-root'
    holder.appendChild(cardRef.current.cloneNode(true))
    document.body.appendChild(holder)
    document.body.classList.add('print-answer')
    const done = () => { holder.remove(); document.body.classList.remove('print-answer'); window.removeEventListener('afterprint', done) }
    window.addEventListener('afterprint', done)
    setTimeout(() => window.print(), 50)
  }

  const streaming = msg?.status === 'streaming'
  const chooseLevel = async (lv) => {
    setError(null)
    if (lv === 'standard' || versions[lv]) { setLevel(lv); return }
    setBusy(lv)
    try {
      const { content } = await api.rewriteLevel(debateId, verdict.id, lv)
      setVersions((v) => ({ ...v, [lv]: content }))
      setLevel(lv)
    } catch (e) {
      setError(e.message)
    }
    setBusy(null)
  }

  const text = level === 'standard' ? msg?.content : versions[level]
  const a = parseAnswer(text)
  const chairName = chairHandle || 'The chair'
  // Diagrams are for the Expert view; the other levels stay text-first with a way in
  const diagramCode = a.diagrams[0] || parseAnswer(msg?.content).diagrams[0]
  const diagram = !streaming && !diagramFailed && diagramCode && (level === 'expert' ? (
    <div className="diagram">
      <div className="diagram-h"><span>At a glance</span><span>drawn by {chairName}</span></div>
      <Mermaid code={diagramCode} onFail={onDiagramFail} />
    </div>
  ) : (
    <div className="diagram-hint">
      <button className="linkish" onClick={() => chooseLevel('expert')} disabled={!!busy}>See the diagram in the Expert view →</button>
    </div>
  ))

  if (msg?.status === 'error') {
    return <div className="answer"><div className="writing error">{msg.content}</div></div>
  }

  return (
    <div className={`answer ${busy ? 'rewriting' : ''}`} ref={cardRef}>
      {question && <div className="print-only print-q">{question}</div>}
      <div className="a-head">
        <div className="t">{verdict ? headline(verdict, finalStances, seats.length, factChecked, chairName) : <span>{chairName} is writing the answer…</span>}</div>
        {verdict && <CopyButton text={text} label="Copy answer" />}
        {verdict && msg?.status === 'done' && <ExportMenu debateId={debateId} level={versions[level] || level === 'standard' ? level : 'standard'} onPrint={print} />}
        {verdict && verdict.reason !== 'direct' && (
          <div className="seg" role="group" aria-label="Reading level">
            {LEVELS.map(([k, label]) => (
              <button key={k} className={level === k ? 'on' : ''} disabled={!!busy} onClick={() => chooseLevel(k)}>
                {busy === k ? '…' : label}
              </button>
            ))}
          </div>
        )}
      </div>
      {error && <div className="writing error" style={{ paddingBottom: 0 }}>{error}</div>}
      {!text && streaming ? (
        <div className="writing">
          <div className="shimmer big" style={{ width: '88%' }} />
          <div className="shimmer big" style={{ width: '62%' }} />
          <div className="shimmer" style={{ width: '94%', marginTop: 20 }} />
          <div className="shimmer" style={{ width: '81%' }} />
          <div className="shimmer" style={{ width: '70%' }} />
        </div>
      ) : (
        <div className="answer-text">
          {a.bottom && (
            <div className="bottom-line">
              <ReactMarkdown remarkPlugins={GFM} components={{ p: ({ children }) => <p>{children}</p> }}>{a.bottom}</ReactMarkdown>
            </div>
          )}
          {a.sections.map((sec, i) => (
            <div key={`${i}-${sec.title}`}>
              <div className="a-body"><Markdown>{sec.title ? `### ${sec.title}\n\n${sec.content}` : sec.content}</Markdown></div>
              {/* The diagram sits right after the first section (the key points) */}
              {i === 0 && diagram}
            </div>
          ))}
          {a.sections.length === 0 && diagram}
          {a.dissent && <div className="dissent"><b>Where they differed: </b><Markdown className="inline">{a.dissent}</Markdown></div>}
        </div>
      )}
      {verdict && <EvidenceSummary claims={claims} evidence={msg?.meta?.evidence} />}
      {verdict && <Evidence claims={claims} />}
      {canTrace && <div className="why-hint">Select any part of the answer to see who argued for it.</div>}
      {sel && !why && (
        <button className="why-btn" style={{ left: sel.x, top: sel.y }} onMouseDown={(e) => e.preventDefault()} onClick={traceSelection}>
          Why?
        </button>
      )}
      {why && <WhyPanel state={why} x={why.x} y={why.below} onClose={closeWhy} />}
      {verdict && <Behind metrics={metrics} seats={seats} chairHandle={chairHandle} rounds={verdict.rounds} />}
    </div>
  )
}
