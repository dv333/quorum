import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

// Models write "~$12k" for approximations; only ~~double~~ tildes should strike through
const GFM = [[remarkGfm, { singleTilde: false }]]
import { useState } from 'react'
import { RESEARCHER, agentFor, formatTime, linkMentions, modelShort } from '../agents'

function MdLink({ node, href, children, ...props }) {
  if (href?.startsWith('#agent-')) {
    const name = href.slice(7)
    return <span className="mention"><Orb handle={name} size="xs" />{name}</span>
  }
  return <a href={href} {...props} target="_blank" rel="noreferrer noopener">{children}</a>
}

export function Markdown({ children, className = '' }) {
  return (
    <div className={`md ${className}`}>
      <ReactMarkdown remarkPlugins={GFM} components={{ a: MdLink }}>
        {linkMentions(children)}
      </ReactMarkdown>
    </div>
  )
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    const el = document.createElement('textarea')
    el.value = text
    document.body.appendChild(el)
    el.select()
    document.execCommand('copy')
    el.remove()
  }
}

export function CopyButton({ text, label = 'Copy' }) {
  const [done, setDone] = useState(false)
  if (!text) return null
  return (
    <button className={`copy-btn ${done ? 'done' : ''}`} aria-label={done ? 'Copied' : label} title={done ? 'Copied' : label}
      onClick={async (e) => { e.stopPropagation(); await copyText(text); setDone(true); setTimeout(() => setDone(false), 1400) }}>
      {done ? (
        <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="m4 10.5 4 4 8-9" strokeLinecap="round" strokeLinejoin="round" /></svg>
      ) : (
        <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><rect x="6.5" y="6.5" width="10" height="10" rx="2.5" /><path d="M13.5 4.5v-.5a2 2 0 0 0-2-2h-7a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h.5" /></svg>
      )}
    </button>
  )
}

export function Orb({ handle, size = '', speaking = false, dim = false, chair = false }) {
  const a = agentFor(handle)
  return (
    <div className={`orb ${size} c-${a.color} ${speaking ? 'speaking' : ''} ${dim ? 'dim' : ''}`} aria-hidden="true">
      {a.emoji}
      {chair && <span className="crown" title="Chair">★</span>}
    </div>
  )
}

const STANCE_WORD = { AGREE: 'Agrees', REFINE: 'Refines', DISAGREE: 'Disagrees' }

export function StanceTag({ stance, position, parsed = true }) {
  if (!stance) return null
  return (
    <div className={`stance ${stance.toLowerCase()}`} title={parsed ? undefined : "The model didn't state a stance; treated as refining"}>
      <i className="dot" />
      <span>{STANCE_WORD[stance] || stance}{position ? ` · ${position}` : ''}</span>
    </div>
  )
}

function Typing() {
  return <span className="typing" aria-label="Writing"><i /><i /><i /></span>
}

