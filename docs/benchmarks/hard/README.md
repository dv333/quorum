# Hard benchmark: does Quorum beat its best model alone?

The first debate benchmark ([docs/benchmarks/debate](../debate/)) hit its ceiling: the best local model alone scored
50 of 50, so Quorum could only tie. This set is harder. It has 50 questions with one checkable answer each: 22 "what
does this Python program print", 28 counting, probability, date and rate problems. Every answer is computed by
[`benchmarks/hard/build.py`](../../../benchmarks/hard/build.py), none typed by hand, and a test fails if the two drift
apart.

Both sides get the same instruction, to start with a line `ANSWER: <value>`, and only that line is graded (for Quorum,
the final answer's `ANSWER:` line, else its bottom line). A stray number in the explanation can't count as right.

## Results

25 questions (every other one of the 50, so both kinds are covered), one run each, on an M5 Pro with 48 GB.

| | Right | Median time |
|---|---|---|
| qwen3.6 (35B MoE) alone | 15 of 25 (60%) | 2.1 min |
| Quorum, as on `main` | 24 of 25 (96%) | 10.0 min |
| **Quorum with Python checks** | **25 of 25 (100%)** | 11.4 min |

On all 50, qwen3.6 alone scored 31 (62%): 12 of 22 Python questions and 19 of 28 others.

- **The council does most of the work.** Without any tool it fixed 9 of the model's 10 misses. On the Python
  questions the debate alone was enough: 11 of 11, against 6 of 11 alone.
- **Python checks fixed the one question the council missed.** Counting Friday the 13ths from 2001 to 2030, the
  debate without checks settled on 51. With checks, round 1 gave seven different answers (42, 52, 43, 7, 171, 52, 66);
  three agents' programs printed 52, and in round 2 every agent said 52. Enumeration is where reasoning by hand
  slips, and a printed result is evidence the others can accept. (In the first benchmark, round 2 never changed an
  answer.)
- **Checks were used widely.** They ran in 20 of the 25 conundrums (66 messages with a program's output, one program
  that failed). gpt-oss tried to call its own Python tool 5 times; each time it answered again without checks and kept
  its seat.
- **The model alone often ran out of room to think.** On 27 of the 50 it used its whole 8,192-token context thinking
  and answered again without thinking, like an agent in Quorum would. A larger context might help it; Quorum's agents
  get at most 4,096 tokens per turn.
- **The cost is time.** A debate takes 7 to 15 minutes; the chair answered 7 to 8 of the 25 directly in under two
  minutes.

## Per question

| Question | Answer | Alone | Quorum | With checks |
|---|---|---|---|---|
| py-fib-sign | `0` | ✗ | ✓ | ✓ |
| py-nonlocal | `21` | ✓ | ✓ | ✓ |
| py-finally | `23` | ✓ | ✓ | ✓ |
| py-grid | `21` | ✓ | ✓ | ✓ |
| py-floordiv | `-8` | ✗ | ✓ | ✓ |
| py-class-attr | `4` | ✓ | ✓ | ✓ |
| py-lru-calls | `36` | ✗ | ✓ | ✓ |
| py-skip-threes | `2772` | ✗ | ✓ | ✓ |
| py-sort-key | `['b10', 'b9', 'b1']` | ✓ | ✓ | ✓ |
| py-bits | `-43` | ✓ | ✓ | ✓ |
| py-str-mul | `7` | ✗ | ✓ | ✓ |
| not-6-10-15 | `734` | ✓ | ✓ | ✓ |
| stairs-123 | `5768` | ✓ | ✓ | ✓ |
| last-two-7 | `49` | ✓ | ✓ | ✓ |
| digit-sum-20 | `633` | ✗ | ✓ | ✓ |
| no-11 | `377` | ✓ | ✓ | ✓ |
| palindromes-sum | `495000` | ✗ | ✓ | ✓ |
| josephus | `31` | ✓ | ✓ | ✓ |
| coupon-die | `147/10` | ✓ | ✓ | ✓ |
| tuesday-boy | `13/27` | ✓ | ✓ | ✓ |
| three-dice-10 | `1/8` | ✓ | ✓ | ✓ |
| friday-13 | `52` | ✗ | ✗ (51) | ✓ |
| clock-738 | `1` | ✓ | ✓ | ✓ |
| avg-speed | `900/19` | ✗ | ✓ | ✓ |
| socks-colors | `6` | ✗ | ✓ | ✓ |

## Limits

- One run per question, 25 questions. The gap between the council and the model alone (24 vs 15) is large; the gap
  between Quorum with and without checks (25 vs 24) is one question.
- I wrote these questions. They're meant to trip a model up (traps, long traces, enumeration), so they aren't a sample
  of what people usually ask.
- The council was picked automatically from the 9 models installed: qwen3.8, qwen3.6, gpt-oss 20B, qwen3 14B,
  phi4 14B, gemma3 12B and 4B, deepseek-r1 8B, llama3.1 8B. Research was off; two rounds.

## Reproduce

```bash
uv run python scripts/benchmark_debate.py --questions benchmarks/hard/questions.json --out docs/benchmarks/hard --ids <ids>
```

Raw results: [`results.json`](results.json) (`single` for all 50; `quorum_merged` and `quorum_python` for the 25).
