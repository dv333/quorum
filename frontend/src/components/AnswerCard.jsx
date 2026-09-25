import { useCallback, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

// Models write "~$12k" for approximations; only ~~double~~ tildes should strike through
const GFM = [[remarkGfm, { singleTilde: false }]]
import { api } from '../api'
import { RESEARCHER, agentFor, formatDuration, formatTime, formatTokens, modelShort } from '../agents'
import { CopyButton, Markdown, Orb } from './Message'
import Mermaid from './Mermaid'

const LEVELS = [['simple', 'Simple'], ['standard', 'Standard'], ['expert', 'Expert']]

// Splits the chair's structured answer (BOTTOM LINE + ## sections) into display parts.
// Falls back to plain markdown when the model ignored the format.
export function parseAnswer(text) {
  const out = { bottom: null, sections: [], diagrams: [], dissent: null }
  if (!text) return out
  // Some models write footnote-style citations ([^1]); show them like the rest ([1])
  let rest = text.replace(/\[\^(\d+)\]/g, '[$1]')
  const bl = rest.match(/^\s*[*_#]*\s*BOTTOM LINE\s*[*_]*\s*[:：]\s*(.+)$/im)
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

export default function AnswerCard({ debateId, msg, verdict, seats, chairHandle, metrics, finalStances, factChecked }) {
  const [level, setLevel] = useState('standard')
  const [versions, setVersions] = useState({})
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)
  const [diagramFailed, setDiagramFailed] = useState(false)
  const onDiagramFail = useCallback(() => setDiagramFailed(true), [])

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
    <div className={`answer ${busy ? 'rewriting' : ''}`}>
      <div className="a-head">
        <div className="t">{verdict ? headline(verdict, finalStances, seats.length, factChecked, chairName) : <span>{chairName} is writing the answer…</span>}</div>
        {verdict && <CopyButton text={text} label="Copy answer" />}
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
        <>
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
        </>
      )}
      {verdict && <Behind metrics={metrics} seats={seats} chairHandle={chairHandle} rounds={verdict.rounds} />}
    </div>
  )
}
