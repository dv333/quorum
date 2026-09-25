# Contributing to Quorum

Thanks for your interest! Bug reports, ideas and pull requests are all welcome.

## Getting set up

You'll need [uv](https://docs.astral.sh/uv/), Node.js 20+ and [Ollama](https://ollama.com) with at least two models.

```bash
git clone https://github.com/dv333/quorum.git
cd quorum
./start.sh
```

The backend runs on http://localhost:8002 and the app on http://localhost:5173 with hot reload.

## Before you open a pull request

```bash
uv run pytest            # backend tests (no models needed)
uv run python scripts/replay.py --all   # replay known failures against your running Quorum (real models, slow)
uvx ruff check backend tests
uvx ruff format backend tests
cd frontend && npm run build
```

- Keep pull requests focused; one change per PR is easiest to review.
- Add or update tests for engine behaviour (`tests/test_engine.py` has a scripted fake model server).
- Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) first; it lists the engine rules that are easy to break.
- UI changes: check light and dark mode, and phone, tablet and desktop widths. Screenshots in the PR help a lot.

## Reporting bugs

Please include your OS, hardware (RAM / GPU), the models in your council, and what you expected versus what happened.
The *Behind the answer* panel and the debate's search log are often useful context.

## Code of conduct

This project follows the [Code of Conduct](CODE_OF_CONDUCT.md). Be kind.
