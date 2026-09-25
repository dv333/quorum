<p align="center">
  <img src="docs/images/logo.svg" width="88" alt="Quorum logo">
</p>

<h1 align="center">Quorum</h1>

<p align="center">
  <b>Many minds. One answer.</b><br>
  A council of AI models that runs on your own computer, debates your question, and agrees on one answer.
</p>

<p align="center">
  <a href="https://github.com/dv333/quorum/actions/workflows/ci.yml"><img src="https://github.com/dv333/quorum/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-3776ab.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/runs-100%25%20local-34c759.svg" alt="Runs locally">
</p>

<p align="center">
  <img src="docs/images/demo.gif" width="880" alt="Quorum answering: Should I rent or buy a home in Cupertino in 2026?">
</p>

Ask one model a hard question and you get one opinion, with its blind spots. Quorum puts several local models in a room
instead. If your question is ambiguous, the chair asks you what it needs to know. Then a researcher looks up current
facts, the council argues it out in rounds, and the chair writes one answer anyone can read, with the reasoning and
dissent one tap away.

Everything runs on your machine with [Ollama](https://ollama.com). No account, no API key, no data leaving your
computer (unless you choose to add web search or a cloud model).

## See it work

| The chair clarifies | The council debates | One clear answer |
|:---:|:---:|:---:|
| <img src="docs/images/demo-interview.gif" alt="The chair asks clarifying questions one at a time"> | <img src="docs/images/demo-debate.gif" alt="Agents debate in rounds while Beagle researches"> | <img src="docs/images/demo-answer.gif" alt="The final answer, metrics and the Simple reading level"> |
| Ambiguous? The chair asks up to three questions, with tap-to-answer suggestions, then confirms its assumptions. | Every model you have joins as an agent and argues in rounds; Beagle 🐶 fetches current facts with sources when anyone asks. | A one-line bottom line, key points, where they differed, and Simple / Standard / Expert versions (Expert adds a diagram). |

A full-length recording is in [docs/images/demo.mp4](docs/images/demo.mp4).

## Features

- **Type and go.** Ask anything. Every model you have installed joins the council as an agent, and the largest one
  picks the best chair for your question.
- **Effort that fits the question.** Say "hi" or ask a simple fact and the chair just answers. Harder questions get
  a debate sized by the chair, from one round to ten.
- **It asks before it guesses.** When the answer depends on something only you know (budget, timeline, where you
  live), the chair asks, one question at a time, three at most. Otherwise it gets straight to work.
- **Memorable agents.** 🦦 Otter, 🐼 Panda, 🐨 Koala, 🐧 Penguin, 🦔 Hedgehog, 🐰 Bunny, 🐢 Turtle and 🐬 Dolphin
  debate; 🐶 Beagle does the web research on a model of its own. The models only ever see these names, never each
  other's model names. Type `@` to talk to any of them.
- **Current facts, with sources.** Beagle searches the web through [Firecrawl](https://github.com/firecrawl/firecrawl),
  posts cited briefs, and fact-checks the claims the final answer depends on.
- **Answers for everyone.** The bottom line comes first. Switch between Simple, Standard and Expert; the Expert view
  adds a diagram when one helps. Every message has a copy button and a timestamp.
- **Nothing hidden.** Expand the whole debate, every source and each model's thinking (reasoning models think by
  default). *Behind the answer* shows tokens, time per model, searches and pages read.
- **You're in the loop.** Add a thought mid-debate, ask `@Beagle` anything, pause, or ask for the answer now. A soft chime,
  a notification and a badge tell you when the chair needs you or the answer is ready.
- **Knows your hardware.** Memory, CPU and GPU charts; per-model memory estimates; councils that don't fit are run one
  model at a time instead of crashing.
- **Bring cloud models if you like.** Add OpenAI, Anthropic, Gemini, OpenRouter, Groq, Mistral, Together, DeepSeek or
  any OpenAI-compatible API with your own key. Cloud models are never picked automatically.
- **A native-feeling app.** Light and dark mode, glass materials, and layouts from phone to ultrawide.

## Screenshots

| | |
|:---:|:---:|
| <img src="docs/images/home-light.png" alt="Home"> | <img src="docs/images/welcome.png" alt="First-launch walkthrough"> |
| Every model you have joins the council | A two-minute first-launch setup |
| <img src="docs/images/clarify.png" alt="The chair's clarifying questions"> | <img src="docs/images/answer-light.png" alt="A final answer"> |
| The chair clarifies first | One clear answer, bottom line first |
| <img src="docs/images/expert.png" alt="The Expert view with a diagram"> | <img src="docs/images/home-dark.png" alt="Home in dark mode"> |
| The Expert view adds a diagram | Dark mode |
| <img src="docs/images/metrics.png" alt="Behind the answer metrics"> | <img src="docs/images/activity.png" alt="Live Memory, CPU and GPU charts"> |
| Tokens, time and searches per model | Live Memory, CPU and GPU |
| <img src="docs/images/settings.png" alt="Settings"> | |
| Local and cloud providers | |

## Requirements

| | Minimum | Recommended |
|---|---|---|
| OS | macOS 12+, Linux or Windows 10+ | macOS on Apple Silicon |
| Memory | 8 GB | 16 GB+ (32 GB+ for 14B-class councils) |
| Disk | 10 GB free for models | 30 GB+ |
| [Ollama](https://ollama.com/download) | 0.5+ | latest |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) (Python 3.10+) | required | |
| [Node.js](https://nodejs.org) | 20+ | 22 LTS |
| [Docker](https://www.docker.com/products/docker-desktop/) (Compose 2.24+) | required for web search | Docker Desktop, 8 GB for its VM |

GPU acceleration comes from Ollama: Apple Silicon (Metal), NVIDIA (CUDA) and AMD (ROCm) all work. CPU-only machines
work too, just slower.

## Quick start

```bash
git clone https://github.com/dv333/quorum.git
cd quorum
./start.sh
```

On Windows, run `.\start.ps1` in PowerShell.

Open **http://localhost:5173**. `start.sh` also starts private web search in the background when Docker is running
(the first time downloads about 4 GB). On first launch a short walkthrough checks Ollama, downloads a starter council
sized for your machine, and confirms web search. Then ask your first conundrum.

<details>
<summary>Prefer to set things up by hand?</summary>

```bash
# 1. Models (three families that fit in 16 GB)
ollama pull qwen3:8b
ollama pull gemma3:4b
ollama pull llama3.2:3b

# 2. Backend (http://localhost:8002)
uv sync
uv run python -m backend.main

# 3. Frontend (http://localhost:5173), in a second terminal
cd frontend
npm install
npm run dev
```

</details>

## Web search (optional)

Beagle needs Firecrawl to search the web. `./start.sh` starts it for you when Docker is running; you can also manage
it yourself:

```bash
scripts/firecrawl.sh up      # first run downloads about 4 GB of images
scripts/firecrawl.sh down    # stop it
```

On Windows use `.\scripts\firecrawl.ps1 up`. Set `QUORUM_NO_WEB=1` to skip starting it.

It listens on `127.0.0.1:3002` only and searches through DuckDuckGo. You can also click **Start** in the walkthrough,
or pick **Firecrawl cloud** in *Settings → Web search* and paste an API key.

## Using Quorum

| To… | Do this |
|---|---|
| Ask | Type your conundrum and press Enter |
| Skip the chair's questions | **Skip, just start** |
| Change the council, chair or priorities | **Customize** under the question box |
| Look something up | `@Beagle what's the current 30-year mortgage rate?` |
| Talk to one agent | Type `@` and pick them, like `@Otter why rent?` |
| Copy a message or the answer | The copy button on each bubble |
| Steer a running debate | Type a message; the next agent sees it |
| Get the answer early | **Answer now** |
| Read it simpler or deeper | **Simple / Standard / Expert** on the answer |
| New conundrum | ⌘N / Ctrl+N |
| Hide the sidebar | ⌃⌘S |

## Configuration

Everything works out of the box. To change defaults, copy `.env.example` to `.env`:

| Variable | Default | Meaning |
|---|---|---|
| `OLLAMA_URL` | `http://localhost:11434` | Ollama server |
| `FIRECRAWL_URL` | `http://localhost:3002` | Self-hosted Firecrawl |
| `FIRECRAWL_API_KEY` | | Firecrawl cloud key (switches search to the cloud) |
| `LLC_MAX_ROUNDS` | `3` | Rounds when the chair doesn't size the debate itself |
| `LLC_NUM_CTX` | `8192` | Context window per model |
| `LLC_MEMORY_RESERVE_GB` | `8` | Memory kept free for your OS and apps |
| `LLC_REQUEST_TIMEOUT` | `600` | Seconds per model turn |

See [.env.example](.env.example) for the full list.

## Troubleshooting

<details>
<summary><b>"Can't reach Quorum's engine"</b></summary>

The backend isn't running. Start everything with `./start.sh`. If port 8002 is taken, stop the other program or set
`LLC_PORT` (and update `frontend/vite.config.js`).
</details>

<details>
<summary><b>No models found</b></summary>

Make sure Ollama is running (`ollama serve`, or open the Ollama app) and that you have at least one model
(`ollama list`). *Settings → Models* can download suggested models for you.
</details>

<details>
<summary><b>It's slow</b></summary>

Every installed model joins the council, reasoning models think before they speak, and a council that doesn't fit
in memory loads one model at a time. That's thorough but slow. Under *Customize* you can remove members or turn off
*Think*, and web research can be switched off. *Settings → Models* shows what fits in memory together.
</details>

<details>
<summary><b>Web search is unavailable</b></summary>

Check that Docker is running and `scripts/firecrawl.sh status` shows the containers up, then use *Settings → Web
search → Test*. The first start can take a minute.
</details>

<details>
<summary><b>The answer got a fact wrong</b></summary>

Answer quality is limited by the local models you run. Turn on web research so Beagle can fact-check, and choose your
largest model as chair. A bigger or more capable chair makes the biggest difference.
</details>

## How it works

A conundrum goes through **intake** (the chair clarifies), an **opening brief** (Beagle researches), **rounds** of
debate (each agent ends with a stance: agree, refine or disagree), a **fact-check**, and the chair's **final answer**.
Details are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), and the reasoning behind the design is in
[docs/DESIGN.md](docs/DESIGN.md).

## Development

```bash
uv run pytest                        # backend tests, no models needed
uvx ruff check backend tests         # lint
cd frontend && npm run build         # frontend build
```

README media are generated from the real app:

```bash
uv run --with playwright python scripts/screenshots.py --answer <debate-id>
uv run --with playwright python scripts/record_demo.py
uv run --with pillow python scripts/make_gifs.py
uv run python scripts/licenses.py    # refresh THIRD_PARTY_NOTICES.md
```

Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).

## Acknowledgements

- [llm-council](https://github.com/karpathy/llm-council) by Andrej Karpathy, the idea this started from
- [Ollama](https://ollama.com) for making local models easy
- [Firecrawl](https://github.com/firecrawl/firecrawl) for web search and page reading
- [Mermaid](https://mermaid.js.org) for diagrams

## License

[MIT](LICENSE) © 2026 Deepak Vijayan. Third-party licenses are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
