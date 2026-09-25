import { useEffect, useState } from 'react'

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
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches
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
  // eslint-disable-next-line react/no-danger
  return <div dangerouslySetInnerHTML={{ __html: svg }} />
}
