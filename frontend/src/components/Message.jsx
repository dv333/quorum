import { formatElapsed, useNow } from '../time'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

// Models write "~$12k" for approximations; only ~~double~~ tildes should strike through
const GFM = [[remarkGfm, { singleTilde: false }]]
import { createContext, memo, useContext, useDeferredValue, useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { CODER, RESEARCHER, agentFor, formatTime, linkMentions, modelShort } from '../agents'

// Inside an answer, [n] citations render through this (see citeLinks); elsewhere they stay plain text
export const CiteContext = createContext(null)

// "[2]" (not already a link) becomes a link the answer card can render as a citation with a preview
export function citeLinks(text) {
  return typeof text === 'string' ? text.replace(/\[(\d{1,2})\](?!\()/g, '[\\[$1\\]](#cite-$1)') : text
}

export function MdLink({ node, href, children, ...props }) {
  const cite = useContext(CiteContext)
  if (cite && href?.startsWith('#cite-')) return cite(Number(href.slice(6)))
  if (href?.startsWith('#agent-')) {
    const name = href.slice(7)
    return <span className="mention"><Orb handle={name} size="xs" />{name}</span>
  }
  return <a href={href} {...props} target="_blank" rel="noreferrer noopener">{children}</a>
}

const MD_COMPONENTS = { a: MdLink }

// Markdown is parsed again whenever its text changes; while a reply streams in, that work waits behind typing,
// scrolling and clicks instead of blocking them
export const Markdown = memo(function Markdown({ children, className = '' }) {
  const text = useDeferredValue(children)
  return (
    <div className={`md ${className}`}>
      <ReactMarkdown remarkPlugins={GFM} components={MD_COMPONENTS}>
        {linkMentions(text)}
      </ReactMarkdown>
    </div>
  )
})

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
    // A span, since orbs also sit inside paragraphs (an @mention); .orb sets its own display
    <span className={`orb ${size} c-${a.color} ${speaking ? 'speaking' : ''} ${dim ? 'dim' : ''}`} aria-hidden="true">
      {a.emoji}
      {chair && <span className="crown" title="Chair">★</span>}
    </span>
  )
}

