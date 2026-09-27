import { useEffect, useState } from 'react'

// "45s", "4m 07s", or "1h 02m" past an hour
export function formatElapsed(ms) {
  const s = Math.max(0, Math.floor(ms / 1000))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (h) return `${h}h ${String(m).padStart(2, '0')}m`
  return m ? `${m}m ${String(s % 60).padStart(2, '0')}s` : `${s}s`
}

// The current time, updated every `every` ms while `active`
export function useNow(active, every = 1000) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return undefined
    const t = setInterval(() => setNow(Date.now()), every)
    return () => clearInterval(t)
  }, [active, every])
  return now
}
