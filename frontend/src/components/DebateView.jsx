import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { RESEARCHER, displayTitle, modelShort } from '../agents'
import { useDebate } from '../useDebate'
import AnswerCard from './AnswerCard'
import { AgentMessage, BeagleCard, ModeratorMessage, Orb, SystemRow, UserMessage } from './Message'

const INTAKE = ['intake', 'clarifying', 'confirming']
const focusComposer = () => window.dispatchEvent(new Event('quorum:focus-composer'))

const STANCE_LABEL = { AGREE: 'agrees', REFINE: 'refining', DISAGREE: 'disagrees' }

function latestStances(messages, topic, seats) {
  return seats.map((seat) => {
    const m = [...messages].reverse().find((x) =>
      x.topic === topic && x.seat_id === seat.id && x.author_kind === 'seat' && x.status === 'done')
    return m?.stance || null
  })
}

export function useAutoGrow(ref, value) {
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
  }, [ref, value])
}

const joinNames = (names) => names.length <= 2 ? names.join(' & ') : `${names.slice(0, -1).join(', ')} & ${names.at(-1)}`

// A round's divider doubles as its recap: how the council stands, who dissents, and a way to fold the round away
function RoundDivider({ round, turns, seatsCount, collapsed, onToggle }) {
  const spoken = turns.filter((m) => m.status === 'done')
  const done = spoken.filter((m) => m.stance)
  const count = (s) => done.filter((m) => m.stance === s).length
  const dissent = done.filter((m) => m.stance === 'DISAGREE').map((m) => m.handle)
  let recap
  if (spoken.length < seatsCount && turns.some((m) => m.status === 'streaming')) recap = `${spoken.length} of ${seatsCount} spoken`
  else if (done.length && count('AGREE') === done.length) recap = `all ${done.length} agree`
  else recap = [count('AGREE') && `${count('AGREE')} agree`, count('REFINE') && `${count('REFINE')} refine`].filter(Boolean).join(' · ')
  return (
    <button className={`round-divider ${collapsed ? 'collapsed' : ''}`} onClick={onToggle} aria-expanded={!collapsed}
      title={collapsed ? 'Show this round' : 'Fold this round away'}>
      <span className="rd-line" />
      <span className="rd-label">
        <b>Round {round}</b>
        <span className="rd-dots" aria-hidden="true">{done.map((m) => <i key={m.id} className={`dot ${m.stance.toLowerCase()}`} />)}</span>
        {recap}{dissent.length > 0 && <>{recap && ' · '}<em>{joinNames(dissent)} {dissent.length === 1 ? 'dissents' : 'dissent'}</em></>}
        <span className="chev" aria-hidden="true">›</span>
      </span>
      <span className="rd-line" />
    </button>
  )
}

function Thread({ items, seatsById, debate, onIntake }) {
  const [collapsed, setCollapsed] = useState(() => new Set())
  const toggleRound = (r) => setCollapsed((prev) => {
    const next = new Set(prev)
    if (next.has(r)) next.delete(r); else next.add(r)
    return next
  })
  const seatsCount = Object.keys(seatsById).length
  const turnsByRound = {}
  for (const m of items) {
    if (m.author_kind !== 'seat') continue
    ;(turnsByRound[m.round] ||= []).push({ ...m, handle: seatsById[m.seat_id]?.handle })
  }
  let lastRound = null
  const out = []
  const lastModerator = [...items].reverse().find((m) => m.author_kind === 'moderator')
  for (const m of items) {
    if (m.kind === 'summary') {
      const s = m.summary
      out.push(
        <div className="sysrow" key={`s${s.id}`}>
          <details><summary>Rounds 1–{s.upto_round} condensed by the chair to save memory</summary><div>{s.content}</div></details>
        </div>,
      )
      continue
    }
    if (m.author_kind === 'seat' && m.round !== lastRound) {
      lastRound = m.round
      out.push(
        <RoundDivider key={`r${m.id}`} round={m.round} turns={turnsByRound[m.round] || []} seatsCount={seatsCount}
          collapsed={collapsed.has(m.round)} onToggle={() => toggleRound(m.round)} />,
      )
    }
    // A folded round hides its turns and the lookups made during it
    if (lastRound !== null && collapsed.has(lastRound) && m.round === lastRound && ['seat', 'researcher'].includes(m.author_kind)) continue
    if (m.author_kind === 'user') out.push(<UserMessage key={m.id} msg={m} />)
    else if (m.author_kind === 'researcher') out.push(<BeagleCard key={m.id} msg={m} model={debate.researcher_model} />)
    else if (m.author_kind === 'system') out.push(<SystemRow key={m.id} msg={m} />)
    else if (m.author_kind === 'moderator') {
      const kind = m.meta?.kind
      const active = m === lastModerator && onIntake &&
        ((kind === 'question' && debate.status === 'clarifying') || (kind === 'summary' && debate.status === 'confirming'))
      out.push(
        <ModeratorMessage key={m.id} msg={m} active={active}
          onAnswer={(text) => onIntake?.answer(text)} onConfirm={() => onIntake?.confirm()} onAddDetails={focusComposer} />,
      )
    }
    else if (m.author_kind === 'seat') {
      const seat = seatsById[m.seat_id]
      out.push(<AgentMessage key={m.id} msg={m} seat={seat} isChair={seat?.handle === debate.chair_handle} />)
    }
  }
  return out
}

