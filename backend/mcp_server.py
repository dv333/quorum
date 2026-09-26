"""Quorum as an MCP server: Claude Code, Codex and other MCP clients ask the local council for a second opinion, a
review of a change, or the strongest case against a plan.

    quorum mcp                           # serve over stdio (what the MCP client runs)
    quorum mcp install --client claude   # print (or --apply) the setup for Claude Code or Codex

Debates take minutes and MCP clients cancel long tool calls, so tools that start a debate return right away with
the conundrum's id, a link to watch it and its status, and wait only as long as asked; quorum_result picks the answer
up later. The server talks to the running Quorum app over HTTP and only reads: it never writes files, runs code or
changes git state. See docs/design/mcp-server.md.
"""

import asyncio
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import cli

MAX_DIFF_CHARS = 60_000
MAX_FILE_CHARS = 20_000
MAX_FILES = 12
MAX_WAIT = 280  # seconds a single tool call may wait for an answer
LIVE = ("intake", "clarifying", "confirming", "running", "paused", "concluding", "researching")
MODES = {
    "quick": {"max_rounds": 1, "seats": 3},  # a small council, one round: usually answers within one call
    "standard": {},  # the chair decides how many rounds
    "deep": {"max_rounds": 5},
}
FOCUS = {
    "all": "correctness, security, tests, edge cases and design",
    "security": "security: injection, auth, secrets, unsafe input handling, permissions",
    "tests": "tests: what the change leaves untested, and tests that would catch its bugs",
    "design": "design: simpler structure, naming, coupling, and whether this is the right change",
}

INSTRUCTIONS = """Quorum is a council of local AI models that debate a question over several rounds, check the
claims they rely on against web sources, and write one answer that keeps the strongest dissent.

Use it for a second opinion on a decision (quorum_ask), a review of the current change (quorum_review), or the
strongest case against a plan or claim (quorum_challenge). A debate takes minutes: these tools return after at most
wait_seconds with either the answer or the conundrum's id and a link to watch it; call quorum_result with the id to
get the answer later. mode="quick" (one round, three models) usually answers within one call. The user can watch
every debate live in the Quorum app."""


# ------------------------------------------------------------------ inputs: files and diffs, read locally


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def read_files(paths: List[str], root: str = ".") -> str:
    """The files' contents as fenced blocks for the council, size-capped; paths are relative to root (or absolute
    inside it). Missing, binary and outside-root files are noted, not read."""
    base = Path(root).expanduser()
    blocks = []
    for raw in paths[:MAX_FILES]:
        p = Path(raw).expanduser()
        p = p if p.is_absolute() else base / p
        if not _within(p, base):
            blocks.append(f"(skipped {raw}: outside {base})")
            continue
        if not p.is_file():
            blocks.append(f"(skipped {raw}: not found)")
            continue
        data = p.read_bytes()[: MAX_FILE_CHARS * 4]
        if b"\0" in data[:4096]:
            blocks.append(f"(skipped {raw}: binary)")
            continue
        text = data.decode("utf-8", errors="replace")
        cut = len(text) > MAX_FILE_CHARS
        text = text[:MAX_FILE_CHARS]
        rel = os.path.relpath(p, base)
        blocks.append(f"File {rel}{' (truncated)' if cut else ''}:\n```\n{text}\n```")
    if len(paths) > MAX_FILES:
        blocks.append(f"({len(paths) - MAX_FILES} more files not included)")
    return "\n\n".join(blocks)


def git_diff(repo: str = ".", base: str = "HEAD") -> str:
    """The change to review: `git diff <base>` (by default everything not yet committed, staged or not), plus the
    names of new untracked files. Size-capped; says so when cut."""
    root = Path(repo).expanduser()
    if not shutil.which("git"):
        raise cli.QuorumError("git isn't installed")

    def git(*args: str) -> str:
        out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            raise cli.QuorumError(out.stderr.strip() or f"git {' '.join(args)} failed")
        return out.stdout

    diff = git("diff", base, "--")
    untracked = [line for line in git("ls-files", "--others", "--exclude-standard").splitlines() if line]
    if untracked:
        diff += "\n" + "\n".join(f"(new file, not yet tracked: {f})" for f in untracked[:50])
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + f"\n… (diff cut at {MAX_DIFF_CHARS} characters)"
    return diff.strip()


