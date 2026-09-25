import { useEffect, useState } from 'react'
import { api, formatGB } from '../api'
import { RESEARCHER, modelShort } from '../agents'
import { Orb } from './Message'

const CTX = [4096, 8192, 16384, 32768]
const PLAN_TEXT = {
  parallel: ['ok', 'All models stay loaded together'],
  sequential: ['warn', "Won't all fit at once — models take turns loading (slower)"],
  too_big: ['bad', "One model is larger than this machine's usable memory"],
}

// A sheet for everything "type and go" picks automatically. `value` is the full custom setup.
export default function Customize({ config, value, models, research, onChange, onClose, onReset }) {
  const [plan, setPlan] = useState(null)
  const v = value
  const set = (patch) => onChange({ ...v, ...patch })
  const handles = config.handles
  const byKey = Object.fromEntries(models.map((m) => [m.key, m]))
  // Local servers first, then each cloud provider
  const groups = Object.entries(models.reduce((acc, m) => {
    const name = m.local ? `${m.endpoint_name} · on this Mac` : `${m.endpoint_name} · cloud`
    ;(acc[name] = acc[name] || []).push(m)
    return acc
  }, {})).sort(([a], [b]) => (a.includes('cloud') ? 1 : 0) - (b.includes('cloud') ? 1 : 0))

  useEffect(() => {
    const refs = v.seats.map((s) => ({ endpoint_id: s.endpoint_id, model: s.model }))
    if (!refs.length) { setPlan(null); return }
    api.plan(refs, v.numCtx).then(setPlan, () => setPlan(null))
  }, [v.seats, v.numCtx])

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const setSeat = (i, patch) => set({ seats: v.seats.map((s, j) => (j === i ? { ...s, ...patch } : s)) })
  const removeSeat = (i) => {
    const seats = v.seats.filter((_, j) => j !== i)
    const valid = (h) => h === 'auto' || handles.indexOf(h) < seats.length
    set({ seats, chair: valid(v.chair) ? v.chair : 'auto', researcher: valid(v.researcher) ? v.researcher : 'auto' })
  }
  const addSeat = () => {
    const used = new Set(v.seats.map((s) => `${s.endpoint_id}|${s.model}`))
    const next = models.find((m) => !used.has(m.key) && m.fit !== 'too_big') || models[0]
    if (next) set({ seats: [...v.seats, { endpoint_id: next.endpoint_id, model: next.model, thinking: true }] })
  }
  const toggleCriterion = (c) => set({ criteria: v.criteria.includes(c) ? v.criteria.filter((x) => x !== c) : [...v.criteria, c] })
  const members = v.seats.map((_, i) => handles[i])

  return (
    <div className="sheet-wrap" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="sheet" role="dialog" aria-modal="true" aria-label="Customize the council">
        <div className="sheet-head">
          <button className="btn ghost small" onClick={onReset}>Reset to automatic</button>
          <h2>Customize</h2>
          <button className="btn blue small" onClick={onClose}>Done</button>
        </div>
        <div className="sheet-body">
          <div className="group">
            <h3>Council</h3>
            <div className="list">
              {v.seats.map((s, i) => {
                const m = byKey[`${s.endpoint_id}|${s.model}`]
                return (
                  <div className="list-row member-row" key={i}>
                    <Orb handle={handles[i]} />
                    <div className="grow"><b>{handles[i]}</b><div className="sub">{m ? (m.local ? `${formatGB(m.est_bytes)} · ${m.endpoint_name}` : `cloud · ${m.endpoint_name}`) : 'not installed'}</div></div>
                    <select className="input" value={`${s.endpoint_id}|${s.model}`} aria-label={`${handles[i]}'s model`}
                      onChange={(e) => { const mm = byKey[e.target.value]; setSeat(i, { endpoint_id: mm.endpoint_id, model: mm.model, thinking: true }) }}>
                      {!m && <option value={`${s.endpoint_id}|${s.model}`}>{s.model}</option>}
                      {groups.map(([name, ms]) => (
                        <optgroup key={name} label={name}>
                          {ms.map((mm) => (
                            <option key={mm.key} value={mm.key} disabled={mm.fit === 'too_big'}>
                              {modelShort(mm.model)}{mm.fit === 'too_big' ? ' (too big)' : ''}
                            </option>
                          ))}
                        </optgroup>
                      ))}
                    </select>
                    {m?.thinking && (
                      <label className="switch small" title="Let this reasoning model think before answering (slower)">
                        <input type="checkbox" checked={s.thinking} onChange={(e) => setSeat(i, { thinking: e.target.checked })} />
                        Think
                      </label>
                    )}
                    <button className="icon-btn" disabled={v.seats.length <= config.min_seats} onClick={() => removeSeat(i)} aria-label={`Remove ${handles[i]}`}>✕</button>
                  </div>
                )
              })}
              {v.seats.length < config.max_seats && (
                <button className="list-row" style={{ width: '100%', border: 0, background: 'none', color: 'var(--blue)', font: 'inherit' }} onClick={addSeat}>
                  <span style={{ width: 34, textAlign: 'center', fontSize: 20 }}>+</span> Add a member
                </button>
              )}
            </div>
            {plan && (
              <p>
                <span className={`pill ${PLAN_TEXT[plan.mode][0]}`}>{PLAN_TEXT[plan.mode][1]}</span>{' '}
                {formatGB(plan.total_bytes)} of {formatGB(plan.usable_bytes)} usable
              </p>
            )}
          </div>

          <div className="group">
            <h3>Roles</h3>
            <div className="list">
              <div className="list-row">
                <Orb handle="Chair" />
                <div className="grow"><b>Chair</b><div className="sub">Condenses long debates and writes the final answer</div></div>
                <select className="input" value={v.chair} onChange={(e) => set({ chair: e.target.value })}>
                  <option value="auto">Automatic</option>
                  {members.map((h) => <option key={h} value={h}>{h}</option>)}
                </select>
              </div>
              <div className="list-row">
                <Orb handle={RESEARCHER} />
                <div className="grow"><b>{RESEARCHER}</b><div className="sub">Plans web searches and writes cited briefs</div></div>
                <select className="input" value={v.researcher} onChange={(e) => set({ researcher: e.target.value })} disabled={!v.research}>
                  <option value="auto">Automatic</option>
                  {members.map((h) => <option key={h} value={h}>Uses {h}'s model</option>)}
                </select>
              </div>
            </div>
            <p>Automatic: the largest model reads your question, picks who fits best, and decides how many rounds it needs.</p>
          </div>

          <div className="group">
            <h3>Debate</h3>
            <div className="list">
              <div className="list-row">
                <div className="grow"><b>Web research</b><div className="sub">{research?.ready ? `${RESEARCHER} checks facts on the web` : research?.error || 'Firecrawl is not available'}</div></div>
                <label className="switch"><input type="checkbox" checked={v.research} disabled={!research?.ready} onChange={(e) => set({ research: e.target.checked })} /></label>
              </div>
              <div className="list-row">
                <div className="grow"><b>Autopilot</b><div className="sub">Off pauses after every round so you can steer</div></div>
                <label className="switch"><input type="checkbox" checked={v.autopilot} onChange={(e) => set({ autopilot: e.target.checked })} /></label>
              </div>
              <div className="list-row">
                <div className="grow"><b>Memory per model</b><div className="sub">How much of the conversation each model can hold</div></div>
                <select className="input" value={v.numCtx} onChange={(e) => set({ numCtx: Number(e.target.value) })}>
                  {CTX.map((c) => <option key={c} value={c}>{c / 1024}k tokens</option>)}
                </select>
              </div>
            </div>
          </div>

          <div className="group">
            <h3>What matters most</h3>
            <div className="suggestions" style={{ justifyContent: 'flex-start', marginTop: 0 }}>
              {config.default_criteria.map((c) => (
                <button key={c} className={`chip ${v.criteria.includes(c) ? 'on' : ''}`} onClick={() => toggleCriterion(c)}>{c}</button>
              ))}
            </div>
            <textarea className="input" rows={2} style={{ width: '100%', marginTop: 10 }} value={v.rubric}
              placeholder="Anything else? For example: keep it under $2,000" onChange={(e) => set({ rubric: e.target.value })} />
          </div>
        </div>
      </div>
    </div>
  )
}
