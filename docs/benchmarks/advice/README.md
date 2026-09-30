# Advice benchmark: is Quorum's advice better than its best model's alone?

20 everyday questions (money, cars, health, travel, work, home: [`benchmarks/advice/questions.json`](../../../benchmarks/advice/questions.json)).
There's no computed answer, so each pair is judged blind. Method: [`scripts/benchmark_advice.py`](../../../scripts/benchmark_advice.py).

- **The model alone:** qwen3.6 (35B), thinking, with the same instructions and answer format as the chair's first
  answer, so the two can't be told apart by their shape.
- **Quorum:** the advice council (the four largest models of at least 10B: qwen3.6, qwen3.8, gpt-oss 20B, gemma3
  12B), web research on (self-hosted Firecrawl), the chair's first answer improved by the debate.

## Results

| | Quorum better | Model alone better | Tie |
|---|---|---|---|
| First run, all 20 | 10 | 7 | 3 |
| After fixing answer presentation, the 10 lost or tied questions re-run | 5 | 4 | 1 |
| **Combined** | **15** | **4** | **1** |

Quorum's share of decided pairs: 59% on the first run, 79% combined.

Before this work, on 10 of these questions with research off, the model alone and Quorum each won 3 (4 ties).

### What lost pairs had in common (first run)

- **The checking showed through:** "is unconfirmed by the available sources", "Evidence contradicts on the base
  limit". Fixed: plainer evidence rules, and a polish step for sentences about the council, corrections or sources.
- **Process leaks:** "the council consensus corrected the initial protein estimate". Fixed by the same polish.
- **Invented context:** the car question was answered for Colorado. Fixed: answers can't assume facts about the user.

After the fixes, Quorum won the credit card, car repair, 401(k), deposit and job-offer questions, mostly on accuracy:
correct 2026 contribution limits, California's $125 receipt rule and filing fees, a worked break-even exit value.
It still lost the half marathon, kids and money (the council argued against saving jars), waking at 3 am and teen
phone use (broken citation marks in the text, since fixed).

## How it was judged, and the limits

- **I judged the pairs**, from `blind.md` (A and B in a random order, "Where they differed" and agents' names removed,
  the key in a separate file read only after judging). It isn't fully blind: Quorum's answers with research carry [n]
  citations.
- **A local judge didn't work:** phi4 picked whichever answer came first on every pair, so its votes all split once the
  order was swapped. The report now says so when that happens.
- **The re-run is favourable to Quorum:** only the questions it had lost or tied were run again, and a second try can
  go better by chance. A clean comparison runs all 20 again on the same code.
- One run per question; the model alone had no web research.

Raw answers and verdicts: [`results.json`](results.json).
