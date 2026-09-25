// Thin client for the FastAPI backend (proxied at /api by Vite).

async function request(path, options = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  })
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const data = await res.json()
      detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
    } catch {
      // keep the status text
    }
    throw new Error(detail)
  }
  return res.json()
}

export const api = {
  config: () => request('/config'),
  inventory: (numCtx, refresh = false) => request(`/inventory?num_ctx=${numCtx}${refresh ? '&refresh=true' : ''}`),
  catalog: (numCtx) => request(`/catalog?num_ctx=${numCtx}`),
  autoCouncil: (numCtx = 8192, pack = null) => request(`/auto-council?num_ctx=${numCtx}${pack ? `&pack=${encodeURIComponent(pack)}` : ''}`),
  plan: (models, numCtx) => request('/plan', { method: 'POST', body: { models, num_ctx: numCtx } }),
  packs: () => request('/packs'),

  listDebates: () => request('/debates'),
  getDebate: (id) => request(`/debates/${id}`),
  createDebate: (body) => request('/debates', { method: 'POST', body }),
  deleteDebate: (id) => request(`/debates/${id}`, { method: 'DELETE' }),
  updateDebate: (id, body) => request(`/debates/${id}`, { method: 'PATCH', body }),
  postMessage: (id, content) => request(`/debates/${id}/messages`, { method: 'POST', body: { content } }),
  why: (id, verdictId, passage) => request(`/debates/${id}/verdicts/${verdictId}/why`, { method: 'POST', body: { passage } }),
  rewriteLevel: (id, verdictId, level) => request(`/debates/${id}/verdicts/${verdictId}/level`, { method: 'POST', body: { level } }),
  confirmIntake: (id) => request(`/debates/${id}/intake/confirm`, { method: 'POST' }),
  setupStatus: () => request('/setup/status'),
  startFirecrawl: () => request('/setup/firecrawl', { method: 'POST' }),
  continueDebate: (id) => request(`/debates/${id}/continue`, { method: 'POST' }),
  stopDebate: (id) => request(`/debates/${id}/stop`, { method: 'POST' }),
  concludeDebate: (id) => request(`/debates/${id}/conclude`, { method: 'POST' }),

  settings: () => request('/settings'),
  saveSettings: (body) => request('/settings', { method: 'PUT', body }),
  researchStatus: () => request('/research/status'),
  researchTest: (query) => request('/research/test', { method: 'POST', body: { query } }),

  addEndpoint: (body) => request('/endpoints', { method: 'POST', body }),
  setEndpointEnabled: (id, enabled) => request(`/endpoints/${id}`, { method: 'PATCH', body: { enabled } }),
  updateEndpoint: (id, body) => request(`/endpoints/${id}`, { method: 'PATCH', body }),
  providers: () => request('/providers'),
  systemSeries: () => request('/system/series'),
  deleteEndpoint: (id) => request(`/endpoints/${id}`, { method: 'DELETE' }),

  // Streams Ollama pull progress; onEvent receives each JSON line.
  async pullModel(endpointId, model, onEvent) {
    const res = await fetch('/api/models/pull', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ endpoint_id: endpointId, model }),
    })
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      const lines = buf.split('\n')
      buf = lines.pop()
      for (const line of lines) if (line.trim()) onEvent(JSON.parse(line))
    }
  },
}

// Markdown export of a conundrum: the answer at a reading level, optionally with the whole debate
export function exportUrl(id, { level = 'standard', debate = false, download = false } = {}) {
  return `/api/debates/${id}/export?level=${level}&debate=${debate}&download=${download}`
}

export async function exportMarkdown(id, opts) {
  const res = await fetch(exportUrl(id, opts))
  if (!res.ok) throw new Error(`Export failed (${res.status})`)
  return res.text()
}

export function formatGB(bytes) {
  if (bytes == null) return '?'
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`
}
