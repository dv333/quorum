# Intermittent fasting vs. daily calorie restriction

> Intermittent fasting vs. daily calorie restriction for weight loss: what does the evidence say?

The reference answer ([reference.md](reference.md)) was written first, with web research, and committed before Quorum
answered. Quorum answered through `quorum ask ... --no-questions` with its default council of eight local models and
web research on. `quorum.md` is the first answer; `quorum-v2.md` onward follow each round of fixes.

## Scores

Scored against the rubric in [../README.md](../README.md), with the material claims checked against sources. The
regression column is `tests/regressions/intermittent-fasting.json` (answer text only).

| Answer | Accuracy | Evidence | Reasoning | Completeness | Clarity | Total | Regression |
|---|---|---|---|---|---|---|---|
| Reference | 9 | 9 | 8 | 9 | 9 | **44** | |
| v1 ([quorum.md](quorum.md)) | | | | | | 19 | 6/9 |
| v3 | | | | | | 31 | 5/9 |
| v4 | 8 | 6 | 8 | 7 | 8 | 37 | 8/9 |
| v5 | 7 | 4 | 6 | 5 | 6 | 28 | 6/9 |
| v6 | 6 | 6 | 6 | 5 | 5 | 28 | 5/9 |
| v7 | 7 | 7 | 7 | 7 | 6 | 34 | 9/9 |
| v8 | 8 | 7 | 8 | 6 | 7 | 36 | 8/9 |
| v9 | 8 | 8 | 8 | 7 | 8 | 39 | 9/9 |
| v10 | | | | | | cut off | 8/9 |
| **v11** | **8** | **9** | **8** | **8** | **8** | **41** | **9/9** |

v9 was the first to match on the first judge: the bottom lines agree (a tie when calories are matched, so pick what you can keep
up) and no dimension differs by more than 2.

**Second judge (blind).** Quorum's own council scored v9 (as A) against the reference (as B) without knowing which
was which ([judge.md](judge.md)). Median scores: accuracy 7/8, evidence 6/9, reasoning 8/9, completeness 8/8,
clarity 8/9, totals 35/50 vs 42/50. The judge agreed on four dimensions but put evidence quality 3 points apart: v9's key points cited checked claims by
number instead of naming the studies and their effect sizes.

**v11 matches on both judges.** The blind council (A = reference, B = v11 this time) gave medians of accuracy 9/7,
evidence 9/8, reasoning 9/7, completeness 9/7, clarity 9/7 (45 vs 36): every dimension within 2. v11 names the 2022
NEJM trial, the BMJ network meta-analysis (99 trials, 6,582 people, alternate-day fasting −1.29 kg, 95% CI −1.99 to
−0.59) and the Cochrane review (21 studies, 1,430 people) in its key points and key studies. Remaining gaps: it dates
the BMJ analysis 2024 (published 2025), misses that the alternate-day edge fades in longer trials, and repeats one
point.

## What changed along the way

| Found in | Problem | Fix |
|---|---|---|
| v1 | No reviews or trials, no numbers; peripheral claims checked; checking jargon in the answer | Evidence-first research (reviews and trials ranked first), the chair sees the research, the central claim is checked first |
| v2 | The audit removed well-sourced facts and whole sections | Tolerant quote matching, research counts as sourced, revisions must keep every section |
| v3–v4 | Only one round; forms of fasting not compared; studies unnamed | Evidence questions get two or more rounds, searches for the newest meta-analysis and Cochrane review, numbered citations |
| v5 | Research found the BMJ and Cochrane reviews but the claim check never saw them | Claims are checked against the pages the research already read |
| v6 | Invented study years; the audit's notes pasted into the answer | A checked **Key studies** section: each study's quote and numbers must be on its page |
| v7 | Details leaned toward one option against the bottom line; "not stated" filler | Every section must agree with the bottom line; filler dropped from studies |
| v8 | Forms and safety missing | A network meta-analysis search; health answers compare each form and say who should be careful |
| v9 | Studies cited by number; no effect sizes | Key studies read from each page's results; key points name studies with their numbers |
| v10 | The answer stopped after one bullet: the prompt filled 8,071 of 8,192 context tokens | Calls get a bigger context (up to 16K) when the prompt plus 2K for the reply won't fit |

Quality varies from run to run (v5 and v6 fell back to 28), because the chair and the pages found differ each time.
