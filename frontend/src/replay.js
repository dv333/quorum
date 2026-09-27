import { createContext } from 'react'

// Replays a recorded conundrum (scripts/record_replay.py) without a backend: the finished snapshot is turned back
// into the events a live debate sends, on a clock, so the app shows it as if the council were debating now.

// { recording, control: ref({ speed, skip }), onDone } while a replay is shown; null in the live app
export const ReplayContext = createContext(null)

const TYPE_MS = 2400 // how long one message takes to type out at 1×

function typing(message, at) {
  const words = (message.content || '').split(/(?<=\s)/)
  const pieces = Math.max(1, Math.min(40, Math.ceil(words.length / 6)))
  const per = Math.ceil(words.length / pieces)
  for (let i = 0; i < words.length; i += per) {
    at(TYPE_MS / pieces, { type: 'message_delta', id: message.id, content: words.slice(i, i + per).join('') })
  }
}

// Moves every timestamp by `ms`, so a recording replays as if it were asked just now (the clock starts at 0s)
function shifted(value, ms) {
  if (Array.isArray(value)) return value.map((v) => shifted(v, ms))
  if (!value || typeof value !== 'object') return value
  return Object.fromEntries(Object.entries(value).map(([k, v]) => [
    k, k === 'created_at' && typeof v === 'string' ? new Date(new Date(v).getTime() + ms).toISOString() : shifted(v, ms),
  ]))
}

// The events of a recorded snapshot, each with its time in ms from the start: { start, events: [{ t, event }] }
export function timeline(recording, now = Date.now()) {
  const asked = recording.snapshot.messages.find((m) => m.author_kind === 'user')?.created_at
  const snap = asked ? shifted(recording.snapshot, now - new Date(asked).getTime()) : recording.snapshot
  const verdict = snap.verdicts[snap.verdicts.length - 1]
  const answer = snap.messages.find((m) => m.id === verdict?.message_id)
  const intro = snap.messages.filter((m) => m.round === 0 && m.author_kind !== 'researcher')
  const debate = snap.messages.filter((m) => !intro.includes(m) && m !== answer && m.status === 'done')
  const events = []
  let t = 0
  const at = (dt, event) => { t += dt; events.push({ t, event }) }
  const start = {
    ...snap,
    debate: { ...snap.debate, status: 'running', round: 1 },
    messages: intro,
    summaries: [],
    drafts: [],
    claims: [],
    verdicts: [],
    metrics: {},
  }

  let round = 1
  const closeRound = (r) => {
    for (const d of snap.drafts.filter((x) => x.round === r)) at(200, { type: 'draft_created', draft: d })
    for (const s of snap.summaries.filter((x) => x.upto_round === r)) at(200, { type: 'summary_created', summary: s })
  }
  for (const m of debate) {
    if (m.round > round) {
      closeRound(round)
      round = m.round
      at(500, { type: 'debate_updated', debate: { round } })
    }
    at(600, { type: 'message_created', message: { ...m, content: '', body: '', thinking: '', status: 'streaming' } })
    typing(m, at)
    at(150, { type: 'message_updated', message: m })
  }
  closeRound(round)
  at(700, { type: 'debate_updated', debate: { status: 'concluding' } })
  if (answer) {
    at(900, { type: 'message_created', message: { ...answer, content: '', body: '', thinking: '', status: 'streaming' } })
    typing(answer, at)
    at(150, { type: 'message_updated', message: answer })
  }
  const topics = [...new Set(snap.claims.map((c) => c.topic))]
  for (const topic of topics) at(300, { type: 'claims_created', topic, claims: snap.claims.filter((c) => c.topic === topic) })
  for (const [topic, metrics] of Object.entries(snap.metrics || {})) at(0, { type: 'metrics_updated', topic, metrics })
  for (const v of snap.verdicts) at(300, { type: 'verdict_created', verdict: v })
  at(0, { type: 'debate_updated', debate: { status: snap.debate.status, round: snap.debate.round } })
  return { start, events }
}

// Plays a timeline into dispatch; returns a function that stops it
export function play(recording, dispatch, control, onDone) {
  const { start, events } = timeline(recording)
  dispatch({ type: 'snapshot', state: start })
  let i = 0
  let clock = 0
  let last = performance.now()
  let timer = null
  const step = () => {
    const now = performance.now()
    clock = control.current.skip ? Infinity : clock + (now - last) * (control.current.speed || 1)
    last = now
    while (i < events.length && events[i].t <= clock) dispatch(events[i++].event)
    if (i < events.length) timer = setTimeout(step, 50)
    else onDone?.()
  }
  timer = setTimeout(step, 800)
  return () => clearTimeout(timer)
}
