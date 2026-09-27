import { useLayoutEffect, useRef, useState } from 'react'
import { CODER, RESEARCHER, agentFor, modelShort } from '../agents'
import { formatElapsed, useNow } from '../time'
import { AgentTip, Orb } from './Message'

// The council around a table, seen from above: the chair at the head, the speaker lit, each agent's stance on their
// seat, and Beagle and the Coder at a research desk beside it. Collapsed (scrolled into the debate, a narrow window,
// or by choice) the same people slide into one line, so the stage never takes much of the screen while you read.

const STANCE = {
  AGREE: { label: 'agrees', icon: 'M3.5 8.4 6.6 11.2 12.5 4.8' },
  REFINE: { label: 'refines', icon: 'M3.5 9c1.4-2 2.8-2 4.2 0s2.8 2 4.3 0' },
  DISAGREE: { label: 'disagrees', icon: 'M4.5 4.5l7 7m0-7-7 7' },
}
const ORB = 48 // px, the orb at the table; it shrinks to 30 in the line
const LINE_H = 44
const DESK_W = 212

function StanceBadge({ stance }) {
  const s = STANCE[stance]
  if (!s) return null
  return (
    <span className={`rt-badge ${stance.toLowerCase()}`} aria-hidden="true">
      <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
        <path d={s.icon} />
      </svg>
    </span>
  )
}

// Where everyone sits: around an ellipse at the table (chair at the head), or left to right in the line
function layout(width, count, helpers, collapsed, narrow) {
  if (collapsed) {
    // One line; on a phone it squeezes to fit and the clock and tally drop to a second row
    const slots = count + helpers.length + (helpers.length ? 0.35 : 0)
    const step = narrow ? Math.min(42, (width - 8) / slots) : 42
    const at = (i) => ({ x: 15 + i * step, y: LINE_H / 2, scale: 30 / ORB })
    const rowEnd = slots * step
    return {
      height: narrow ? LINE_H + 36 : LINE_H,
      seats: Array.from({ length: count }, (_, i) => at(i)),
      helpers: helpers.map((_, i) => at(count + i + 0.35)),
      barLeft: narrow ? null : rowEnd + 14, // the clock and round sit in the space after the icons
    }
  }
  const desk = helpers.length ? DESK_W + 20 : 0
  const tRx = Math.max(130, Math.min((width - desk) / 2 - 120, 205))
  const tRy = 58
  const sRx = tRx + 64
  const sRy = tRy + 44
  // The table and the desk sit together in the middle, not at opposite edges of a wide window
  const group = 2 * sRx + 110
  const left = Math.max(0, (width - desk - group) / 2)
  const area = left + group
  const cx = left + group / 2
  const cy = 38 + ORB / 2 + sRy - 6 // room above the head seat for its name and role
  const height = cy + sRy + ORB / 2 + 40
  const seats = Array.from({ length: count }, (_, i) => {
    const a = (-90 + (i * 360) / count) * (Math.PI / 180)
    return { x: cx + sRx * Math.cos(a), y: cy + sRy * Math.sin(a), scale: 1, angle: a }
  })
  const helperAt = (i) => ({ x: area + 20 + 34, y: cy - (helpers.length - 1) * 34 + i * 68 + 4, scale: 0.84 })
  return { height, cx, cy, tRx, tRy, seats, helpers: helpers.map((_, i) => helperAt(i)), area }
}

function TableClock({ since, speed }) {
  const now = useNow(!!since)
  if (!since) return null
  const total = formatElapsed(now - new Date(since).getTime())
  return <span role="timer" aria-label={`${total} since you asked`}>{total}{speed ? ` · avg ${speed} t/s` : ''}</span>
}

