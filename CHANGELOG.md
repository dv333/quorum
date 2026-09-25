# Changelog

## 0.2.0 (2026-09-25)

- Topic packs: pick a kind of debate (Review code, Stress-test a decision, Brainstorm ideas and more) or add your own
  as JSON in `data/packs/`; the pack's guidance reaches every agent and the chair
- `quorum` command: ask from the terminal, scripts or CI (`git diff | quorum ask --pack code-review`), with JSON output
- Export: copy or download an answer as Markdown (optionally with the whole debate), or print / save it as PDF
- The living answer: after every round the chair updates a draft answer pinned at the top, with changed words
  highlighted, what changed and whose argument changed it, and earlier drafts one click away
- Why?: select any part of the answer to see which agents argued for it, who pushed back, and the sources behind it
- A compact stage (the default): one slim row of avatars with stance dots; the chevron shows the full view with
  names, models and status. On phones the header also takes less room
- Long agent messages fold to their first lines (Show more), keeping each stance and position visible
- Round recaps (stances, who dissents, fold a round away) and a plain-words live status line by the message box
- Councils built for the pack: a pack can prefer certain models (Review code prefers coding models); the council
  becomes those specialists plus the strongest generalists, and Quorum suggests a model to add, checked against
  memory and free disk, when none is installed. Nothing is downloaded without a click; nothing is ever removed
- `quorum doctor`: a health report measured from your recent conundrums (outcomes, time to answer, stance and failure
  rates, speed per model) with plain-language findings; `--ask` gets the council's diagnosis. Also at `/api/diagnostics`
- A features guide with screenshots: [docs/FEATURES.md](docs/FEATURES.md)
- The frontend's backend address can be changed with `QUORUM_URL`

## 0.1.1 (2026-09-25)

- `start.sh` / `start.ps1` check prerequisites and offer to install missing ones (Homebrew on macOS, winget on
  Windows), start Ollama and Docker when they aren't running, and add `--check` and `--yes`
- Repair a half-finished `npm install` (for example after an interrupted first run) instead of failing with
  "vite: command not found"
- Fall back to the public npm registry when a custom one in `~/.npmrc` isn't reachable (for example, a company mirror
  when you're off VPN)

## 0.1.0 (2026-09-24)

First public release.

- Council of local models that debate in rounds under animal names, with stance-based consensus
- Every installed model joins the council (up to eight animal agents), reasoning models think by default
- The chair sizes each conundrum: direct answers for simple questions, 1–10 rounds for the rest
- The chair clarifies ambiguous conundrums first (one question at a time) and confirms its assumptions
- `@` mentions with autocomplete, copy buttons and date and time on every message
- Beagle, a web researcher powered by Firecrawl: opening brief, on-request lookups, fact-check before the answer
- Automatic council and chair selection sized to your hardware
- Structured answers with a bottom line, key points, dissent and Simple / Standard / Expert levels (diagrams in Expert)
- Token, time and search metrics for every answer
- Live Memory / CPU / GPU charts
- Optional cloud providers with your own API key
- First-launch walkthrough: Ollama check, starter models, web search setup; `start.sh` starts web search automatically
- Responsive light and dark UI; sound, notification and badge alerts
