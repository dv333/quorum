# Architecture

Quorum is a FastAPI backend plus a React (Vite) frontend. Everything runs on your machine; models are served by
[Ollama](https://ollama.com) or any OpenAI-compatible server, and web research goes through
[Firecrawl](https://github.com/firecrawl/firecrawl).

```
Browser (React) ── /api (REST + Server-Sent Events) ──► FastAPI backend ──► Ollama / OpenAI-compatible servers
                                                            │
                                                            ├──► Firecrawl (web search, optional)
                                                            └──► SQLite (data/council.db)
```

## How a conundrum flows

1. **Intake.** Every installed local model gets a seat (up to eight), and Beagle is assigned a mid-sized model. A
   topic pack that prefers certain models instead gets those specialists plus the strongest generalists. The
   largest member picks the chair and a short title. The chair then sizes up the conundrum: trivial messages
   (greetings, simple facts) it answers directly with no debate; otherwise it sets the number of rounds (1–10), and if
   the answer depends on something only the user knows, it asks up to three questions (one at a time, with suggested
   answers) and ends with a summary of assumptions for the user to confirm.
2. **Opening brief.** Beagle (the researcher) plans searches, reads the most relevant parts of the top pages and posts a
   cited brief.
3. **Rounds.** Agents speak round-robin under animal names (Otter, Panda, Koala, Penguin, Hedgehog). Every message ends
   with a stance (`AGREE`, `REFINE`, `DISAGREE`) and a one-line position. Agents can ask `@Beagle: …` for facts; the
   brief lands before the next speaker. The user can interject at any time.
4. **Consensus.** The debate ends when everyone agrees (from round 2), at the round limit, or when the user asks for the
   answer. After every round that isn't the last, the chair writes a short draft answer (the "living answer", stored
   in `drafts`). Long debates are compressed into a rolling summary written by the chair.
5. **Answer.** Beagle fact-checks the claims the answer will rely on, then the chair writes a structured answer: bottom
   line, key points, an optional Mermaid diagram (shown in the Expert view), where the agents differed, and details.
   Simple and Expert versions are rewritten on demand, and **Why?** traces a selected passage back to the agents and
   sources behind it (on demand, cached in `provenance`).

## Backend (`backend/`)

| Module | Responsibility |
|---|---|
| `main.py` | FastAPI app: REST endpoints and one Server-Sent Events stream per debate |
| `engine.py` | `DebateEngine`, one per debate: intake, turns, research queue, summaries, fact-check, answer, usage metrics |
| `prompts.py` | Every prompt the models see (all include today's date, since local models don't know it) |
| `parsing.py` | Stance footers, `@Beagle` requests, `<think>` blocks, forgiving JSON parsing, fact-check cleanup |
| `providers.py` | Streaming chat for Ollama (`/api/chat`) and OpenAI-compatible servers; model listing and pulls |
| `inventory.py` | Installed models across servers, memory estimates, fit, the automatic council |
| `hardware.py` | RAM / VRAM detection and KV-cache estimates |
| `firecrawl.py` | Web search with page excerpts, self-hosted or cloud |
| `monitor.py` | Background CPU / memory / GPU sampler for the live charts |
| `setup.py` | First-launch helpers: what's installed, starter model pack, starting local web search |
| `packs.py` | Topic packs: loading and validating built-in (`backend/packs/`) and user (`data/packs/`) packs |
| `diagnostics.py` | Health report measured from the database (no model calls), with findings; `quorum doctor` |
| `export.py` | Markdown export: the answer at a reading level, its sources and optionally the whole debate |
| `cli.py` | The `quorum` command; talks to the running backend over HTTP using only the standard library |
| `db.py` | SQLite schema, lightweight migrations and helpers |
| `config.py` | Settings (all overridable through environment variables or `.env`) |

### Engine rules worth knowing

- One asyncio task at a time per debate. All state is in SQLite; only the text of in-flight streams lives in memory,
  so a client that connects mid-stream still gets everything in its first snapshot.
- Every model call goes through `_stream_into` or `_complete`, which record a `usage` row (tokens in and out, time).
  Don't call the chat client directly from the engine.
- Models only ever see the animal names. Model names are shown in the UI only.
- Thinking (Ollama's `thinking` field or inline `<think>` tags) is stored separately and never forwarded to other
  agents or the chair.
- Round-0 messages belong to intake (the question, answers to the chair, the opening brief). They are never folded into
  the rolling summary.
- API keys live in `endpoints.api_key` and are never returned by the API.
- In sequential memory mode (the council doesn't fit in memory at once), requests pass `keep_alive: 0` unless the next
  speaker uses the same model.

### Debate statuses

`intake` → `clarifying` / `confirming` (waiting on the user) → `running` ⇄ `paused` → `concluding` → `concluded`.
A trivial conundrum goes straight from `intake` to `concluding` (the chair answers directly; the verdict's reason is
`direct`).
A `@Beagle` lookup after an answer runs as `researching` and returns to `concluded`.

## Frontend (`frontend/src/`)

| File | Responsibility |
|---|---|
| `App.jsx` | Routing (`#q/<id>`, `#settings/<tab>`, `#welcome`), sidebar state, alerts |
| `useDebate.js` | Live debate state from the SSE stream (snapshot, then deltas), with reconnects |
| `components/Home.jsx` | The ask screen and automatic council preview |
| `components/DebateView.jsx` | Stage, thread, intake bubbles, composer |
| `components/AnswerCard.jsx` | Structured answer, reading levels, diagram, metrics |
| `components/Onboarding.jsx` | First-launch walkthrough |
| `components/Settings.jsx` | Models, providers and web search |
| `components/Resources.jsx` | Live Memory / CPU / GPU cards and charts |
| `attention.js` | Chime, system notifications and tab badge |
| `agents.js` | Emoji and colors for each agent name (keep in sync with `config.HANDLES`) |

## Tests

```bash
uv run pytest
```

Engine tests run whole debates against a scripted fake model server and fake search, so no models are needed.