# ------------------------------------------------------------------ questions for the council


def review_question(diff: str, files: str, focus: str, task: str) -> str:
    what = f" The change was made to: {task.strip()}." if task.strip() else ""
    parts = [
        f"Review this code change.{what} Focus on {FOCUS.get(focus, FOCUS['all'])}.",
        "List findings by severity: High (bugs, security or data-loss risks that must be fixed), Medium (should be "
        "fixed), Low (nice to have). Give each one the file and line it's about and a concrete fix. Say which parts "
        "look correct, and don't invent problems that aren't there.",
    ]
    if diff:
        parts.append(f"Diff:\n```diff\n{diff}\n```")
    if files:
        parts.append(files)
    return "\n\n".join(parts)


def challenge_question(claim: str, files: str) -> str:
    parts = [
        f"Challenge this: {claim.strip()}",
        "Argue the strongest case against it: what's wrong, risky or missing, and what the better alternative is. "
        "Then say whether it survives the challenge, and what evidence would change the council's mind.",
    ]
    if files:
        parts.append(files)
    return "\n\n".join(parts)


# ------------------------------------------------------------------ talking to the running app


def start(question: str, mode: str = "standard", research: Optional[bool] = None, pack: Optional[str] = None) -> str:
    """Start a conundrum and return its id."""
    settings = MODES.get(mode, MODES["standard"])
    body: Dict[str, Any] = {"question": question}
    if pack:
        body["pack"] = pack
    if research is not None:
        body["research_enabled"] = research
    if "max_rounds" in settings:
        body["max_rounds"] = settings["max_rounds"]
    if "seats" in settings:
        council = cli.get("/auto-council")
        seats = (council.get("seats") or [])[: settings["seats"]]
        if seats:
            body["seats"] = [{"endpoint_id": s["endpoint_id"], "model": s["model"]} for s in seats]
    return cli.post("/debates", body)["debate"]["id"]


def wait(debate_id: str, seconds: float) -> Tuple[Dict[str, Any], bool]:
    """Wait up to `seconds` for the answer, skipping the chair's clarifying questions (nobody is there to answer
    them). Returns the latest snapshot and whether the answer is ready."""
    deadline = time.monotonic() + max(0.0, min(seconds, MAX_WAIT))
    handled: Optional[int] = None
    quiet = cli.Progress(quiet=True)
    while True:
        snap = cli.get(f"/debates/{debate_id}")
        d = snap["debate"]
        if d["status"] == "concluded" and cli._verdict(snap, d["topic"]):
            return snap, True
        if d["status"] in ("clarifying", "confirming"):
            last = max((m["id"] for m in snap["messages"] if m["author_kind"] == "moderator"), default=0)
            if handled != last:
                handled = last
                cli._answer_intake(debate_id, snap, interactive=False, progress=quiet)
        elif d["status"] == "paused":
            cli.post(f"/debates/{debate_id}/continue")
        if time.monotonic() >= deadline:
            return snap, False
        time.sleep(cli.POLL_SECONDS)


def status_line(snap: Dict[str, Any]) -> str:
    d = snap["debate"]
    seats = len(snap.get("seats") or [])
    spoken = len(
        [
            m
            for m in snap["messages"]
            if m["topic"] == d["topic"]
            and m["round"] == d["round"]
            and m["author_kind"] == "seat"
            and m["status"] == "done"
        ]
    )
    if d["status"] == "running" and d["round"]:
        return f"debating: round {d['round']} of {d['max_rounds']}, {spoken} of {seats} have spoken"
    return {
        "intake": "reading the question",
        "clarifying": "starting",
        "confirming": "starting",
        "researching": "researching",
        "running": "starting the debate",
        "concluding": "fact-checking and writing the answer",
        "paused": "paused",
        "concluded": "answered",
    }.get(d["status"], d["status"])