function Stage({ state, speakingSeatIds, beagleBusy, searches }) {
  const { debate, seats, messages } = state
  const stances = latestStances(messages, debate.topic, seats)
  const agreeN = stances.filter((s) => s === 'AGREE').length
  const phase = debate.status === 'concluding' ? 'writing the answer'
    : agreeN === seats.length && agreeN > 0 ? 'in agreement'
    : agreeN > 0 ? 'converging' : 'debating'
  return (
    <div className="stage-wrap">
    <div className="stage">
      <div className="seats">
        {seats.map((seat, i) => {
          // In automatic mode the first (largest) member picks the chair before round 1
          const picking = i === 0 && debate.chair_mode === 'auto' && !debate.chair_handle && debate.status === 'running'
          const speaking = speakingSeatIds.has(seat.id) || picking
          const st = stances[i]
          return (
            <div className="seat" key={seat.id} title={`${seat.handle} · ${seat.model}`}>
              <Orb handle={seat.handle} size="lg" speaking={speaking} chair={seat.handle === debate.chair_handle} />
              <b>{seat.handle}</b>
              <small className="mdl">({modelShort(seat.model)})</small>
              <span className="st">
                {picking ? 'choosing chair…' : speaking ? 'speaking…' : st ? <><i className={`dot ${st.toLowerCase()}`} /> {STANCE_LABEL[st]}</> : <><i className="dot idle" /> waiting</>}
              </span>
            </div>
          )
        })}
        {debate.research_enabled && (
          <div className="seat" title={`${RESEARCHER} · web search using ${debate.researcher_model}`}>
            <Orb handle={RESEARCHER} size="lg" speaking={beagleBusy} dim={!beagleBusy} />
            <b>{RESEARCHER}</b>
            <small className="mdl">({modelShort(debate.researcher_model)})</small>
            <span className="st">{beagleBusy ? 'searching…' : `${searches} search${searches === 1 ? '' : 'es'}`}</span>
          </div>
        )}
      </div>
      {debate.round > 0 && (
        <div className="progress">
          <span>Round {debate.round} of {debate.max_rounds} · {phase}</span>
          <div className="bar">{stances.map((s, i) => <i key={i} className={s ? s.toLowerCase() : ''} />)}</div>
        </div>
      )}
    </div>
    </div>
  )
}

// Agents you can @mention from the composer: the council plus Beagle
function mentionables(seats, debate) {
  const list = seats.map((s) => ({ name: s.handle, model: s.model }))
  if (debate.research_enabled) list.push({ name: RESEARCHER, model: debate.researcher_model, hint: 'searches the web' })
  return list
}

// One line of plain words where the user is looking, so the quiet gaps between turns never look frozen
function LiveStatus({ state }) {
  const { debate, seats, messages } = state
  const streaming = messages.filter((m) => m.status === 'streaming')
  const seatName = (id) => seats.find((s) => s.id === id)?.handle
  const writer = streaming.find((m) => m.author_kind === 'seat')
  let text
  if (debate.status === 'concluding') {
    text = streaming.some((m) => m.research_kind === 'factcheck') ? `${RESEARCHER} is fact-checking the answer…`
      : `${debate.chair_handle || 'The chair'} is writing the answer…`
  } else if (debate.status === 'paused') {
    text = `Paused after round ${debate.round}`
  } else if (debate.status === 'running' && debate.round > 0) {
    const spoken = messages.filter((m) => m.topic === debate.topic && m.round === debate.round && m.author_kind === 'seat' && m.status === 'done').length
    const who = writer ? `${seatName(writer.seat_id)} is writing…`
      : streaming.some((m) => m.author_kind === 'researcher') ? `${RESEARCHER} is searching…`
      : 'next speaker is thinking…'
    text = `Round ${debate.round} of ${debate.max_rounds} · ${spoken} of ${seats.length} spoken · ${who}`
  } else if (debate.status === 'running') {
    text = streaming.some((m) => m.author_kind === 'researcher') ? `${RESEARCHER} is researching before round 1…` : 'Getting started…'
  }
  if (!text) return null
  return <span className="live-status" role="status"><i className="live-dot" />{text}</span>
}

