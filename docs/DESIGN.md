# Design notes

Quorum started from Andrej Karpathy's [llm-council](https://github.com/karpathy/llm-council) idea (several models answer,
review each other, and a chairman synthesizes) and reworks it for local models and live debate.

The goal is **a better answer than any single local model would give**. It is not a model benchmark, so there is no
leaderboard or scoring of models.

## Principles

- **Local first.** Models run on your own machine through Ollama. Cloud providers are optional, need your own key,
  and are never picked automatically.
- **Type and go.** Only the question is required. The council, chair, rounds and research are chosen automatically;
  everything is adjustable under *Customize*.
- **Ask only when it matters.** The chair clarifies a conundrum only when the best answer depends on something only
  the user knows, one question at a time, three at most, and always with a *Skip* option.
- **Readable by anyone.** Answers lead with a one-sentence bottom line, then key points, and can be rewritten simpler
  or more technical.
- **Transparent.** The full debate, research sources, raw thinking and per-model costs are one click away.

## Key decisions

| Area | Decision | Why |
|---|---|---|
| Agent names | Otter, Panda, Koala, Penguin, Hedgehog, Bunny, Turtle, Dolphin; researcher Beagle | Easy to remember with an emoji avatar. Neutral animals: models see the names too, so names like "Owl" (wise) or "Fox" (sly) could bias them |
| Anonymity | Models never see model names | Keeps the anti-bias idea of the original council |
| Turn-taking | Round-robin; each agent sees everything said before it | Predictable, and local GPUs run one model at a time anyway |
| Consensus | Self-reported stance footer on every message; all `AGREE` ends the debate from round 2 | Cheap and transparent; round 1 has nothing to agree with yet |
| Council size | Every installed local model joins (up to eight); cloud models only by choice | More perspectives; you chose which models to install |
| Chair | Picked by the largest member for each conundrum; it also decides the number of rounds (1–10) and answers trivial questions directly | Larger local models write better syntheses; simple questions shouldn't wait for a debate |
| Research | Firecrawl search plus query-relevant page excerpts; opening brief, on-request lookups, fact-check before the answer | Local models' knowledge is dated; every prompt also carries today's date |
| Long debates | Chair folds older rounds into a rolling summary | Local context windows are small |
| Memory | Estimate weights plus KV cache per model; run the council in parallel when it fits, otherwise load models one at a time | Avoids out-of-memory crashes |
| Answer format | BOTTOM LINE, Key points, optional Mermaid diagram, Where they differed, Details | Parseable into a layered UI; the diagram is shown in the Expert view, and invalid diagrams are hidden, not shown as errors |
| Thinking | On by default for reasoning models' debate turns; off for the chair's final answer and for JSON steps | Better arguments; the answer and structured steps need the context window and reliable output |
| Chair thinking | Off for the final answer | Reasoning models can exhaust the context before writing anything; the debate already did the reasoning |
| Storage | SQLite in `data/` | One file, no services |

## Known limits

- Answer quality is bounded by the local models you have. A small chair can ignore a correct fact-check.
- Small models sometimes produce malformed JSON or Mermaid; the app recovers what it can and hides the rest.
- GPU usage is read from macOS (`ioreg`) or NVIDIA (`nvidia-smi`); other GPUs show CPU and memory only.
