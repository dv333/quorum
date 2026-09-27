import { describe, expect, it } from 'vitest'
import { reducer } from './useDebate'

const msg = (id, extra = {}) => ({ id, content: '', body: '', thinking: '', status: 'streaming', ...extra })
const start = { messages: [msg(1), msg(2), msg(3, { content: 'done', status: 'done' })] }

describe('live debate state', () => {
  it('applies a frame of text pieces in order, per message', () => {
    const s = reducer(start, {
      type: 'deltas',
      items: [
        { id: 1, content: 'Hel' },
        { id: 2, thinking: 'hmm' },
        { id: 1, content: 'lo' },
        { id: 2, content: 'Hi' },
      ],
    })
    expect(s.messages[0]).toMatchObject({ content: 'Hello', body: 'Hello' })
    expect(s.messages[1]).toMatchObject({ content: 'Hi', thinking: 'hmm' })
  })

  it('leaves messages that got no text as they were, so they do not render again', () => {
    const s = reducer(start, { type: 'deltas', items: [{ id: 1, content: 'x' }] })
    expect(s.messages[1]).toBe(start.messages[1])
    expect(s.messages[2]).toBe(start.messages[2])
  })

  it('a message update replaces streamed text (a retry starts over)', () => {
    let s = reducer(start, { type: 'message_delta', id: 1, content: 'first try' })
    s = reducer(s, { type: 'message_updated', message: msg(1, { thinking: '(empty reply; trying once more)' }) })
    s = reducer(s, { type: 'message_delta', id: 1, content: 'second' })
    expect(s.messages[0]).toMatchObject({ content: 'second', thinking: '(empty reply; trying once more)' })
  })
})
