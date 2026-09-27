import { describe, expect, it } from 'vitest'
import { layout } from './RoundTable'

describe('round table layout', () => {
  // Below 640px the table is always folded into a line
  it.each([640, 768, 1100, 1600])('keeps every seat inside a %ipx stage, the desk clear of the table', (width) => {
    const t = layout(width, 8, ['Beagle', 'Coder'], false, false)
    for (const s of t.seats) {
      expect(s.x).toBeGreaterThanOrEqual(0)
      expect(s.x).toBeLessThanOrEqual(width)
      expect(s.y).toBeGreaterThan(0)
      expect(s.y).toBeLessThan(t.height)
    }
    const rightmost = Math.max(...t.seats.map((s) => s.x))
    for (const h of t.helpers) expect(h.x).toBeGreaterThan(rightmost + 24)
  })

  it('puts the chair at the head of the table', () => {
    const t = layout(1100, 5, [], false, false)
    expect(t.seats[0].y).toBe(Math.min(...t.seats.map((s) => s.y)))
    expect(t.seats[0].x).toBeCloseTo(t.cx)
  })

  it('folds into one line, with the clock clear of the seats', () => {
    const wide = layout(1100, 8, ['Beagle'], true, false)
    const lastSeat = Math.max(...wide.helpers.map((h) => h.x), ...wide.seats.map((s) => s.x))
    expect(wide.clockX).toBeGreaterThan(lastSeat + 30)
    const phone = layout(360, 8, ['Beagle', 'Coder'], true, true)
    expect(phone.clockX).toBeNull()
    expect(Math.max(...phone.seats.map((s) => s.x), ...phone.helpers.map((h) => h.x))).toBeLessThan(360)
    expect(phone.height).toBeGreaterThan(wide.height) // the clock moves to a second row
  })
})
