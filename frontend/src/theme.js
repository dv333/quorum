// Appearance: follow the system, or force light or dark. Kept in this browser; applied before the first paint.
const KEY = 'quorum.theme'
export const THEMES = [['system', 'System'], ['light', 'Light'], ['dark', 'Dark']]

export function getTheme() {
  try { return localStorage.getItem(KEY) || 'system' } catch { return 'system' }
}

export function applyTheme(theme) {
  const root = document.documentElement
  if (theme === 'light' || theme === 'dark') root.dataset.theme = theme
  else delete root.dataset.theme
}

export function setTheme(theme) {
  try { localStorage.setItem(KEY, theme) } catch { /* private mode: this session only */ }
  applyTheme(theme)
}