// Hover or focus an agent's icon to see who it is: name, role and model. The card is drawn over the page (a portal),
// so the scrolling seat bar can't clip it.
export function AgentTip({ handle, role, model, note, chair = false, focusable = false, children }) {
  const ref = useRef(null)
  const id = useId()
  const [at, setAt] = useState(null)
  const show = () => {
    const r = ref.current?.getBoundingClientRect()
    if (!r) return
    const below = r.bottom + 160 < window.innerHeight
    const x = Math.min(Math.max(r.left + r.width / 2, 140), window.innerWidth - 140)
    setAt({ x, y: below ? r.bottom + 8 : r.top - 8, below })
  }
  const hide = () => setAt(null)
  useEffect(() => {
    if (!at) return undefined
    window.addEventListener('scroll', hide, true)
    return () => window.removeEventListener('scroll', hide, true)
  }, [at])
  return (
    <span className="agent-tip-anchor" ref={ref} tabIndex={focusable ? 0 : undefined} aria-describedby={at ? id : undefined}
      onMouseEnter={show} onMouseLeave={hide} onFocus={show} onBlur={hide}>
      {children}
      {at && createPortal(
        <div id={id} role="tooltip" className={`agent-tip ${at.below ? 'below' : 'above'}`} style={{ left: at.x, top: at.y }}>
          <div className="agent-tip-h">
            <Orb handle={handle} size="sm" />
            <b>{handle}</b>
            {chair && <span className="agent-tip-chair">★ Chair</span>}
          </div>
          {role && <div className="agent-tip-role">{role}</div>}
          {model && <div className="agent-tip-model">{model}</div>}
          {note && <div className="agent-tip-note">{note}</div>}
        </div>,
        document.body,
      )}
    </span>
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

// Long finished turns fold to their first lines so the debate stays scannable; the stance line below stays visible
const FOLD_WORDS = 110
function Folding({ text, fold, children }) {
  const [open, setOpen] = useState(false)
  const long = fold && text.split(/\s+/).length > FOLD_WORDS
  if (!long) return children
  return (
    <>
      <div className={`folding ${open ? 'open' : ''}`}>{children}</div>
      <button className="linkish fold-btn" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? 'Show less' : 'Show more'}
      </button>
    </>
  )
}

// How long this turn took (ticking while it's written), and the agent's total so far
function TurnTime({ msg, total }) {
  const streaming = msg.status === 'streaming'
  const now = useNow(streaming)
  const turn = streaming ? now - new Date(msg.created_at).getTime() : msg.duration_ms
  if (!turn && !total) return null
  const label = [turn ? formatElapsed(turn) : null, total && !streaming && total !== turn ? `${formatElapsed(total)} total` : null]
    .filter(Boolean).join(' · ')
  return <span className="turn-time" title={`This turn${total ? ` · ${seatTotalHint(total)}` : ''}`}>{label}</span>
}

const seatTotalHint = (total) => `${formatElapsed(total)} for this agent across the debate`

export const AgentMessage = memo(function AgentMessage({ msg, seat, isChair, total }) {
  const streaming = msg.status === 'streaming'
  const body = visibleBody(msg)
  const meta = [modelShort(seat?.model)]
  if (msg.tok_per_s) meta.push(`${Math.round(msg.tok_per_s)} tok/s`)
  if (streaming) meta.push(body ? 'writing' : msg.thinking ? 'thinking' : 'getting ready')
  if (msg.status === 'stopped') meta.push('stopped')
  return (
    <div className="msg">
      <AgentTip handle={seat?.handle} role={msg.meta?.role || seat?.role} model={seat?.model} chair={isChair}>
        <Orb handle={seat?.handle} speaking={streaming} chair={isChair} />
      </AgentTip>
      <div className="bubble">
        <div className="who">{seat?.handle}{(msg.meta?.role || seat?.role) && <span className="role-tag">{msg.meta?.role || seat?.role}</span>}<span>{meta.filter(Boolean).join(' · ')}</span><TurnTime msg={msg} total={total} /><span className="ts">{formatTime(msg.created_at)}</span>{!streaming && <CopyButton text={body} />}</div>
        <Thinking text={msg.thinking} live={streaming && !body} />
        {body ? <Folding text={body} fold={!streaming}><Markdown>{body}</Markdown></Folding>
          : streaming ? <Typing />
          : msg.status === 'stopped' ? <span className="faint">Stopped before replying</span> : null}
        {msg.status === 'done' && <StanceTag stance={msg.stance} position={msg.position_line} parsed={msg.stance_parsed} />}
      </div>
    </div>
  )
})

export const UserMessage = memo(function UserMessage({ msg }) {
  return (
    <div className="msg me">
      <div className="bubble">
        <Markdown>{msg.content}</Markdown>
        <div className="me-ts">{formatTime(msg.created_at)}<CopyButton text={msg.content} /></div>
      </div>
    </div>
  )
})

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

export const BeagleCard = memo(function BeagleCard({ msg, model }) {
  const streaming = msg.status === 'streaming'
  const lastLog = msg.thinking?.trim().split('\n').pop()
  const meta = [RESEARCH_KIND[msg.research_kind] || 'research', `web · ${modelShort(model)}`]
  if (msg.status === 'stopped') meta.push('stopped')
  return (
    <div className="msg">
      <AgentTip handle={RESEARCHER} role="Web research" model={model}>
        <Orb handle={RESEARCHER} speaking={streaming} />
      </AgentTip>
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
})

// The Coder: Claude Code or Codex reading the repository read-only, with its file:line citations checked
export const CoderCard = memo(function CoderCard({ msg }) {
  const streaming = msg.status === 'streaming'
  const lastLog = msg.thinking?.trim().split('\n').pop()
  const meta = msg.meta || {}
  const refs = meta.refs || { found: [], missing: [] }
  const info = [msg.research_kind === 'codebrief' ? 'code brief' : 'answer', `${meta.label || 'coding agent'} · read-only`]
  if (msg.status === 'stopped') info.push('stopped')
  return (
    <div className="msg">
      <AgentTip handle={CODER} role="Reads the repository (read-only)" model={meta.label}>
        <Orb handle={CODER} speaking={streaming} />
      </AgentTip>
      <div className="bubble coder">
        <div className="who">{CODER}<span>{info.join(' · ')}</span><span className="ts">{formatTime(msg.created_at)}</span>{!streaming && <CopyButton text={msg.content} />}</div>
        {msg.requested_by && <div className="asked">Asked by {msg.requested_by}: “{msg.research_request}”</div>}
        {msg.status === 'error' ? <span className="error">{msg.content}</span>
          : msg.content ? <Markdown>{msg.content}</Markdown>
          : streaming ? <div className="status-line"><Typing /> {lastLog || 'Reading the code…'}</div>
          : null}
        {(refs.found.length > 0 || refs.missing.length > 0) && (
          <div className="refs-check" title={[...refs.found, ...refs.missing.map((r) => `${r} (not found)`)].join('\n')}>
            {refs.found.length} of {refs.found.length + refs.missing.length} citations checked against the files
          </div>
        )}
      </div>
    </div>
  )
})

export const SystemRow = memo(function SystemRow({ msg }) {
  if (msg.meta?.kind === 'roles') {
    return (
      <div className="roles-row" aria-label="Roles the chair assigned">
        <span className="roles-label">Roles</span>
        {msg.meta.roles.map((r) => (
          <span className="role-chip" key={r.handle} title={r.focus || r.role}>
            <Orb handle={r.handle} size="xs" /><b>{r.handle}</b> {r.role}
          </span>
        ))}
      </div>
    )
  }
  return <div className={`sysrow ${msg.status === 'error' ? 'error' : ''}`}>{msg.content}</div>
})

// The chair's clarifying interview: a question with tap-to-answer chips, or a summary of assumptions
export function ModeratorMessage({ msg, active, onAnswer, onConfirm, onAddDetails }) {
  const meta = msg.meta || {}
  const chair = meta.chair || 'Chair'
  if (meta.kind === 'summary') {
    return (
      <div className="msg">
        <AgentTip handle={chair} role="Chair" chair>
          <Orb handle={chair} chair />
        </AgentTip>
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
      <AgentTip handle={chair} role="Chair" chair>
        <Orb handle={chair} chair />
      </AgentTip>
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
