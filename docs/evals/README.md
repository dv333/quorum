# Answer quality evaluations

Each folder compares Quorum's answer to a reference answer on a question that needs research and judgment. The goal
is to find where Quorum falls short and improve it, not to rank models.

## Process

1. **Reference first, sealed.** A reference answer is written independently with web research and committed to git
   before Quorum answers (`reference.md`), so it can't be influenced by Quorum's answer.
2. **Quorum answers** the same question through the CLI (`quorum ask ... --no-questions`), exported as `quorum.md`.
3. **Two judges.** The material claims in both answers are fact-checked against sources and scored. As a second
   judge, Quorum's council scores both answers blind (labeled A and B in random order).
4. **Compare.** The answers *match* when their bottom lines agree (or are compatible) and no dimension differs by more
   than 2 points.
5. **Improve.** When they don't match, find the cause (research, reasoning, synthesis, missed requirements, search),
   fix Quorum, add the case to `tests/regressions/`, replay and re-score.

## Rubric (0–10 each, 50 total)

| Dimension | What it checks |
|---|---|
| Factual accuracy | Every material claim checked against a source; a wrong decisive fact costs heavily |
| Evidence quality | Primary sources, current information, honest labels on uncertainty |
| Reasoning | Weighs alternatives and says when the answer would flip |
| Completeness | Covers every part of the question |
| Clarity and actionability | A clear bottom line and next steps a reader can follow |

## Results

| Question | Reference | Quorum | Match | Changes |
|---|---|---|---|---|
| [Intermittent fasting vs. calorie restriction](intermittent-fasting/) | 44 | 39 (v9; first run 19) | First judge yes; blind judge not yet (evidence 6 vs 9) | 9 rounds of fixes, see the folder |