function Composer({ debate, seats, onError, state }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [picker, setPicker] = useState(null) // { query, start, index }
  const ref = useRef(null)
  useAutoGrow(ref, text)
  const id = debate.id
  useEffect(() => {
    const focus = () => ref.current?.focus()
    window.addEventListener('quorum:focus-composer', focus)
    return () => window.removeEventListener('quorum:focus-composer', focus)
  }, [])
  const act = async (fn) => {
    onError(null)
    try { await fn() } catch (e) { onError(e.message) }
  }
  const send = async () => {
    if (!text.trim() || busy) return
    setBusy(true)
    await act(async () => { await api.postMessage(id, text.trim()); setText('') })
    setBusy(false)
    ref.current?.focus()
  }
  const options = picker
    ? mentionables(seats, debate).filter((o) => o.name.toLowerCase().startsWith(picker.query.toLowerCase()))
    : []
  const onChange = (e) => {
    const value = e.target.value
    setText(value)
    const caret = e.target.selectionStart
    const m = value.slice(0, caret).match(/(^|\s)@(\w*)$/)
    setPicker(m ? { query: m[2], start: caret - m[2].length - 1, index: 0 } : null)
  }
  const choose = (name) => {
    const end = picker.start + 1 + picker.query.length
    const next = `${text.slice(0, picker.start)}@${name} ${text.slice(end)}`
    setText(next)
    setPicker(null)
    requestAnimationFrame(() => {
      const pos = picker.start + name.length + 2
      ref.current?.focus()
      ref.current?.setSelectionRange(pos, pos)
    })
  }
  const onKeyDown = (e) => {
    if (picker && options.length) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setPicker({ ...picker, index: (picker.index + 1) % options.length }); return }
      if (e.key === 'ArrowUp') { e.preventDefault(); setPicker({ ...picker, index: (picker.index - 1 + options.length) % options.length }); return }
      if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); choose(options[picker.index].name); return }
      if (e.key === 'Escape') { e.preventDefault(); setPicker(null); return }
    }
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send() }
  }

  const answered = debate.status === 'concluded' || debate.status === 'idle'
  const chair = debate.chair_handle || 'the chair'
  const placeholder = debate.status === 'clarifying' ? `Answer ${chair}, or tap a suggestion`
    : debate.status === 'confirming' ? 'Add details, or tap “Looks right, start”'
    : debate.status === 'intake' ? `Add anything else ${chair} should know`
    : answered ? `Ask a follow-up, or @${RESEARCHER} to look something up`
    : `Add a thought, or ask @${RESEARCHER} to look something up`
  return (
    <div className="composer-wrap">
      {picker && options.length > 0 && (
        <div className="mention-menu" role="listbox" aria-label="Mention an agent">
          {options.map((o, i) => (
            <button key={o.name} role="option" aria-selected={i === picker.index} className={i === picker.index ? 'on' : ''}
              onMouseDown={(e) => { e.preventDefault(); choose(o.name) }} onMouseEnter={() => setPicker({ ...picker, index: i })}>
              <Orb handle={o.name} size="sm" />
              <b>{o.name}</b>
              <span>{o.hint ? `${o.hint} · ` : ''}{modelShort(o.model)}</span>
            </button>
          ))}
        </div>
      )}
      <div className="composer">
        <textarea ref={ref} rows={1} value={text} placeholder={placeholder} aria-label="Message"
          onChange={onChange} onKeyDown={onKeyDown} onBlur={() => setTimeout(() => setPicker(null), 150)} />
        <button className="send" style={{ width: 34, height: 34, fontSize: 16 }} disabled={!text.trim() || busy} onClick={send} aria-label="Send">↑</button>
      </div>
      <div className="composer-meta">
        {state && <LiveStatus state={state} />}
        <label className="switch" title="Opening brief, lookups when agents ask, and a fact-check before the answer">
          <input type="checkbox" checked={debate.research_enabled}
            onChange={(e) => act(() => api.updateDebate(id, { research_enabled: e.target.checked }))} />
          Web research
        </label>
        <label className="switch" title="Off: pause after every round so you can steer">
          <input type="checkbox" checked={debate.autopilot}
            onChange={(e) => act(() => api.updateDebate(id, { autopilot: e.target.checked }))} />
          Autopilot
        </label>
      </div>
    </div>
  )
}

