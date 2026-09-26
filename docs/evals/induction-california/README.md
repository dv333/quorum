# Replacing a gas stove with induction in California in 2026

> Should a California homeowner replace a gas stove with induction in 2026? Costs, rebates, health evidence.

Reference: [reference.md](reference.md), sealed before Quorum answered. Regression spec:
`tests/regressions/induction-california.json`.

## Scores

| Answer | Accuracy | Evidence | Reasoning | Completeness | Clarity | Total | Regression |
|---|---|---|---|---|---|---|---|
| Reference | 8 | 8 | 8 | 9 | 9 | **42** | |
| v1 ([quorum.md](quorum.md)) | 5 | 5 | 6 | 6 | 5 | 27 | 7/8 |
| v2 | 4 | 5 | 6 | 7 | 8 | 30 | 7/8 |
| v3 | 5 | 5 | 7 | 6 | 8 | 31 | 5/8 |

v1 had no web research at all: the Firecrawl cloud account ran out of credits (HTTP 402).

## What each run exposed, and the fix

| Found in | Problem | Fix |
|---|---|---|
| v1 | Search failed silently and the answer presented rebates as current; "[Beagle]'s data" in the text | A search outage stops retries and the answer says it wasn't checked against current sources; empty pages skipped; agent citations cleaned |
| v2 | A "2026 Clean Cooking Act" banning gas stoves, from an SEO site; a blog ranked as a systematic review | Laws and bills need an official page the research read; only journal, .gov and .edu pages count as reviews from their text |
| v3 | No asthma evidence, no cookware or ventilation | Evidence criteria need actual studies; the details cover practical requirements |
