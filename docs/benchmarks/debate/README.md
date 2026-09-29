# Debate benchmark: does the debate change the answer?

> **Update (2026-09-28): 50 of 50.** All 5 misses below were questions the chair answered directly, with thinking
> off. Since #41 the chair thinks (and checks) before a direct answer. The 20 direct questions were run again on that
> code ([`direct-rerun.json`](direct-rerun.json)): **20 of 20**, including all 5 misses. With the 30 debated questions
> (30 of 30), Quorum ties the model alone. This set can't show more than a tie; the
> [hard benchmark](../hard/) can.
>
> Also note: two test models (MLX builds, since removed) were installed during questions 22–50 and joined the
> automatically picked council.

50 questions with one computed answer each (Python output, math, probability): `benchmarks/debate/questions.json`. Method: `scripts/benchmark_debate.py`. Web research off, two rounds.

| | Right |
|---|---|
| qwen3.6:latest alone | 50 of 50 (100%) |
| An average council agent, round 1 | 91% |
| Best agent in the council (qwen3.8:latest), round 1 | 100% |
| The chair's draft after round 1 | 30 of 30 debated |
| **Quorum's final answer** | **45 of 50 (90%)** |

- The agents disagreed (some right, some wrong) on **13 of 50 (26%)** of the questions.
- Round 2 changed the answer on 0 questions: it **fixed 0** and broke 0.
- The chair answered 20 of 50 directly, without a debate. Those count toward Quorum's final answer but not toward the agent or disagreement numbers.
- Time: 5.1 min per question for Quorum, 24 s for the single model.
- Debates that ended without an answer: 0.

## Per question

| Question | Answer | Alone | Agents right (round 1) | Draft | Final |
|---|---|---|---|---|---|
| py-01 | `2` | ✓ | 7 of 8 | ✓ | ✓ |
| py-02 | `6` | ✓ | 7 of 8 | ✓ | ✓ |
| py-03 | `False` | ✓ | 8 of 8 | ✓ | ✓ |
| py-04 | `6` | ✓ | 7 of 8 | ✓ | ✓ |
| py-05 | `4` | ✓ | 8 of 8 | ✓ | ✓ |
| py-06 | `2` | ✓ | 7 of 8 | ✓ | ✓ |
| py-07 | `-4` | ✓ | 8 of 8 | ✓ | ✓ |
| py-08 | `2` | ✓ | 8 of 8 | ✓ | ✓ |
| py-09 | `-3` | ✓ | 7 of 8 | ✓ | ✓ |
| py-10 | `14` | ✓ | 7 of 8 | ✓ | ✓ |
| py-11 | `1 c` | ✓ | 4 of 8 | ✓ | ✓ |
| py-12 | `5` | ✓ | 8 of 8 | ✓ | ✓ |
| py-13 | `olh` | ✓ | 5 of 8 | ✓ | ✓ |
| py-14 | `1` | ✓ | 7 of 8 | ✓ | ✓ |
| py-15 | `4` | ✓ | 8 of 8 | ✓ | ✓ |
| py-16 | `2` | ✓ | 7 of 7 | ✓ | ✓ |
| py-17 | `12` | ✓ | 8 of 8 | ✓ | ✓ |
| py-18 | `7%` | ✓ | 7 of 8 | ✓ | ✓ |
| py-19 | `6` | ✓ | 6 of 8 | ✓ | ✓ |
| py-20 | `apple` | ✓ | 8 of 8 | ✓ | ✓ |
| py-21 | `512` | ✓ | 8 of 8 | ✓ | ✓ |
| py-22 | `[1, 2]` | ✓ | 5 of 8 | ✓ | ✓ |
| bat-ball | `0.05` | ✓ | 8 of 8 | ✓ | ✓ |
| lily-pads | `47` | ✓ | 8 of 8 | ✓ | ✓ |
| widgets | `5` | ✓ | 0 of 0 | · | ✓ |
| compound | `1157.63` | ✓ | 0 of 0 | · | ✓ |
| round-trip | `48` | ✓ | 0 of 0 | · | ✓ |
| zeros | `24` | ✓ | 0 of 0 | · | ✓ |
| y2k-day | `Saturday` | ✓ | 0 of 0 | · | ✓ |
| multiples | `2418` | ✓ | 0 of 0 | · | ✗ |
| discount | `100` | ✓ | 0 of 0 | · | ✓ |
| up-down | `-1` | ✓ | 0 of 0 | · | ✓ |
| handshakes | `45` | ✓ | 0 of 0 | · | ✓ |
| chessboard | `204` | ✓ | 0 of 0 | · | ✓ |
| clock | `7.5` | ✓ | 0 of 0 | · | ✗ |
| digits | `31` | ✓ | 7 of 8 | ✓ | ✓ |
| dice-seven | `0.167` | ✓ | 0 of 0 | · | ✓ |
| monty | `0.667` | ✓ | 8 of 8 | ✓ | ✓ |
| two-children | `0.333` | ✓ | 8 of 8 | ✓ | ✓ |
| birthday | `23` | ✓ | 0 of 0 | · | ✓ |
| three-flips | `0.875` | ✓ | 8 of 8 | ✓ | ✓ |
| socks | `3` | ✓ | 0 of 0 | · | ✓ |
| log | `15` | ✓ | 0 of 0 | · | ✗ |
| ages | `10` | ✓ | 8 of 8 | ✓ | ✓ |
| sevens | `20` | ✓ | 0 of 0 | · | ✓ |
| die-six | `6` | ✓ | 0 of 0 | · | ✓ |
| bird | `200` | ✓ | 8 of 8 | ✓ | ✓ |
| leap-days | `29` | ✓ | 0 of 0 | · | ✗ |
| percent-of | `80` | ✓ | 0 of 0 | · | ✓ |
| ages-sum | `18` | ✓ | 0 of 0 | · | ✗ |
