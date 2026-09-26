import { useEffect, useRef } from 'react'

const MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent)
const MOD = MAC ? '⌘' : 'Ctrl'

const GROUPS = [
  ['Anywhere', [
    [[MOD, 'N'], 'New conundrum'],
    [[MOD, 'K'], 'Search conundrums', ['/']],
    [[MAC ? '⌃' : 'Ctrl', MOD, 'S'], 'Show or hide the sidebar'],
    [[MOD, 'Z'], 'Undo a delete'],
    [['?'], 'Show these shortcuts'],
  ]],
  ['In a conversation', [
    [['Enter'], 'Send'],
    [['Shift', 'Enter'], 'New line'],
    [['@'], 'Ask an agent or Beagle directly'],
    [['Esc'], 'Close a menu or panel'],
  ]],
]

// A sheet listing the keyboard shortcuts; "?" opens it, Escape or a click outside closes it
export default function ShortcutsHelp({ onClose }) {
  const ref = useRef(null)
  useEffect(() => {
    ref.current?.focus()
    const onKey = (e) => { if (e.key === 'Escape' || e.key === '?') { e.preventDefault(); onClose() } }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="shortcuts-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="shortcuts" role="dialog" aria-modal="true" aria-labelledby="shortcuts-title" tabIndex={-1} ref={ref}>
        <div className="shortcuts-head">
          <h2 id="shortcuts-title">Keyboard shortcuts</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">✕</button>
        </div>
        {GROUPS.map(([title, rows]) => (
          <section key={title}>
            <h3>{title}</h3>
            {rows.map(([keys, label, alt]) => (
              <div className="shortcut" key={label + keys.join('')}>
                <span>{label}</span>
                <span className="keys">
                  {keys.map((k) => <kbd key={k}>{k}</kbd>)}
                  {alt && <><span className="or">or</span>{alt.map((k) => <kbd key={k}>{k}</kbd>)}</>}
                </span>
              </div>
            ))}
          </section>
        ))}
      </div>
    </div>
  )
}
