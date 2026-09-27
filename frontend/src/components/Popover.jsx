import { useEffect, useRef, useState } from 'react'

// A small menu or panel that opens from a button, and closes on a click outside it or Esc
export function usePopover() {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  useEffect(() => {
    if (!open) return undefined
    const onDown = (e) => { if (!ref.current?.contains(e.target)) setOpen(false) }
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])
  return { open, setOpen, ref }
}

// ⋯ with a short list of actions, each with a line saying what it does
export function MoreMenu({ label, items }) {
  const { open, setOpen, ref } = usePopover()
  return (
    <span className="pop-anchor" ref={ref}>
      <button className="btn more-btn" aria-label={label} title={label} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
        <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
          <circle cx="3.5" cy="8" r="1.4" /><circle cx="8" cy="8" r="1.4" /><circle cx="12.5" cy="8" r="1.4" />
        </svg>
      </button>
      {open && (
        <div className="pop-menu" role="menu">
          {items.map((it) => (
            <button key={it.label} role="menuitem" className={it.danger ? 'danger' : ''} onClick={() => { setOpen(false); it.run() }}>
              <b>{it.label}</b>
              {it.hint && <small>{it.hint}</small>}
            </button>
          ))}
        </div>
      )}
    </span>
  )
}