def respond(debate_id: str, snap: Dict[str, Any], done: bool, level: str = "standard") -> str:
    """The answer when it's ready; otherwise where the debate is and how to get the answer."""
    link = f"{cli.APP_URL}/#q/{debate_id}"
    if done:
        return f"{cli._result(debate_id, level, False, False).strip()}\n\nConundrum {debate_id} · {link}"
    return (
        f"Quorum is still on it ({status_line(snap)}). Conundrum id: {debate_id}. Watch it live: {link}\n"
        f'Call quorum_result with conundrum_id="{debate_id}" (and wait_seconds) to get the answer when it\'s ready.'
    )


# ------------------------------------------------------------------ the tools


def ask(
    question: str,
    files: Optional[List[str]] = None,
    root: str = ".",
    mode: str = "standard",
    research: bool = True,
    wait_seconds: int = 45,
) -> str:
    context = read_files(files or [], root)
    debate_id = start(f"{question.strip()}\n\n{context}".strip(), mode, research)
    snap, done = wait(debate_id, wait_seconds)
    return respond(debate_id, snap, done)


def review(
    repo_path: str = ".",
    base: str = "HEAD",
    files: Optional[List[str]] = None,
    focus: str = "all",
    task: str = "",
    mode: str = "standard",
    research: bool = False,
    wait_seconds: int = 45,
) -> str:
    diff = git_diff(repo_path, base) if base else ""
    context = read_files(files or [], repo_path)
    if not diff and not context:
        return "Nothing to review: no uncommitted changes and no files given."
    debate_id = start(review_question(diff, context, focus, task), mode, research, pack="code-review")
    snap, done = wait(debate_id, wait_seconds)
    return respond(debate_id, snap, done)


def challenge(
    claim: str,
    files: Optional[List[str]] = None,
    root: str = ".",
    mode: str = "standard",
    research: bool = True,
    wait_seconds: int = 45,
) -> str:
    debate_id = start(challenge_question(claim, read_files(files or [], root)), mode, research)
    snap, done = wait(debate_id, wait_seconds)
    return respond(debate_id, snap, done)


def result(conundrum_id: str, wait_seconds: int = 45, level: str = "standard") -> str:
    snap, done = wait(conundrum_id, wait_seconds)
    return respond(conundrum_id, snap, done, level if level in ("simple", "standard", "expert") else "standard")


def followup(conundrum_id: str, message: str, wait_seconds: int = 45) -> str:
    cli.post(f"/debates/{conundrum_id}/messages", {"content": message})
    time.sleep(1)  # let the new topic start before waiting on it
    snap, done = wait(conundrum_id, wait_seconds)
    return respond(conundrum_id, snap, done)


def recent(limit: int = 10) -> str:
    rows = cli.get(f"/debates?limit={max(1, min(limit, 50))}")
    if not rows:
        return "No conundrums yet."
    lines = []
    for d in rows:
        title = d.get("title") or (d.get("question") or "")[:80]
        lines.append(f"- {d['id']} · {d['status']} · {title}")
    return "\n".join(lines)


