# Quorum as an MCP server for coding agents

Claude Code, Codex and other MCP clients should be able to call Quorum the way they call
[PAL MCP](https://github.com/BeehiveInnovations/pal-mcp-server): ask the local council for a second opinion, a
review of a change, or an attack on a plan, without leaving the coding session. This note compares PAL's features
with what Quorum already has and sets out what to build.

## What PAL MCP offers, and where Quorum stands

| PAL MCP | What it does | Quorum today | Gap |
|---|---|---|---|
| MCP server | Tools Claude Code / Codex call from a session; `uvx` one-liner that configures the clients | REST API and `quorum ask` CLI | **An MCP server and an install command** |
| `consensus` | Several models answer a proposal one at a time, each with a stance (for, against, neutral); the calling agent combines them | Multi-round debate, required roles (Skeptic, Pragmatist, User advocate), blind round 1, chair synthesis, fact-checked claims, preserved dissent | Only the entry point. Quorum's debate goes further than PAL's one-shot stances |
| `challenge`, `thinkdeep` | Critical analysis against reflexive agreement; deeper reasoning | Skeptic role; debates can run several rounds | A "challenge" mode: the council argues against a claim or plan |
| `codereview`, `precommit` | Severity-ranked findings on files or a diff | A code-review pack | **Take a diff or files as input** and return findings by severity |
| `chat` | Quick second opinion from one model | Debates use the whole council; a "direct" answer exists for simple questions | A quick mode (one round, a few models) |
| `apilookup` | Current API and SDK docs | Beagle's web research with verified quotes | Covered |
| `clink` | Spawns Codex, Gemini or Claude CLIs as subagents | None | Later: the "@Repo" agent (Quorum asks a coding agent, read-only) |
| `continuation_id` | Carries a conversation across tools and models | Debates persist; follow-up questions continue a debate | Follow-ups through the MCP tools |
| Large prompts | Works around MCP's ~25K token limit by passing files, not text | n/a | Tools take a repo path and file paths, and Quorum reads the diff and files itself |
| Providers, auto model choice | Gemini, OpenAI, xAI, Ollama, OpenRouter, Azure; auto mode ranks models | Ollama plus OpenAI-compatible providers; auto council; the chair is picked | Covered |
| `debug`, `planner`, `refactor`, `testgen`, `docgen`, `tracer`, `secaudit`, `analyze` | One model doing a coding task | Not Quorum's job; the coding agent does these better | Skip, except security review as a review focus |
| Configuration | `DISABLED_TOOLS`, `DEFAULT_MODEL` | Settings in the app | Tool list in the install command |

PAL is Apache 2.0 licensed. Quorum doesn't copy its code; this is Quorum's own implementation of the same idea.

**What Quorum adds that PAL doesn't:** a real debate (agents answer each other over several rounds), claims checked
against sources with exact quotes, preserved dissent, local models by default (no API cost), and a live page where
the user can watch the council, with a link from every tool result.

## Design

**Transport.** A stdio MCP server, `quorum mcp`, built on the official Python SDK (`mcp` 2.x, `MCPServer`). It talks to
the running Quorum backend over HTTP, like the CLI, so the app, the CLI and the coding agent share the same
conundrums.

**Debates are slow; MCP calls time out.** A debate takes minutes, and clients cancel long tool calls (Codex's default
tool timeout is 60 seconds). Every tool that starts a debate returns at once with the conundrum's id, a link to watch
it live, and its status; `quorum_result` waits up to a given number of seconds and returns the answer when it's
ready. A `quick` mode (one round, a small council) usually answers within a single call.

**Tools (first version)**

| Tool | Input | Returns |
|---|---|---|
| `quorum_ask` | question, optional file paths, mode (`quick`, `standard`, `deep`), research on or off, seconds to wait | id, link, status, and the answer if it finished in time |
| `quorum_review` | repo path, base ref (default: uncommitted changes), optional files, focus (all, security, tests, design), seconds to wait | findings by severity (high, medium, low) with file references, and the council's overall verdict |
| `quorum_challenge` | a claim, plan or decision, optional files | the strongest case against it and what would change the council's mind |
| `quorum_result` | id, seconds to wait, level (simple, standard, expert) | the answer with its checked evidence, or the current status |
| `quorum_followup` | id, message | continues the same conundrum |
| `quorum_list` | how many | recent conundrums with status |

Files and diffs are read by the server from the paths given (size-capped), so large inputs never pass through the
MCP message.

**Install.** `quorum mcp install --client claude` runs `claude mcp add`; `--client codex` adds a
`[mcp_servers.quorum]` entry (with a longer tool timeout) to `~/.codex/config.toml`. Both can also be printed instead
of applied.

**Safety.** The MCP tools only read: they never write files, run the user's code or change git state.

## Measuring it

- **Review quality:** replay about 20 real commits that later needed a bug-fix commit, and count how many
  `quorum_review` findings are real (precision) and how many of the known bugs it flags (recall). Below about 50%
  precision, reviews aren't worth their noise.
- **Latency:** time to a first answer in `quick` and `standard` modes.
- **Use:** how often the coding agent acts on Quorum's feedback.

## Later

- The **@Repo agent** (PAL's `clink` idea, reversed): council members ask a read-only coding agent about the codebase
  mid-debate, with every `file:line` it cites checked.
- A **Claude Code Stop hook** that sends the session's diff to `quorum_review` in the background and reports findings
  on the next turn, not blocking the session.
