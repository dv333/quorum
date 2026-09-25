import { useEffect, useRef, useState } from 'react'
import { formatGB } from '../api'
import { modelShort } from '../agents'

// Live Memory / CPU / GPU: tiny sparkline cards in the sidebar, a larger chart sheet on click.

const METRICS = [
  { key: 'mem', label: 'Memory', color: 'var(--koala)' },
  { key: 'cpu', label: 'CPU', color: 'var(--panda)' },
  { key: 'gpu', label: 'GPU', color: 'var(--otter)' },
]

function pct(sample, key) {
  if (!sample) return null
  if (key === 'mem') return (100 * sample.mem_used) / sample.mem_total
  if (key === 'models') return (100 * sample.models_mem) / sample.mem_total
  return sample[key]
}

function current(sample, key) {
  if (!sample) return '—'
  if (key === 'mem') return `${Math.round((100 * sample.mem_used) / sample.mem_total)}%`
  const v = sample[key]
  return v == null ? 'n/a' : `${Math.round(v)}%`
}

function linePath(values, w, h) {
  const n = values.length
  if (n < 2) return ''
  return values.map((v, i) => {
    const x = (i / (n - 1)) * w
    const y = h - (Math.max(0, Math.min(100, v ?? 0)) / 100) * h
    return `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
}

function Sparkline({ values, color }) {
  const w = 100, h = 26
  const d = linePath(values, w, h)
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="spark" aria-hidden="true">
      {d && <path d={`${d} L${w},${h} L0,${h} Z`} fill={color} opacity="0.16" />}
      {d && <path d={d} fill="none" stroke={color} strokeWidth="1.6" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />}
    </svg>
  )
}

export function ResourceCards({ series, onOpen }) {
  const samples = series?.samples || []
  const last = samples[samples.length - 1]
  const hasGpu = samples.some((s) => s.gpu != null)
  return (
    <div className="res-cards">
      {METRICS.filter((m) => m.key !== 'gpu' || hasGpu).map((m) => (
        <button key={m.key} className="res-card" onClick={() => onOpen(m.key)} title={`${m.label} over the last few minutes`}>
          <div className="res-top"><span>{m.label}</span><b>{current(last, m.key)}</b></div>
          <Sparkline values={samples.slice(-60).map((s) => pct(s, m.key))} color={m.color} />
        </button>
      ))}
    </div>
  )
}

function BigChart({ samples, metric, overlay, interval }) {
  const ref = useRef(null)
  const [hover, setHover] = useState(null)
  const w = 600, h = 150
  const values = samples.map((s) => pct(s, metric.key))
  const over = overlay ? samples.map((s) => pct(s, overlay.key)) : null
  const d = linePath(values, w, h)
  const od = over ? linePath(over, w, h) : ''
  const span = Math.round(((samples.length - 1) * interval) / 60)
  const onMove = (e) => {
    const r = ref.current.getBoundingClientRect()
    const i = Math.round(((e.clientX - r.left) / r.width) * (samples.length - 1))
    setHover(Math.max(0, Math.min(samples.length - 1, i)))
  }
  const hv = hover != null ? samples[hover] : null
  return (
    <div className="bigchart">
      <div className="bigchart-h">
        <span className="dotkey" style={{ background: metric.color }} />{metric.label}
        {overlay && <><span className="dotkey" style={{ background: overlay.color, marginLeft: 12 }} />{overlay.label}</>}
        <span className="val">
          {hv ? `${Math.round(pct(hv, metric.key) ?? 0)}% · ${Math.round(((samples.length - 1 - hover) * interval))}s ago`
            : current(samples[samples.length - 1], metric.key)}
        </span>
      </div>
      <svg ref={ref} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {[25, 50, 75].map((g) => <line key={g} x1="0" x2={w} y1={h - (g / 100) * h} y2={h - (g / 100) * h} className="grid" />)}
        {d && <path d={`${d} L${w},${h} L0,${h} Z`} fill={metric.color} opacity="0.14" />}
        {d && <path d={d} fill="none" stroke={metric.color} strokeWidth="2" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />}
        {od && <path d={od} fill="none" stroke={overlay.color} strokeWidth="2" strokeDasharray="4 3" vectorEffect="non-scaling-stroke" />}
        {hover != null && <line x1={(hover / Math.max(1, samples.length - 1)) * w} x2={(hover / Math.max(1, samples.length - 1)) * w} y1="0" y2={h} className="cursor" />}
      </svg>
      <div className="bigchart-x"><span>{span ? `${span} min ago` : ''}</span><span>50%</span><span>now</span></div>
    </div>
  )
}

export function ResourceSheet({ series, focus, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  const samples = series?.samples || []
  const last = samples[samples.length - 1]
  const sys = series?.system
  const hasGpu = samples.some((s) => s.gpu != null)
  const order = [focus, ...METRICS.map((m) => m.key).filter((k) => k !== focus)]
  return (
    <div className="sheet-wrap" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="sheet wide" role="dialog" aria-modal="true" aria-label="Activity">
        <div className="sheet-head">
          <span className="small muted">{sys?.label}</span>
          <h2>Activity</h2>
          <button className="btn blue small" onClick={onClose}>Done</button>
        </div>
        <div className="sheet-body">
          <div className="stats" style={{ gridTemplateColumns: `repeat(${hasGpu ? 4 : 3}, 1fr)` }}>
            <div className="stat"><div>{last ? formatGB(last.mem_used) : '—'}</div><small>memory used of {last ? formatGB(last.mem_total) : '—'}</small></div>
            <div className="stat"><div>{last ? formatGB(last.models_mem) : '—'}</div><small>held by models</small></div>
            <div className="stat"><div>{current(last, 'cpu')}</div><small>CPU · {sys?.cpu_count} cores</small></div>
            {hasGpu && <div className="stat"><div>{current(last, 'gpu')}</div><small>GPU{last?.gpu_mem ? ` · ${formatGB(last.gpu_mem)} in use` : ''}</small></div>}
          </div>
          {order.filter((k) => k !== 'gpu' || hasGpu).map((k) => {
            const metric = METRICS.find((m) => m.key === k)
            return (
              <BigChart key={k} samples={samples} metric={metric} interval={series.interval_s}
                overlay={k === 'mem' ? { key: 'models', label: 'Models', color: 'var(--panda)' } : null} />
            )
          })}
          <div className="group">
            <h3>Models in memory</h3>
            <div className="list">
              {(series?.loaded || []).map((m) => (
                <div className="list-row" key={`${m.endpoint}-${m.model}`}>
                  <div className="grow"><b>{modelShort(m.model)}</b><div className="sub">{m.endpoint}</div></div>
                  <span className="small muted">{formatGB(m.size_bytes)}</span>
                  {m.size_bytes ? <span className="pill ok">{Math.round((100 * (m.vram_bytes || 0)) / m.size_bytes)}% on GPU</span> : null}
                </div>
              ))}
              {!series?.loaded?.length && <div className="list-row muted small">No models loaded right now. They load when a debate starts.</div>}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