def build_server():
    """The MCP server with Quorum's tools (imported lazily so the rest of Quorum doesn't need the MCP SDK)."""
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("quorum", instructions=INSTRUCTIONS)

    @server.tool()
    async def quorum_ask(
        question: str,
        files: Optional[List[str]] = None,
        mode: str = "standard",
        research: bool = True,
        wait_seconds: int = 45,
    ) -> str:
        """Ask Quorum's council of local models for a second opinion on a decision or question. files: paths to
        include as context (read locally, size-capped). mode: quick (one round, three models, usually answers within
        one call), standard or deep. research: let the council search the web and check its claims. Returns the
        answer, or the conundrum id and a link if it isn't done within wait_seconds (then call quorum_result)."""
        return await asyncio.to_thread(ask, question, files, os.getcwd(), mode, research, wait_seconds)

    @server.tool()
    async def quorum_review(
        repo_path: str = ".",
        base: str = "HEAD",
        files: Optional[List[str]] = None,
        focus: str = "all",
        task: str = "",
        mode: str = "standard",
        wait_seconds: int = 45,
    ) -> str:
        """Have Quorum's council review a code change and list findings by severity (High, Medium, Low) with file and
        line. By default reviews everything not yet committed in repo_path (git diff HEAD, plus new files' names);
        base can be another ref ("main"), or "" to review only the given files. focus: all, security, tests or
        design. task: what the change was meant to do. Returns the review, or the id and a link to get it later."""
        return await asyncio.to_thread(review, repo_path, base, files, focus, task, mode, False, wait_seconds)

    @server.tool()
    async def quorum_challenge(
        claim: str,
        files: Optional[List[str]] = None,
        mode: str = "standard",
        wait_seconds: int = 45,
    ) -> str:
        """Have Quorum's council argue against a plan, claim or decision: the strongest case against it, the better
        alternative, and whether it survives. Use it before committing to a design, to avoid reflexive agreement."""
        return await asyncio.to_thread(challenge, claim, files, os.getcwd(), mode, True, wait_seconds)

    @server.tool()
    async def quorum_result(conundrum_id: str, wait_seconds: int = 45, level: str = "standard") -> str:
        """Get a Quorum answer by conundrum id, waiting up to wait_seconds if the council is still debating. level:
        simple, standard or expert (expert adds detail and a diagram)."""
        return await asyncio.to_thread(result, conundrum_id, wait_seconds, level)

    @server.tool()
    async def quorum_followup(conundrum_id: str, message: str, wait_seconds: int = 45) -> str:
        """Ask a follow-up in an existing conundrum; the council continues with everything it already discussed."""
        return await asyncio.to_thread(followup, conundrum_id, message, wait_seconds)

    @server.tool()
    async def quorum_list(limit: int = 10) -> str:
        """List recent conundrums with their ids and status."""
        return await asyncio.to_thread(recent, limit)

    return server


def serve() -> None:
    build_server().run("stdio")


# ------------------------------------------------------------------ setup for Claude Code and Codex

REPO = Path(__file__).resolve().parent.parent


def launch_command() -> List[str]:
    """How an MCP client starts this server: through uv, from this checkout."""
    return ["uv", "run", "--directory", str(REPO), "quorum", "mcp"]


def claude_setup() -> List[str]:
    return ["claude", "mcp", "add", "--scope", "user", "quorum", "--", *launch_command()]


def codex_setup() -> str:
    cmd = launch_command()
    args = ", ".join(f'"{a}"' for a in cmd[1:])
    return (
        "[mcp_servers.quorum]\n"
        f'command = "{cmd[0]}"\n'
        f"args = [{args}]\n"
        "# Debates take minutes; the tools wait up to wait_seconds, so allow a little more than that\n"
        "tool_timeout_sec = 300\n"
    )


def install(client: str, apply: bool) -> str:
    """The setup for a client, applied or just printed."""
    if client == "claude":
        cmd = claude_setup()
        if not apply:
            return "Run this to add Quorum to Claude Code:\n\n  " + " ".join(cmd)
        if not shutil.which("claude"):
            raise cli.QuorumError("The claude command isn't installed; run: quorum mcp install --client claude")
        out = subprocess.run(cmd, capture_output=True, text=True)
        if out.returncode != 0:
            raise cli.QuorumError(out.stderr.strip() or out.stdout.strip() or "claude mcp add failed")
        return "Added Quorum to Claude Code. Start a new session and ask it to use quorum_review or quorum_ask."
    if client == "codex":
        block = codex_setup()
        path = Path(os.getenv("CODEX_HOME", "~/.codex")).expanduser() / "config.toml"
        if not apply:
            return f"Add this to {path}:\n\n{block}"
        existing = path.read_text() if path.exists() else ""
        if "[mcp_servers.quorum]" in existing:
            return f"Quorum is already in {path}."
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(existing.rstrip() + ("\n\n" if existing.strip() else "") + block)
        return f"Added Quorum to {path}. Start a new Codex session to use it."
    raise cli.QuorumError(f"Unknown client '{client}': use claude or codex")
