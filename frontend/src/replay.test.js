import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { timeline } from './replay'
import { reducer } from './useDebate'

const demos = JSON.parse(readFileSync(new URL('../public/demos/index.json', import.meta.url)))

describe.each(demos.map((d) => d.name))('the %s demo', (name) => {
  const recording = JSON.parse(readFileSync(new URL(`../public/demos/${name}.json`, import.meta.url)))

  it('replays, in order, to the debate as it was recorded', () => {
    const { start, events } = timeline(recording)
    expect(events.every((e, i) => i === 0 || e.t >= events[i - 1].t)).toBe(true)
    let s = reducer(undefined, { type: 'snapshot', state: start })
    for (const { event } of events) s = reducer(s, event)
    const final = recording.snapshot
    expect(s.debate.status).toBe(final.debate.status)
    expect(s.verdicts.map((v) => v.id)).toEqual(final.verdicts.map((v) => v.id))
    const shown = new Map(s.messages.map((m) => [m.id, m]))
    for (const m of final.messages.filter((x) => x.status === 'done' && x.author_kind === 'seat')) {
      expect(shown.get(m.id)?.content).toBe(m.content)
    }
  })
})
