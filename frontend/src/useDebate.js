import { useContext, useEffect, useReducer } from 'react'
import { ReplayContext, play } from './replay'

// Live debate state: a snapshot on connect, then incremental SSE events.

const empty = { loaded: false, debate: null, seats: [], messages: [], summaries: [], drafts: [], claims: [], attachments: [], verdicts: [], mode: null, metrics: {} }

function upsert(list, item) {
  const i = list.findIndex((x) => x.id === item.id)
  if (i === -1) return [...list, item]
  const next = list.slice()
  next[i] = { ...list[i], ...item }
  return next
}

// Streamed text arrives a few characters at a time; the pieces that arrive within one frame are applied together
function applyDeltas(messages, items) {
  const add = new Map()
  for (const d of items) {
    const a = add.get(d.id) || { content: '', thinking: '' }
    a.content += d.content || ''
    a.thinking += d.thinking || ''
    add.set(d.id, a)
  }
  return messages.map((m) => {
    const a = add.get(m.id)
    return a ? { ...m, content: m.content + a.content, body: (m.body || '') + a.content, thinking: m.thinking + a.thinking } : m
  })
}

export function reducer(state, event) {
  switch (event.type) {
    case 'reset':
      return empty
    case 'snapshot':
      return { drafts: [], claims: [], attachments: [], ...event.state, loaded: true }
    case 'debate_updated':
      return { ...state, debate: { ...state.debate, ...event.debate } }
    case 'message_created':
    case 'message_updated':
      return { ...state, messages: upsert(state.messages, event.message) }
    case 'message_delta':
      return { ...state, messages: applyDeltas(state.messages, [event]) }
    case 'deltas':
      return { ...state, messages: applyDeltas(state.messages, event.items) }
    case 'claims_created':
      return { ...state, claims: [...state.claims.filter((c) => c.topic !== event.topic), ...event.claims] }
    case 'seats_updated':
      return { ...state, seats: event.seats }
    case 'draft_created':
      return { ...state, drafts: [...state.drafts, event.draft] }
    case 'summary_created':
      return { ...state, summaries: [...state.summaries, event.summary] }
    case 'metrics_updated':
      return { ...state, metrics: { ...state.metrics, [event.topic]: event.metrics } }
    case 'attachments_updated':
      return { ...state, attachments: [...(state.attachments || []).filter((a) => a.topic !== event.topic), ...event.attachments] }
    case 'verdict_created':
      return { ...state, verdicts: [...state.verdicts, event.verdict] }
    default:
      return state
  }
}

export function useDebate(debateId) {
  const [state, dispatch] = useReducer(reducer, empty)
  const replay = useContext(ReplayContext)

  // A recorded conundrum plays back on a clock instead of connecting to the backend
  useEffect(() => {
    if (!replay) return undefined
    dispatch({ type: 'reset' })
    return play(replay.recording, dispatch, replay.control, replay.onDone)
  }, [replay])

  useEffect(() => {
    if (!debateId || replay) return undefined
    dispatch({ type: 'reset' })
    let source = null
    let retry = null
    let delay = 1000
    let closed = false
    // Text deltas wait for the next frame and are applied together; any other event applies them first, in order
    let pending = []
    let frame = null
    const flush = () => {
      frame = null
      if (pending.length) dispatch({ type: 'deltas', items: pending })
      pending = []
    }
    const receive = (event) => {
      if (event.type === 'message_delta') {
        pending.push(event)
        if (frame === null) frame = requestAnimationFrame(flush)
        return
      }
      if (frame !== null) cancelAnimationFrame(frame)
      flush()
      dispatch(event)
    }
    const connect = () => {
      source = new EventSource(`/api/debates/${debateId}/events`)
      source.onopen = () => { delay = 1000 }
      source.onmessage = (e) => receive(JSON.parse(e.data))
      // EventSource retries network drops by itself, but gives up for good on an HTTP error
      // (for example while the backend restarts behind the dev proxy). Reconnect ourselves;
      // the server sends a fresh snapshot on every connection.
      source.onerror = () => {
        if (source.readyState === EventSource.CLOSED && !closed) {
          retry = setTimeout(connect, delay)
          delay = Math.min(delay * 2, 10000)
        }
      }
    }
    connect()
    return () => { closed = true; clearTimeout(retry); if (frame !== null) cancelAnimationFrame(frame); source?.close() }
  }, [debateId, replay])

  return state
}