function IntakeTyping({ debate }) {
  const chair = debate.chair_handle
  return (
    <div className="msg">
      <Orb handle={chair || 'Chair'} speaking chair={!!chair} />
      <div className="bubble moderator">
        <div className="status-line">
          <span className="typing"><i /><i /><i /></span>
          {chair ? `${chair} is reading your conundrum…` : 'Choosing a chair…'}
        </div>
      </div>
    </div>
  )
}

function TopicBlock({ topic, state, seatsById, current, answerRef, onIntake }) {
  const { debate, messages, verdicts, summaries, metrics } = state
  const [open, setOpen] = useState(false)
  const historyRef = useRef(null)
  const toggle = () => {
    setOpen(!open)
    // The debate opens below the fold; bring its start into view so the click visibly does something
    if (!open) setTimeout(() => historyRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 60)
  }
  const all = messages.filter((m) => m.topic === topic)
  const question = all.find((m) => m.author_kind === 'user' && m.round === 0)
  const chairMsg = [...all].reverse().find((m) => m.author_kind === 'chair')
  const verdict = verdicts.find((v) => v.topic === topic && (!chairMsg || v.message_id === chairMsg.id))
  const items = [
    ...all.filter((m) => m !== question && m.author_kind !== 'chair'),
    ...summaries.filter((s) => s.topic === topic).map((s) => ({ kind: 'summary', summary: s, created_at: s.created_at, id: `s${s.id}` })),
  ].sort((a, b) => (a.created_at < b.created_at ? -1 : a.created_at > b.created_at ? 1 : 0))
  const answered = !!verdict
  const factChecked = all.some((m) => m.research_kind === 'factcheck' && m.status === 'done' && m.sources?.length)
  const counts = {
    rounds: Math.max(0, ...all.filter((m) => m.author_kind === 'seat').map((m) => m.round)),
    messages: all.filter((m) => m.author_kind === 'seat').length,
    lookups: all.filter((m) => m.author_kind === 'researcher').length,
  }
  const answer = chairMsg && (
    <div ref={current ? answerRef : null} style={{ display: 'contents' }}>
      <AnswerCard debateId={debate.id} msg={chairMsg} verdict={verdict} seats={state.seats}
        chairHandle={debate.chair_handle || null} metrics={metrics[topic]}
        finalStances={latestStances(messages, topic, state.seats)} factChecked={factChecked}
        question={question?.content} />
    </div>
  )
  return (
    <>
      {topic > 1 && <div className="divider">Follow-up</div>}
      {question && <UserMessage msg={question} />}
      {answered && verdict.reason === 'direct' ? answer : answered ? (
        <>
          {answer}
          <button className={`how ${open ? 'open' : ''}`} onClick={toggle} aria-expanded={open}>
            <span className="stack">{state.seats.slice(0, 8).map((s) => <Orb key={s.id} handle={s.handle} size="sm" />)}</span>
            <span><b>{open ? 'Hide the debate' : 'See how the council got here'}</b> · {counts.rounds} round{counts.rounds === 1 ? '' : 's'} · {counts.messages} messages{counts.lookups ? ` · ${counts.lookups} lookups` : ''}</span>
            <span className="chev">›</span>
          </button>
          {open && (
            <div className="history-block" ref={historyRef}>
              <Thread items={items} seatsById={seatsById} debate={debate} />
            </div>
          )}
        </>
      ) : (
        <>
          <Thread items={items} seatsById={seatsById} debate={debate} onIntake={current ? onIntake : null} />
          {current && debate.status === 'intake' && <IntakeTyping debate={debate} />}
          {answer}
        </>
      )}
    </>
  )
}

