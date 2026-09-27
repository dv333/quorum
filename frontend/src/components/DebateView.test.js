import { describe, expect, it } from 'vitest'
import { tableFolds } from './DebateView'

// A live debate that follows the latest message: every streamed word scrolls to the bottom, and the browser clamps the
// scroll position when folding the table makes the page shorter
function follow(contentHeight, rule, frames = 60) {
  const client = 600
  const table = 324
  const line = 56
  let folded = false
  let flips = 0
  for (let i = 0; i < frames; i++) {
    const height = contentHeight + (folded ? line : table)
    const top = Math.max(0, height - client) // stuck to the bottom
    const next = rule(folded, top, height - client, folded ? 0 : table - line)
    if (next !== folded) flips++
    folded = next
    // The fold changes the page height; the browser clamps the position and fires another scroll
    const after = contentHeight + (folded ? line : table)
    const clamped = Math.min(top, Math.max(0, after - client))
    const again = rule(folded, clamped, after - client, folded ? 0 : table - line)
    if (again !== folded) flips++
    folded = again
  }
  return { folded, flips }
}

const before = (was, top) => (was ? top > 8 : top > 90) // the rule that flickered

describe('the round table while a debate streams', () => {
  it('used to fold and open on every frame when the page was just a little taller than the window', () => {
    expect(follow(500, before).flips).toBeGreaterThan(50)
  })

  it.each([100, 300, 500, 600, 700, 900, 3000])('settles with %ipx of messages', (content) => {
    expect(follow(content, tableFolds).flips).toBeLessThanOrEqual(1)
  })

  it('folds once the thread is long enough to stay scrolled after folding', () => {
    expect(follow(3000, tableFolds)).toEqual({ folded: true, flips: 1 })
    expect(follow(500, tableFolds).folded).toBe(false)
  })
})
