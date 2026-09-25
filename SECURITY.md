# Security

Quorum is designed to run on your own computer. A few things to keep in mind:

- The backend listens on `127.0.0.1` only and has no authentication. Don't expose ports 8002 or 5173 to a network.
- The self-hosted Firecrawl started by `scripts/firecrawl.sh` is bound to `127.0.0.1:3002` for the same reason.
- Cloud API keys you add are stored in the local SQLite database (`data/council.db`) and are never sent back to the
  browser. Protect that file like any other credential store, and never commit it.
- With web research on, your questions (as search queries) are sent to the search provider. With a cloud model in
  your council, the debate is sent to that provider.

## Reporting a vulnerability

Please don't open a public issue. Use GitHub's [private vulnerability reporting](https://github.com/dv333/quorum/security/advisories/new)
instead, and include steps to reproduce. I'll reply as soon as I can.