export default function DebateView({ debateId, onChanged, mobileBar }) {
  const state = useDebate(debateId)
  const scrollRef = useRef(null)
  const stickRef = useRef(true)
  const answerRef = useRef(null)
  const [error, setError] = useState(null)
  const seatsById = useMemo(() => Object.fromEntries(state.seats.map((s) => [s.id, s])), [state.seats])

  const status = state.debate?.status
  const title = state.debate?.title
  useEffect(() => { if (status) onChanged?.() }, [status, title, onChanged])

  // Follow the conversation while it's live, unless the reader scrolled up
  useLayoutEffect(() => {
    const el = scrollRef.current
    if (el && stickRef.current && status !== 'concluded') el.scrollTop = el.scrollHeight
  }, [state.messages, status])

  // When an answer lands, bring it into view
  const verdictCount = state.verdicts.length
  const prevVerdicts = useRef(null)
  useEffect(() => {
    if (prevVerdicts.current !== null && verdictCount > prevVerdicts.current) {
      answerRef.current?.firstElementChild?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
    if (state.loaded) prevVerdicts.current = verdictCount
  }, [verdictCount, state.loaded])

  if (!state.loaded) return <>{mobileBar}<div className="empty-state"><div className="typing"><i /><i /><i /></div></div></>
  const { debate, seats, messages } = state
  const topics = [...new Set(messages.map((m) => m.topic))].filter((t) => t > 0)
  const live = ['running', 'paused', 'concluding'].includes(status)
  const speakingSeatIds = new Set(messages.filter((m) => m.status === 'streaming' && m.seat_id).map((m) => m.seat_id))
  const beagleBusy = messages.some((m) => m.status === 'streaming' && m.author_kind === 'researcher')
  const question = messages.find((m) => m.author_kind === 'user' && m.round === 0)?.content || ''
  const searches = state.metrics[debate.topic]?.totals?.searches || 0

  let sub
  if (debate.chair_handle) {
    const by = debate.chair_picked_by && debate.chair_picked_by !== debate.chair_handle ? ` — picked by ${debate.chair_picked_by}` : ''
    sub = <>Chair: {debate.chair_handle}{by}{debate.chair_reason && ` · “${debate.chair_reason}”`}</>
  } else if (debate.chair_mode === 'auto' && ['running', 'intake'].includes(status)) {
    sub = <>Choosing a chair…</>
  } else {
    sub = <>Chair: {debate.chair_model}</>
  }

  const act = async (fn) => {
    setError(null)
    try { await fn() } catch (e) { setError(e.message) }
  }

  return (
    <>
      {mobileBar}
      <div className="topbar">
        <div className="q-title">
          <h2 title={question}>{displayTitle(debate.title, question)}</h2>
          <div className="sub">{sub}{debate.pack && <> · {debate.pack.emoji} {debate.pack.name}</>}</div>
        </div>
        {INTAKE.includes(status) && (
          <div className="actions">
            <button className="btn" onClick={() => act(() => api.confirmIntake(debate.id))}>Skip, just start</button>
          </div>
        )}
        {!['concluded', 'idle', ...INTAKE].includes(status) && (
          <div className="actions">
            {status === 'running' && <button className="btn" onClick={() => act(() => api.stopDebate(debate.id))}>Pause</button>}
            {status === 'paused' && <button className="btn" onClick={() => act(() => api.continueDebate(debate.id))}>Continue</button>}
            {(status === 'concluding' || status === 'researching') && (
              <button className="btn" onClick={() => act(() => api.stopDebate(debate.id))}>Stop</button>
            )}
            {(status === 'running' || status === 'paused') && (
              <button className="btn primary" onClick={() => act(() => api.concludeDebate(debate.id))}>Answer now</button>
            )}
          </div>
        )}
      </div>
      <div className="scroller" ref={scrollRef}
        onScroll={(e) => { const el = e.currentTarget; stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120 }}>
        {live && <Stage state={state} speakingSeatIds={speakingSeatIds} beagleBusy={beagleBusy} searches={searches} />}
        <div className="thread">
          {topics.map((t) => (
            <TopicBlock key={t} topic={t} state={state} seatsById={seatsById} current={t === debate.topic} answerRef={answerRef}
              onIntake={{
                answer: (text) => act(() => api.postMessage(debate.id, text)),
                confirm: () => act(() => api.confirmIntake(debate.id)),
              }} />
          ))}
          {error && <div className="sysrow error">{error}</div>}
        </div>
      </div>
      <Composer debate={debate} seats={seats} onError={setError} state={state} />
    </>
  )
}
