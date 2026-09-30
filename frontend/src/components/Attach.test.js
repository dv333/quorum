import { describe, expect, it } from 'vitest'
import { fileSize, refusal } from './Attach'
import { filesOf } from './DebateView'
import { reducer } from '../useDebate'

describe('attachments', () => {
  it('refuses files it can’t read, too many, or too big, before uploading', () => {
    expect(refusal({ name: 'post.PNG', size: 10 }, 0)).toBeNull()
    expect(refusal({ name: 'setup.exe', size: 10 }, 0)).toMatch(/Can't read .exe/)
    expect(refusal({ name: 'a.pdf', size: 30_000_000 }, 0)).toMatch(/Over 20 MB/)
    expect(refusal({ name: 'a.pdf', size: 10 }, 5)).toMatch(/At most 5/)
  })

  it('shows sizes the way people read them', () => {
    expect(fileSize(900)).toBe('900 B')
    expect(fileSize(48_000)).toBe('48 KB')
    expect(fileSize(2_400_000)).toBe('2.4 MB')
  })

  it('shows what the council read once the server has it, else what was sent', () => {
    const msg = { id: 7, meta: { attachments: [{ id: 'a', name: 'feed.png' }] } }
    expect(filesOf([], msg)).toEqual([{ id: 'a', name: 'feed.png' }])
    const read = [{ id: 'a', message_id: 7, read: 'A post', topic: 1 }, { id: 'b', message_id: 9, topic: 2 }]
    expect(filesOf(read, msg)).toEqual([read[0]])
  })

  it('replaces one question’s files when they are read', () => {
    const state = { attachments: [{ id: 'a', topic: 1 }, { id: 'b', topic: 2 }] }
    const next = reducer(state, { type: 'attachments_updated', topic: 1, attachments: [{ id: 'a', topic: 1, read: 'text' }] })
    expect(next.attachments).toEqual([{ id: 'b', topic: 2 }, { id: 'a', topic: 1, read: 'text' }])
  })
})
