import { useEffect, useReducer } from 'react'

// Live debate state: a snapshot on connect, then incremental SSE events.

const empty = { loaded: false, debate: null, seats: [], messages: [], summaries: [], drafts: [], claims: [], verdicts: [], mode: null, metrics: {} }

function upsert(list, item) {
  const i = list.findIndex((x) => x.id === item.id)
  if (i === -1) return [...list, item]
  const next = list.slice()
  next[i] = { ...list[i], ...item }
  return next
}

function reducer(state, event) {
  switch (event.type) {
    case 'reset':
      return empty
    case 'snapshot':
      return { drafts: [], claims: [], ...event.state, loaded: true }
    case 'debate_updated':
      return { ...state, debate: { ...state.debate, ...event.debate } }
    case 'message_created':
    case 'message_updated':
      return { ...state, messages: upsert(state.messages, event.message) }
    case 'message_delta':
      return {
        ...state,
        messages: state.messages.map((m) =>
          m.id === event.id
            ? {
                ...m,
                content: m.content + (event.content || ''),
                body: (m.body || '') + (event.content || ''),
                thinking: m.thinking + (event.thinking || ''),
              }
            : m,
        ),
      }
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
    case 'verdict_created':
      return { ...state, verdicts: [...state.verdicts, event.verdict] }
    default:
      return state
  }
}

export function useDebate(debateId) {
  const [state, dispatch] = useReducer(reducer, empty)

  useEffect(() => {
    if (!debateId) return undefined
    dispatch({ type: 'reset' })
    let source = null
    let retry = null
    let delay = 1000
    let closed = false
    const connect = () => {
      source = new EventSource(`/api/debates/${debateId}/events`)
      source.onopen = () => { delay = 1000 }
      source.onmessage = (e) => dispatch(JSON.parse(e.data))
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
    return () => { closed = true; clearTimeout(retry); source?.close() }
  }, [debateId])

  return state
}