// While streaming, hide the STANCE/POSITION footer as soon as it starts to appear
function visibleBody(msg) {
  if (msg.status !== 'streaming') return msg.body
  const idx = msg.content.search(/^[\s>*_`#-]*STANCE\s*[:：]/im)
  return idx >= 0 ? msg.content.slice(0, idx) : msg.content
}

function Thinking({ text, label = 'Thinking', live }) {
  if (!text) return null
  return (
    <details className="disclose">
      <summary>{label}{live ? '…' : ''}</summary>
      <pre>{text}</pre>
    </details>
  )
}

export function AgentMessage({ msg, seat, isChair }) {
  const streaming = msg.status === 'streaming'
  const body = visibleBody(msg)
  const meta = [modelShort(seat?.model)]
  if (msg.tok_per_s) meta.push(`${Math.round(msg.tok_per_s)} tok/s`)
  if (streaming) meta.push(body ? 'writing' : msg.thinking ? 'thinking' : 'getting ready')
  if (msg.status === 'stopped') meta.push('stopped')
  return (
    <div className="msg">
      <Orb handle={seat?.handle} speaking={streaming} chair={isChair} />
      <div className="bubble">
        <div className="who">{seat?.handle}<span>{meta.filter(Boolean).join(' · ')}</span><span className="ts">{formatTime(msg.created_at)}</span>{!streaming && <CopyButton text={body} />}</div>
        <Thinking text={msg.thinking} live={streaming && !body} />
        {body ? <Markdown>{body}</Markdown>
          : streaming ? <Typing />
          : msg.status === 'stopped' ? <span className="faint">Stopped before replying</span> : null}
        {msg.status === 'done' && <StanceTag stance={msg.stance} position={msg.position_line} parsed={msg.stance_parsed} />}
      </div>
    </div>
  )
}

export function UserMessage({ msg }) {
  return (
    <div className="msg me">
      <div className="bubble">
        <Markdown>{msg.content}</Markdown>
        <div className="me-ts">{formatTime(msg.created_at)}<CopyButton text={msg.content} /></div>
      </div>
    </div>
  )
}

const FAV_COLORS = ['#5e5ce6', '#30b0c7', '#34c759', '#ff9f0a', '#ff375f', '#a2845e', '#32ade6', '#bf5af2']

function domainOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, '') } catch { return url }
}

export function SourceChips({ sources }) {
  if (!sources?.length) return null
  return (
    <div className="srcs">
      {sources.map((s, i) => {
        const d = domainOf(s.url)
        const color = FAV_COLORS[[...d].reduce((a, c) => a + c.charCodeAt(0), 0) % FAV_COLORS.length]
        return (
          <a key={s.url} className="src" href={s.url} target="_blank" rel="noreferrer noopener" title={s.title}>
            <span className="fav" style={{ background: color }}>{i + 1}</span>
            <span className="d">{d}</span>
          </a>
        )
      })}
    </div>
  )
}

const RESEARCH_KIND = { brief: 'opening brief', request: 'lookup', factcheck: 'fact-check' }

export function BeagleCard({ msg, model }) {
  const streaming = msg.status === 'streaming'
  const lastLog = msg.thinking?.trim().split('\n').pop()
  const meta = [RESEARCH_KIND[msg.research_kind] || 'research', `web · ${modelShort(model)}`]
  if (msg.status === 'stopped') meta.push('stopped')
  return (
    <div className="msg">
      <Orb handle={RESEARCHER} speaking={streaming} />
      <div className="bubble beagle">
        <div className="who">{RESEARCHER}<span>{meta.join(' · ')}</span><span className="ts">{formatTime(msg.created_at)}</span>{!streaming && <CopyButton text={msg.content} />}</div>
        {msg.requested_by && <div className="asked">Asked by {msg.requested_by}: “{msg.research_request}”</div>}
        {msg.status === 'error' ? <span className="error">{msg.content}</span>
          : msg.content ? <Markdown>{msg.content}</Markdown>
          : streaming ? <div className="status-line"><Typing /> {lastLog || 'Sniffing around the web…'}</div>
          : null}
        <SourceChips sources={msg.sources} />
        <Thinking text={msg.thinking} label="Search log" />
      </div>
    </div>
  )
}

export function SystemRow({ msg }) {
  return <div className={`sysrow ${msg.status === 'error' ? 'error' : ''}`}>{msg.content}</div>
}

// The chair's clarifying interview: a question with tap-to-answer chips, or a summary of assumptions
export function ModeratorMessage({ msg, active, onAnswer, onConfirm, onAddDetails }) {
  const meta = msg.meta || {}
  const chair = meta.chair || 'Chair'
  if (meta.kind === 'summary') {
    return (
      <div className="msg">
        <Orb handle={chair} chair />
        <div className="bubble moderator summary">
          <div className="who">{chair}<span>here's what I'll give the council</span><span className="ts">{formatTime(msg.created_at)}</span>
            <CopyButton text={[msg.content, ...(meta.assumptions || []).map((a) => `- ${a}`)].join('\n')} /></div>
          <div className="brief">{msg.content}</div>
          {meta.assumptions?.length > 0 && (
            <>
              <div className="assume-h">Assumptions</div>
              <ul className="assume">{meta.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
            </>
          )}
          {active && (
            <div className="intake-actions">
              <button className="btn blue" onClick={onConfirm}>Looks right, start</button>
              <button className="btn" onClick={onAddDetails}>Add details</button>
            </div>
          )}
        </div>
      </div>
    )
  }
  return (
    <div className="msg">
      <Orb handle={chair} chair />
      <div className="bubble moderator">
        <div className="who">{chair}<span>question {meta.n || 1} of up to {meta.max || 3}</span><span className="ts">{formatTime(msg.created_at)}</span><CopyButton text={msg.content} /></div>
        <div className="md"><p>{msg.content}</p></div>
        {active && meta.options?.length > 0 && (
          <div className="quick-replies">
            {meta.options.map((o) => <button key={o} className="chip" onClick={() => onAnswer(o)}>{o}</button>)}
          </div>
        )}
      </div>
    </div>
  )
}