export default function RoundTable({
  seats, debate, stances, speakingSeatIds, beagleBusy, coderBusy, searches, coderAnswers, since, speed,
  collapsed, onToggle, roundPill, draft, clock,
}) {
  const ref = useRef(null)
  const [width, setWidth] = useState(900)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return undefined
    const ro = new ResizeObserver(([e]) => setWidth(e.contentRect.width))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const narrow = width < 640
  const folded = collapsed || narrow
  const helpers = [
    debate.research_enabled && {
      handle: RESEARCHER, role: 'Web research', model: debate.researcher_model, busy: beagleBusy,
      idle: `${searches} search${searches === 1 ? '' : 'es'}`, doing: 'Searching the web…', kind: 'web',
    },
    debate.repo_path && {
      handle: CODER, role: 'Reads your repository (read-only)', model: '', busy: coderBusy,
      idle: `${coderAnswers} answer${coderAnswers === 1 ? '' : 's'}`, doing: 'Reading the code…', kind: 'code',
    },
  ].filter(Boolean)
  // The chair sits at the head of the table
  const order = [...seats.keys()].sort((a, b) => (seats[b].handle === debate.chair_handle) - (seats[a].handle === debate.chair_handle))
  const L = layout(width, seats.length, helpers, folded, narrow)
  const speakerIdx = order.findIndex((i) => speakingSeatIds.has(seats[i].id))
  const speaker = speakerIdx >= 0 ? L.seats[speakerIdx] : null
  const picking = debate.chair_mode === 'auto' && !debate.chair_handle && debate.status === 'running'

  return (
    <div ref={ref} className={`rt ${folded ? 'folded' : ''} ${narrow ? 'narrow' : ''}`} style={{ height: L.height }}>
      {!folded && (
        <>
          <div className="rt-table" style={{ left: L.cx - L.tRx, top: L.cy - L.tRy, width: L.tRx * 2, height: L.tRy * 2 }}>
            {speaker && (
              <i className={`rt-spot c-${agentColor(seats[order[speakerIdx]].handle)}`} style={{
                left: `${50 + 42 * Math.cos(speaker.angle)}%`, top: `${50 + 42 * Math.sin(speaker.angle)}%`,
              }} />
            )}
            <div className="rt-center">
              {debate.round > 0 ? <b>Round {debate.round} of {debate.max_rounds}</b> : <b>{picking ? 'Choosing a chair…' : 'Getting seated…'}</b>}
              <span className="rt-dots" aria-hidden="true">
                {order.map((i) => <i key={i} className={`dot ${stances[i] ? stances[i].toLowerCase() : 'idle'}`} />)}
              </span>
              <small><TableClock since={since} speed={speed} /></small>
            </div>
          </div>
          {helpers.length > 0 && (() => {
            const top = L.cy - (helpers.length * 68) / 2 - 24
            const busy = helpers.find((h) => h.busy)
            return (
              <div className={`rt-desk ${busy ? `on c-${agentColor(busy.handle)}` : ''}`}
                style={{ left: L.area + 20, top, width: DESK_W, height: helpers.length * 68 + 40 }}>
                <span className="rt-desk-title">
                  <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M3 14h10M8 14V9M5.5 3.5 8 9l4-2.5L9.5 1z" />
                  </svg>
                  Research desk
                </span>
                {helpers.map((h, k) => (
                  <span key={h.handle} className={`rt-station c-${agentColor(h.handle)} ${h.busy ? 'on' : ''}`}
                    style={{ top: L.helpers[k].y - top - 28 }} />
                ))}
              </div>
            )
          })()}
        </>
      )}

      {order.map((i, k) => {
        const seat = seats[i]
        const p = L.seats[k]
        const speaking = speakingSeatIds.has(seat.id) || (picking && i === 0)
        const st = stances[i]
        const chair = seat.handle === debate.chair_handle
        const above = !folded && Math.sin(p.angle) < -0.35 // labels go outside the table
        const status = picking && i === 0 ? 'choosing the chair' : speaking ? 'speaking' : st ? STANCE[st]?.label : 'waiting'
        return (
          <div key={seat.id} className={`rt-seat ${speaking ? 'on' : ''} ${above ? 'above' : ''}`}
            style={{ transform: `translate(${p.x}px, ${p.y}px) translate(-50%, -50%) scale(${p.scale})` }}
            aria-label={`${seat.handle}${seat.role ? `, ${seat.role}` : ''} · ${modelShort(seat.model)} · ${status}`}>
            <AgentTip handle={seat.handle} role={seat.role} model={seat.model} note={status} chair={chair} focusable>
              <span className="rt-orb">
                <Orb handle={seat.handle} size="lg" speaking={speaking} chair={chair} />
                {!speaking && <StanceBadge stance={st} />}
              </span>
            </AgentTip>
            <span className="rt-label">
              <b>{seat.handle}</b>
              {seat.role && <small title={seat.role_focus || seat.role}>{seat.role}</small>}
            </span>
          </div>
        )
      })}

      {speaker && !folded && (() => {
        const bx = speaker.x + (L.cx - speaker.x) * 0.36
        const by = speaker.y + (L.cy - speaker.y) * 0.36
        return (
          <span className={`rt-bubble c-${agentColor(seats[order[speakerIdx]].handle)}`} style={{ left: bx, top: by }} aria-hidden="true">
            <i /><i /><i />
          </span>
        )
      })()}

      {helpers.map((h, k) => {
        const p = L.helpers[k]
        return (
          <div key={h.handle} className={`rt-seat helper ${h.busy ? 'on' : ''}`}
            style={{ transform: `translate(${p.x}px, ${p.y}px) translate(-50%, -50%) scale(${p.scale})` }}>
            <AgentTip handle={h.handle} role={h.role} model={h.model} note={h.busy ? h.doing : h.idle} focusable>
              <span className="rt-orb"><Orb handle={h.handle} size="lg" speaking={h.busy} /></span>
            </AgentTip>
          </div>
        )
      })}
      {!folded && helpers.map((h, k) => {
        const p = L.helpers[k]
        return (
          <div key={`${h.handle}-info`} className={`rt-helper-info ${h.busy ? 'on' : ''} ${h.kind}`} style={{ left: p.x + 30, top: p.y }}>
            <b>{h.handle}</b>
            <small>{h.busy ? h.doing : h.model ? `${h.idle} · ${modelShort(h.model)}` : h.idle}</small>
            {h.busy && <span className="rt-work" aria-hidden="true"><i /></span>}
          </div>
        )
      })}

      {folded && !narrow ? (
        <div className="rt-line-bar" style={{ left: L.barLeft }}>{clock}<span className="rt-line-gap" />{roundPill}{draft}</div>
      ) : (
        <>
          <div className="rt-line-info">{roundPill}{draft}</div>
          {folded && <div className="rt-line-clock">{clock}</div>}
        </>
      )}
      {!narrow && (
        <button className="icon-btn rt-toggle" onClick={onToggle} aria-expanded={!folded}
          aria-label={folded ? 'Show the table' : 'Fold into a line'} title={folded ? 'Show the table' : 'Fold into a line'}>
          <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            <path d={folded ? 'm5 8 5 5 5-5' : 'm5 12 5-5 5 5'} strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      )}
    </div>
  )
}
const agentColor = (handle) => agentFor(handle).color
