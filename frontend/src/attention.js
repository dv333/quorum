// Getting the user's attention: a soft chime, a system notification when Quorum isn't in front,
// and a count in the tab title. Browsers only allow sound and notification prompts after a user
// gesture, so both are unlocked on the first click or key press.

let ctx = null

function unlock() {
  try {
    ctx = ctx || new (window.AudioContext || window.webkitAudioContext)()
    if (ctx.state === 'suspended') ctx.resume()
  } catch {
    ctx = null
  }
}
window.addEventListener('pointerdown', unlock, { once: true, capture: true })
window.addEventListener('keydown', unlock, { once: true, capture: true })

// Two soft sine notes, like a glass tap
export function chime() {
  if (!ctx) return
  const now = ctx.currentTime
  ;[[1318.5, 0], [1975.5, 0.11]].forEach(([freq, at]) => {
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.type = 'sine'
    osc.frequency.value = freq
    gain.gain.setValueAtTime(0, now + at)
    gain.gain.linearRampToValueAtTime(0.07, now + at + 0.012)
    gain.gain.exponentialRampToValueAtTime(0.0001, now + at + 0.9)
    osc.connect(gain).connect(ctx.destination)
    osc.start(now + at)
    osc.stop(now + at + 1)
  })
}

export function requestNotifications() {
  if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission().catch(() => {})
  }
}

export function notify(title, body, onClick) {
  chime()
  if (!('Notification' in window) || Notification.permission !== 'granted') return
  if (document.visibilityState === 'visible' && document.hasFocus()) return
  try {
    const n = new Notification(title, { body, tag: 'quorum', silent: true })
    n.onclick = () => { window.focus(); onClick?.(); n.close() }
  } catch {
    // some browsers only allow notifications from a service worker
  }
}

export function setBadge(count, appName) {
  document.title = count > 0 ? `(${count}) ${appName}` : appName
}
