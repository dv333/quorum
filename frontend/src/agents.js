import { formatElapsed } from './time'

// Visual identity for council members. Models only ever see the names; the emoji and colors are UI-only.

export const AGENTS = {
  Otter: { emoji: '🦦', color: 'otter' },
  Panda: { emoji: '🐼', color: 'panda' },
  Koala: { emoji: '🐨', color: 'koala' },
  Penguin: { emoji: '🐧', color: 'penguin' },
  Hedgehog: { emoji: '🦔', color: 'hedgehog' },
  Bunny: { emoji: '🐰', color: 'bunny' },
  Turtle: { emoji: '🐢', color: 'turtle' },
  Dolphin: { emoji: '🐬', color: 'dolphin' },
  Beagle: { emoji: '🐶', color: 'beagle' },
  Coder: { emoji: '💻', color: 'coder' },
  Chair: { emoji: '★', color: 'chair' },
}

export const RESEARCHER = 'Beagle'
export const CODER = 'Coder'

export function agentFor(handle) {
  // Debates from before the animal roster used "Agent A" style handles
  return AGENTS[handle] || { emoji: (handle || '?').slice(-1), color: 'chair' }
}

export function modelShort(model) {
  if (!model) return ''
  return model.replace(/:latest$/, '')
}

export function formatDuration(ms) {
  return ms == null ? '—' : formatElapsed(Math.round(ms / 1000) * 1000)
}

export function formatTokens(n) {
  if (n == null) return '—'
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
}

export function formatTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const sameYear = d.getFullYear() === new Date().getFullYear()
  const date = d.toLocaleDateString([], { month: 'short', day: 'numeric', ...(sameYear ? {} : { year: 'numeric' }) })
  return `${date} · ${d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`
}

// "@Beagle", "@Otter" … (and the old "@Researcher") inside message text
export const MENTION_NAMES = ['Otter', 'Panda', 'Koala', 'Penguin', 'Hedgehog', 'Bunny', 'Turtle', 'Dolphin', 'Beagle', 'Coder', 'Researcher']
const MENTION_RE = new RegExp(`(^|[\\s(“"'])@(${MENTION_NAMES.join('|')})\\b`, 'g')

export function linkMentions(text) {
  // Turn mentions into links the markdown renderer shows as avatar pills; leave code alone
  return (text || '').split(/(```[\s\S]*?```|`[^`]*`)/).map((part, i) => (i % 2 ? part
    : part.replace(MENTION_RE, (_, pre, name) => `${pre}[@${name === 'Researcher' ? 'Beagle' : name}](#agent-${name === 'Researcher' ? 'Beagle' : name})`)
  )).join('')
}

// Titles from early versions were slugs ("m5-air-local-llms"); show the question instead
export function displayTitle(title, question) {
  if (!title || /^[a-z0-9]+(-[a-z0-9]+)+$/.test(title)) return question || title || 'Untitled'
  return title
}
