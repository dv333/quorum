# Changelog

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
