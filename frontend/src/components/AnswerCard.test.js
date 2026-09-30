import { describe, expect, it } from 'vitest'
import { parseAnswer, readTime } from './AnswerCard'

const ANSWER = `BOTTOM LINE: **No, it isn't a scam.** You pay for how it's grown.

## Is it more nutritious? Not really.
Vitamins are about the same.

## What to do
1. Buy regular produce and wash it.

## Evidence
- A 2012 review of 237 studies found little difference.

## Key studies

1. **Smith-Spangler et al. (2012)** · review. Little difference. [Source](https://example.com)`

describe('a reader answer', () => {
  it('folds the evidence and key studies away from the sections', () => {
    const a = parseAnswer(ANSWER)
    expect(a.bottom).toContain("No, it isn't a scam.")
    expect(a.sections.map((s) => s.title)).toEqual(['Is it more nutritious? Not really.', 'What to do'])
    expect(a.evidence.map((e) => e.title)).toEqual(['Evidence', 'Key studies'])
  })

  it('counts only what is shown when it says how long it takes to read', () => {
    expect(readTime(parseAnswer(ANSWER))).toBe('Under a minute to read')
    const long = `BOTTOM LINE: x\n\n## Why\n${'word '.repeat(700)}`
    expect(readTime(parseAnswer(long))).toBe('3 min read')
  })

  it('keeps a review in the report format', () => {
    const a = parseAnswer('BOTTOM LINE: **Don\'t merge.**\n\n## Key points\n- SQL injection.\n\n## Where they differed\nOtter disagreed.')
    expect(a.sections[0].title).toBe('Key points')
    expect(a.dissent).toBe('Otter disagreed.')
    expect(a.evidence).toEqual([])
  })
})
