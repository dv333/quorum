import { useEffect, useRef, useState } from 'react'

// Renders a Mermaid diagram written by the chair. Local models sometimes produce invalid
// Mermaid, so failures render nothing instead of an error box.

let mermaidPromise = null
let counter = 0

function loadMermaid() {
  if (!mermaidPromise) {
    mermaidPromise = import('mermaid').then(({ default: mermaid }) => mermaid)
  }
  return mermaidPromise
}

function isDark() {
  const forced = document.documentElement.dataset.theme // Settings → Appearance
  if (forced) return forced === 'dark'
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches
}

function download(svg) {
  const url = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }))
  const a = Object.assign(document.createElement('a'), { href: url, download: 'quorum-diagram.svg' })
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

// The diagram full-window, for detail; Escape or a click closes it
function Expanded({ svg, onClose }) {
  const ref = useRef(null)
  useEffect(() => {
    ref.current?.focus()
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="diagram-full" role="dialog" aria-modal="true" aria-label="Diagram" tabIndex={-1} ref={ref} onClick={onClose}>
      <button className="icon-btn close" aria-label="Close" onClick={onClose}>✕</button>
      {/* eslint-disable-next-line react/no-danger */}
      <div className="diagram-full-svg" onClick={(e) => e.stopPropagation()} dangerouslySetInnerHTML={{ __html: svg }} />
    </div>
  )
}

// Common small-model mistakes that are safe to repair
export function tidy(code) {
  const cleaned = code
    .replace(/^\s*```(?:mermaid)?\s*/i, '')
    .replace(/```\s*$/, '')
    .replace(/^(\s*)graph\s/m, '$1flowchart ')
    .trim()
  // Quoted text used as a node ("Check budget" --> "Buy") isn't valid Mermaid: give each one an id
  const ids = new Map()
  return cleaned.replace(/(^|[^\[({|"\w])"([^"\n]+)"/gm, (match, pre, label) => {
    if (!ids.has(label)) ids.set(label, `n${ids.size + 1}`)
    return `${pre}${ids.get(label)}["${label}"]`
  })
}

export default function Mermaid({ code, onFail }) {
  const [svg, setSvg] = useState(null)
  const [failed, setFailed] = useState(false)
  const [full, setFull] = useState(false)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    let cancelled = false
    setFailed(false)
    loadMermaid().then(async (mermaid) => {
      const dark = isDark()
      const css = getComputedStyle(document.documentElement)
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: 'strict',
        theme: 'base',
        fontFamily: css.getPropertyValue('--font'),
        flowchart: { curve: 'basis', htmlLabels: false, padding: 14 },
        themeVariables: {
          fontSize: '14px',
          primaryColor: dark ? '#2c2c30' : '#ffffff',
          primaryBorderColor: dark ? '#48484e' : '#d2d2d7',
          primaryTextColor: dark ? '#f5f5f7' : '#1d1d1f',
          lineColor: dark ? '#6e6e73' : '#a1a1a6',
          secondaryColor: dark ? '#23233f' : '#eeeefe',
          tertiaryColor: dark ? '#1f3326' : '#eaf7ee',
          edgeLabelBackground: dark ? '#1f1f22' : '#f5f5f7',
          clusterBkg: 'transparent',
        },
      })
      try {
        const { svg: out } = await mermaid.render(`qm-${Date.now()}-${counter++}`, tidy(code))
        if (!cancelled) setSvg(out)
      } catch {
        if (!cancelled) { setFailed(true); onFail?.() }
        // mermaid leaves an error element behind on failure
        document.querySelectorAll('[id^="dqm-"]').forEach((el) => el.remove())
      }
    })
    return () => { cancelled = true }
  }, [code, onFail])

  if (failed || !svg) return null
  const copy = async () => {
    try { await navigator.clipboard.writeText(tidy(code)); setCopied(true); setTimeout(() => setCopied(false), 1500) } catch { /* no clipboard */ }
  }
  return (
    <div className="mermaid-wrap">
      <div className="diagram-tools">
        <button onClick={() => setFull(true)} title="Expand" aria-label="Expand diagram">
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true"><path d="M9.5 2.5h4v4M6.5 13.5h-4v-4M13.5 2.5 9 7M2.5 13.5 7 9" /></svg>
        </button>
        <button onClick={copy} title="Copy Mermaid source" aria-label="Copy diagram source">{copied ? 'Copied' : 'Copy'}</button>
        <button onClick={() => download(svg)} title="Download as SVG" aria-label="Download diagram as SVG">SVG</button>
      </div>
      {/* eslint-disable-next-line react/no-danger */}
      <div dangerouslySetInnerHTML={{ __html: svg }} onDoubleClick={() => setFull(true)} />
      {full && <Expanded svg={svg} onClose={() => setFull(false)} />}
    </div>
  )
}
