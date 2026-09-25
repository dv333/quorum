import { useEffect, useState } from 'react'

// The living answer: the chair's draft after each round, pinned in the stage while the council debates.
// Words that changed since the previous draft are highlighted, and earlier drafts are a click away.

const clean = (line) => line.replace(/\*\*/g, '').replace(/^\s*[-*•]\s+/, '').replace(/^\s*BOTTOM\s*LINE\s*[:：]\s*/i, '').trim()

function parseDraft(text) {
  const lines = (text || '').split('\n').map((l) => l.trim()).filter(Boolean)
  const bottomIdx = lines.findIndex((l) => /BOTTOM\s*LINE/i.test(l))
  const bottom = clean(lines[bottomIdx >= 0 ? bottomIdx : 0] || '')
  const points = lines.filter((l, i) => i !== (bottomIdx >= 0 ? bottomIdx : 0) && /^[-*•]\s+/.test(l)).map(clean)
  return { bottom, points }
}

const words = (s) => s.split(/(\s+)/).filter((w) => w.length)

// Marks the words of `next` that aren't in `prev` (longest common subsequence over words; drafts are short)
export function diffWords(prev, next) {
  const a = words(prev).filter((w) => w.trim())
  const b = words(next)
  const bw = b.map((w, i) => ({ w, i })).filter((x) => x.w.trim())
  const n = a.length, m = bw.length
  const dp = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = norm(a[i]) === norm(bw[j].w) ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  const kept = new Set()
  for (let i = 0, j = 0; i < n && j < m;) {
    if (norm(a[i]) === norm(bw[j].w)) { kept.add(bw[j].i); i++; j++ } else if (dp[i + 1][j] >= dp[i][j + 1]) i++; else j++
  }
  return b.map((w, i) => ({ w, changed: !!w.trim() && !kept.has(i) }))
}
function norm(w) { return w.toLowerCase().replace(/[^\p{L}\p{N}%$]/gu, '') }

function Marked({ prev, text }) {
  if (prev == null) return text
  return diffWords(prev, text).map((t, i) => (t.changed ? <mark key={i}>{t.w}</mark> : <span key={i}>{t.w}</span>))
}

export default function LivingAnswer({ drafts, chair }) {
  const [open, setOpen] = useState(false)
  const [pick, setPick] = useState(null) // index into drafts; null = latest
  const latest = drafts.length - 1
  useEffect(() => { setPick(null) }, [drafts.length]) // a new draft always takes the stage
  if (!drafts.length) return null
  const idx = pick ?? latest
  const draft = drafts[idx]
  const cur = parseDraft(draft.content)
  const prev = idx > 0 ? parseDraft(drafts[idx - 1].content) : null
  const prevAll = prev && [prev.bottom, ...prev.points].join(' ')
  return (
    <div className={`living ${open ? 'open' : ''}`}>
      <button className="living-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="living-label">Draft answer · round {draft.round}</span>
        <span className="living-bl"><Marked prev={prev?.bottom} text={cur.bottom} /></span>
        <span className="chev" aria-hidden="true">›</span>
      </button>
      {open && (
        <div className="living-body">
          {cur.points.length > 0 && (
            <ul>{cur.points.map((p, i) => <li key={i}><Marked prev={prevAll} text={p} /></li>)}</ul>
          )}
          <div className="living-foot">
            {draft.changed && <span className="living-changed">{idx === 0 ? `${chair || 'The chair'}'s first draft` : draft.changed}</span>}
            {drafts.length > 1 && (
              <span className="living-nav" role="group" aria-label="Earlier drafts">
                <button disabled={idx === 0} onClick={() => setPick(idx - 1)} aria-label="Previous draft">‹</button>
                <span>{idx + 1} of {drafts.length}</span>
                <button disabled={idx === latest} onClick={() => setPick(idx + 1)} aria-label="Next draft">›</button>
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
