# Running a 30B model locally: 48 GB Mac or 24 GB NVIDIA PC

> Best way to run a 30B-parameter model locally: 48 GB Mac or a PC with a 24 GB NVIDIA GPU? Speed, cost, quantization.

Reference: [reference.md](reference.md), sealed before Quorum answered. Regression spec:
`tests/regressions/local-30b-hardware.json`.

## Scores

| Answer | Accuracy | Evidence | Reasoning | Completeness | Clarity | Total | Regression |
|---|---|---|---|---|---|---|---|
| Reference | 8 | 7 | 8 | 9 | 9 | **41** | |
| v1 ([quorum.md](quorum.md)) | 4 | 3 | 4 | 3 | 5 | 19 | 3/8 |
| v2 | 6 | 6 | 6 | 6 | 8 | 32 | 6/8 |
| v3 | 4 | 5 | 5 | 5 | 5 | 24 | 6/8 |
| v4 | 7 | 6 | 7 | 8 | 8 | 36 | 7/8 |
| v5 | 7 | 7 | 6 | 6 | 8 | 34 | 6/8 |

**Blind judge on v4** ([judge.md](judge.md)): medians accuracy 8/5, evidence 7/6, reasoning 9/7, completeness 8/7,
clarity 8/7 (40 vs 32). Accuracy is 3 apart: v4's headline speeds came from a different model's benchmark.

## What each run exposed, and the fix

| Found in | Problem | Fix |
|---|---|---|
| v1 | "Sustained throughput favors the Mac"; no cost or speed numbers; the audit struck simple arithmetic; Amazon and Facebook pages as sources | One search per criterion the question lists, checked coverage of each; calculations allowed when shown, and checked; social posts and shop listings skipped |
| v2 | The chair audited its own answer; a bottom line built on unchecked cost claims, against the research | An independent auditor (the largest non-chair model), bottom-line reasons checked first, realistic versions of each option |
| v3 | Drifted to an RTX 5090 and 64 GB Macs; hedged "no single superior choice" | The question's numbers (48 GB, 24 GB) must be answered; conditional recommendations instead of "no clear winner" |
| v4 | Figures with no source behind them | Every price, size and speed checked against what the research read |
| v5 | "Cost" answered without a single price | A quantity criterion (cost, speed, range) needs a number |
