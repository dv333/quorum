import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { requestNotifications } from '../attention'
import { RESEARCHER, modelShort } from '../agents'
import Customize from './Customize'
import { useAutoGrow } from './DebateView'
import { Orb } from './Message'

const PACKS_SHOWN = 4 // the rest sit behind "More"

function defaultsFrom(config, auto, research) {
  return {
    seats: auto.seats.map((s) => ({ endpoint_id: s.endpoint_id, model: s.model, thinking: true })),
    chair: 'auto',
    researcher: 'auto',
    research: !!research?.ready,
    autopilot: config.autopilot,
    numCtx: config.num_ctx,
    criteria: ['Accuracy', 'Practicality'],
    rubric: '',
  }
}

export default function Home({ config, onCreated, onOpenSettings, mobileBar }) {
  const [auto, setAuto] = useState(null)
  const [models, setModels] = useState([])
  const [question, setQuestion] = useState('')
  const [custom, setCustom] = useState(null) // null = fully automatic
  const [editing, setEditing] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [packs, setPacks] = useState([])
  const [packId, setPackId] = useState(null)
  const [allPacks, setAllPacks] = useState(false)
  const ref = useRef(null)
  useAutoGrow(ref, question)

  useEffect(() => {
    api.autoCouncil(config.num_ctx).then(setAuto, (e) => setError(e.message))
    api.inventory(config.num_ctx).then((inv) => setModels(inv.models.filter((m) => m.chat)), () => {})
  }, [config.num_ctx])
  useEffect(() => { api.packs().then(setPacks, () => {}) }, [])

  const pack = packs.find((p) => p.id === packId)
  const choosePack = (p) => {
    const starters = packs.map((x) => x.prompt).filter(Boolean)
    const untouched = !question.trim() || starters.includes(question)
    if (p.id === packId) {
      setPackId(null)
      if (untouched) setQuestion('')
    } else {
      setPackId(p.id)
      if (untouched) setQuestion(p.prompt || '')
    }
    ref.current?.focus()
  }
  const shownPacks = allPacks ? packs : packs.filter((p, i) => i < PACKS_SHOWN || p.id === packId)

  const research = auto?.research
  const setup = custom || (auto ? defaultsFrom(config, auto, research) : null)
  const handles = config.handles

  const start = async () => {
    const q = question.trim()
    if (!q || busy) return
    setBusy(true)
    setError(null)
    requestNotifications() // ask once, during a click, so the chair can reach you later
    try {
      let body = { question: q, pack: packId }
      if (custom) {
        const seatRef = (h) => { const s = custom.seats[handles.indexOf(h)]; return s && { endpoint_id: s.endpoint_id, model: s.model } }
        body = {
          question: q,
          seats: custom.seats,
          chair: custom.chair === 'auto' ? null : seatRef(custom.chair),
          researcher: custom.researcher === 'auto' ? null : seatRef(custom.researcher),
          research_enabled: custom.research,
          autopilot: custom.autopilot,
          num_ctx: custom.numCtx,
          criteria: custom.criteria,
          custom_rubric: custom.rubric,
          pack: packId,
        }
      }
      const snap = await api.createDebate(body)
      onCreated(snap.debate.id)
    } catch (e) {
      setError(e.message)
      setBusy(false)
    }
  }

  if (auto && auto.seats.length === 0) {
    return (
      <>
        {mobileBar}
        <div className="empty-state">
          <div className="mark big alive" style={{ margin: '0 auto' }}><span /><span /><span /></div>
          <h2>Add a model to begin</h2>
          <p>Quorum runs entirely on your machine. Install a few models with Ollama, for example:</p>
          <p><code>ollama pull qwen3:8b</code> <code>ollama pull gemma3:12b</code></p>
          <button className="btn blue" onClick={onOpenSettings}>Browse suggested models</button>
        </div>
      </>
    )
  }

  const seatsShown = setup?.seats || []
  const beagleModel = setup?.researcher && setup.researcher !== 'auto'
    ? setup.seats[handles.indexOf(setup.researcher)]?.model
    : auto?.researcher?.model
  const chairText = !setup ? '' : setup.chair === 'auto'
    ? `${handles[0]} will choose the chair`
    : `${setup.chair} chairs`
  return (
    <>
      {mobileBar}
      <div className="home">
        <div className="home-hero">
          <div className="mark big alive"><span /><span /><span /></div>
          <h1>{config.app_name}</h1>
          <div className="tagline">Many minds. One answer.</div>
          <div className="ask">
            <textarea ref={ref} rows={1} autoFocus value={question} placeholder="What's your conundrum?" aria-label="Your question"
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); start() } }} />
            <button className="send" disabled={!question.trim() || busy || !setup} onClick={start} aria-label="Ask">
              {busy ? '…' : '↑'}
            </button>
          </div>
          <div className="council-row">
            {seatsShown.map((s, i) => (
              <div className="member" key={i} style={{ animationDelay: `${i * 60}ms` }}>
                <Orb handle={handles[i]} size="sm" /><b>{handles[i]}</b> {modelShort(s.model)}
              </div>
            ))}
            {setup?.research && (
              <div className="member" style={{ animationDelay: `${seatsShown.length * 60}ms` }} title="Searches the web for the council">
                <Orb handle={RESEARCHER} size="sm" /><b>{RESEARCHER}</b> {modelShort(beagleModel)} · web
              </div>
            )}
          </div>
          {setup && (
            <div className="hint">
              {custom ? 'Your council' : `Every model you have joins (${seatsShown.length} agents)`} · {chairText} ·{' '}
              <button className="linkish" onClick={() => setEditing(setup)}>Customize</button>
              {custom && <> · <button className="linkish" onClick={() => setCustom(null)}>Reset</button></>}
            </div>
          )}
          {error && <p className="error">{error}</p>}
          <div className="suggestions" role="group" aria-label="Topic packs">
            {shownPacks.map((p) => (
              <button key={p.id} className={`chip ${p.id === packId ? 'on' : ''}`} aria-pressed={p.id === packId}
                title={p.description} onClick={() => choosePack(p)}>
                {p.emoji && <span className="chip-emoji" aria-hidden="true">{p.emoji}</span>}{p.name}
              </button>
            ))}
            {packs.length > PACKS_SHOWN && (
              <button className="chip ghost" onClick={() => setAllPacks(!allPacks)}>{allPacks ? 'Less' : 'More'}</button>
            )}
          </div>
          {pack && <div className="pack-note">{pack.description}</div>}
        </div>
      </div>
      {editing && (
        <Customize config={config} value={editing} models={models} research={research}
          onChange={setEditing}
          onClose={() => {
            const unchanged = JSON.stringify(editing) === JSON.stringify(defaultsFrom(config, auto, research))
            setCustom(unchanged ? null : editing)
            setEditing(null)
          }}
          onReset={() => { setCustom(null); setEditing(null) }} />
      )}
    </>
  )
}
