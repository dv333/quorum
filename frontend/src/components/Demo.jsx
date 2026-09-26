import { useEffect, useMemo, useRef, useState } from 'react'
import { ReplayContext } from '../replay'
import DebateView from './DebateView'
import ErrorBoundary from './ErrorBoundary'

const SPEEDS = [1, 2, 4]
const base = import.meta.env.BASE_URL

// A recorded conundrum replayed in the real debate view: no backend, models, Docker or API keys.
// Recordings live in public/demos/ (scripts/record_replay.py writes them); #demo/<name> picks one.
export default function Demo({ name }) {
  const [list, setList] = useState(null)
  const [recording, setRecording] = useState(null)
  const [error, setError] = useState(null)
  const [run, setRun] = useState(0)
  const [speed, setSpeed] = useState(2)
  const [done, setDone] = useState(false)
  const control = useRef({ speed: 2, skip: false })

  useEffect(() => {
    fetch(`${base}demos/index.json`).then((r) => r.json()).then(setList).catch(() => setError('No recordings found.'))
  }, [])
  const current = name || list?.[0]?.name
  useEffect(() => {
    if (!current) return
    setRecording(null)
    fetch(`${base}demos/${current}.json`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`No recording named “${current}”.`))))
      .then(setRecording)
      .catch((e) => setError(e.message))
  }, [current])

  // A new object per run restarts the replay (useDebate plays it from the start)
  const replay = useMemo(() => recording && { recording, control, run, onDone: () => setDone(true) }, [recording, run])
  useEffect(() => { control.current.skip = false; setDone(false) }, [replay])

  const changeSpeed = (s) => { setSpeed(s); control.current.speed = s }

  return (
    <div className="app demo-app">
      <div className="ambient" />
      <main className="main">
        <div className="demo-bar" role="region" aria-label="Replay controls">
          <span className="demo-badge">Replay</span>
          <span className="demo-note">
            {recording ? `${recording.label} · recorded ${recording.recorded}. ` : ''}
            Nothing is running: no models, Docker or keys.
          </span>
          {list?.length > 1 && (
            <div className="seg" role="group" aria-label="Recording">
              {list.map((d) => (
                <button key={d.name} className={d.name === current ? 'on' : ''}
                  onClick={() => { window.location.hash = `demo/${d.name}` }}>{d.short}</button>
              ))}
            </div>
          )}
          <div className="seg" role="group" aria-label="Speed">
            {SPEEDS.map((s) => (
              <button key={s} className={speed === s ? 'on' : ''} onClick={() => changeSpeed(s)}>{s}×</button>
            ))}
          </div>
          {done
            ? <button className="btn small" onClick={() => setRun((n) => n + 1)}>Replay</button>
            : <button className="btn small" onClick={() => { control.current.skip = true }}>Skip to answer</button>}
          <a className="btn small primary" href="https://github.com/dv333/quorum#quick-start" target="_blank" rel="noreferrer">
            Run it yourself
          </a>
        </div>
        {error && <div className="empty-state"><p>{error}</p></div>}
        {replay && (
          <ErrorBoundary resetKey={`${current}/${run}`}>
            <ReplayContext.Provider value={replay}>
              <DebateView key={`${current}/${run}`} debateId={recording.snapshot.debate.id} />
            </ReplayContext.Provider>
          </ErrorBoundary>
        )}
      </main>
    </div>
  )
}
