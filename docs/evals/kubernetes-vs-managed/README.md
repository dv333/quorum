# Kubernetes or a managed container service for 5 engineers and 12 services

> Kubernetes or a managed container service (ECS, Cloud Run, Fly.io) for a 5-engineer team running 12 services?

Reference: [reference.md](reference.md), sealed before Quorum answered. Regression spec:
`tests/regressions/kubernetes-vs-managed.json`.

## Scores

| Answer | Accuracy | Evidence | Reasoning | Completeness | Clarity | Total | Regression |
|---|---|---|---|---|---|---|---|
| Reference | 8 | 7 | 8 | 8 | 9 | **40** | |
| v1 ([quorum.md](quorum.md)) | 6 | 6 | 6 | 7 | 5 | 30 | 7/7 |
| v2 | 6 | 6 | 7 | 6 | 8 | 33 | 6/7 |

v1 lost two lookups when the Firecrawl cloud credits ran out; later runs use self-hosted Firecrawl.

**Blind judge on v2** ([judge.md](judge.md)): medians accuracy 9/6, evidence 8.5/5, reasoning 9/7, completeness 9/8,
clarity 9/7 (45 vs 33). v2's claim that Cloud Run enforces mTLS by default is wrong.

## What each run exposed, and the fix

| Found in | Problem | Fix |
|---|---|---|
| v1 | Four-sentence hedged bottom line; Docker and Nomad added because the shortlist treated "A or B" as open | Bottom lines over 55 words are flagged; the shortlist runs only for open which-is-best questions |
| v2 | "(Beagle research)" in the answer; an unsourced security claim | Researcher and agent names cleaned from answers; figures checked against the sources |
