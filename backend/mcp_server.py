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
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import cli

MAX_DIFF_CHARS = 60_000
MAX_FILE_CHARS = 20_000
MAX_FILES = 12
# All attached files together: every agent rereads them on every turn, and local models slow down (and run out of
# time) on very long prompts. About 10k tokens.
MAX_TOTAL_CHARS = 40_000
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


def read_files(paths: List[str], root: str = ".", budget: int = MAX_TOTAL_CHARS) -> str:
    """The files' contents as fenced blocks for the council, size-capped (see attach)."""
    return attach(paths, root, budget)[0]


def attach(
    paths: List[str], root: str = ".", budget: int = MAX_TOTAL_CHARS, coder_reads: bool = False
) -> Tuple[str, List[str]]:
    """The files for the council, and a manifest line per file saying what happened to it.

    Paths are relative to root, or absolute inside it; anything outside root, missing or binary is skipped. The first
    file is the primary one and is read first; the rest share what's left of the budget, so the total stays under
    `budget` characters. With coder_reads (a coding agent can read root), only the primary file is included and the
    others are listed for the Coder to read when the council asks. The list of files comes first, so an agent that
    only sees the start of the question still knows what's there."""
    base = Path(root).expanduser()
    left = budget
    listing, blocks, manifest = [], [], []
    for i, raw in enumerate(paths[:MAX_FILES]):
        p = Path(raw).expanduser()
        p = p if p.is_absolute() else base / p
        if not _within(p, base):
            manifest.append(f"{raw}: skipped, outside {base}")
            continue
        if not p.is_file():
            manifest.append(f"{raw}: skipped, not found")
            continue
        data = p.read_bytes()[: MAX_FILE_CHARS * 4]
        if b"\0" in data[:4096]:
            manifest.append(f"{raw}: skipped, binary")
            continue
        rel = os.path.relpath(p, base)
        text = data.decode("utf-8", errors="replace")
        if coder_reads and i > 0:
            listing.append(f"- {rel}: in the repository; ask @Coder about it")
            manifest.append(f"{rel}: left for the Coder to read ({len(text):,} characters)")
            continue
        room = min(MAX_FILE_CHARS, left)
        if room < 500:
            listing.append(f"- {rel}: not included (over the {budget:,}-character limit for attachments)")
            manifest.append(f"{rel}: not included, over the {budget:,}-character limit for all files")
            continue
        cut = len(text) > room
        text = text[:room]
        left -= len(text)
        listing.append(f"- {rel}{' (primary)' if i == 0 and len(paths) > 1 else ''}{' (cut)' if cut else ''}")
        manifest.append(
            f"{rel}: cut to {len(text):,} characters" if cut else f"{rel}: included ({len(text):,} characters)"
        )
        blocks.append(f"File {rel}{' (truncated)' if cut else ''}:\n```\n{text}\n```")
    if len(paths) > MAX_FILES:
        manifest.append(f"{len(paths) - MAX_FILES} more files not included (at most {MAX_FILES})")
    head = f"Files attached ({len(listing)}):\n" + "\n".join(listing) if len(listing) > 1 else ""
    skipped = [f"(skipped {m.replace(': skipped, ', ': ', 1)})" for m in manifest if ": skipped, " in m]
    return "\n\n".join(x for x in [head, *skipped, *blocks] if x), manifest


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

BACKEND_START_S = 45
LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "mcp-backend.log"


def backend_up() -> bool:
    try:
        return cli.get("/health").get("status") == "ok"
    except Exception:
        return False


def ensure_backend() -> Optional[str]:
    """Make sure the Quorum backend is running, starting it in the background when it's a local default install that
    isn't up yet. Returns a note for the user when it had to start it (the app itself isn't started)."""
    if backend_up():
        return None
    local = cli.API_URL.startswith(("http://127.0.0.1", "http://localhost"))
    if not local or os.getenv("QUORUM_MCP_AUTOSTART", "1") == "0":
        raise cli.QuorumError(f"Quorum isn't running at {cli.API_URL}. Start it with ./start.sh.")
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "ab") as log:
        subprocess.Popen(
            [sys.executable, "-m", "backend.main"],
            cwd=str(REPO),
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # keeps running for other clients after this MCP session ends
        )
    deadline = time.monotonic() + BACKEND_START_S
    while time.monotonic() < deadline:
        if backend_up():
            return (
                "(Quorum wasn't running, so its engine was started in the background. Run ./start.sh to open the "
                "app and watch debates live.)"
            )
        time.sleep(1)
    raise cli.QuorumError(f"Quorum's engine didn't start within {BACKEND_START_S} seconds; see {LOG_PATH}")


def app_up() -> bool:
    try:
        with urllib.request.urlopen(cli.APP_URL, timeout=2):
            return True
    except Exception:
        return False


def start(
    question: str,
    mode: str = "standard",
    research: Optional[bool] = None,
    pack: Optional[str] = None,
    repo_path: Optional[str] = None,
) -> str:
    """Start a conundrum and return its id. With a repository, the council can ask the Coder about the code."""
    settings = MODES.get(mode, MODES["standard"])
    body: Dict[str, Any] = {"question": question}
    if repo_path:
        body["repo_path"] = str(Path(repo_path).expanduser().resolve())
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
        elif d["status"] in ("cancelled", "failed"):
            return snap, False  # stopped for good: the user resumes it in the app
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
        "cancelled": "cancelled in the app",
        "failed": "stopped after an error",
        "concluded": "answered",
    }.get(d["status"], d["status"])


def respond(debate_id: str, snap: Dict[str, Any], done: bool, level: str = "standard") -> str:
    """The answer when it's ready; otherwise where the debate is and how to get the answer."""
    link = f"{cli.APP_URL}/#q/{debate_id}"
    watch = f"Watch it live: {link}" if app_up() else "Run ./start.sh to open the app and watch it live"
    if done:
        return f"{cli._result(debate_id, level, False, False).strip()}\n\nConundrum {debate_id} · {link}"
    if snap["debate"]["status"] in ("cancelled", "failed"):
        return f"Quorum {status_line(snap)}: {cli.stop_reason(snap)} Conundrum id: {debate_id} · {link}"
    return (
        f"Quorum is still on it ({status_line(snap)}). Conundrum id: {debate_id}. {watch}.\n"
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
    repo_path: str = "",
) -> str:
    # Files come from the repository the caller named, when there is one: the same read-only boundary the Coder gets
    from . import coder

    context, manifest = attach(
        files or [], repo_path or root, coder_reads=bool(repo_path) and coder.which() is not None
    )
    debate_id = start(f"{question.strip()}\n\n{context}".strip(), mode, research, repo_path=repo_path or None)
    snap, done = wait(debate_id, wait_seconds)
    return with_manifest(respond(debate_id, snap, done), manifest)


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
    context, manifest = attach(files or [], repo_path)
    if not diff and not context:
        return "Nothing to review: no uncommitted changes and no files given."
    debate_id = start(
        review_question(diff, context, focus, task), mode, research, pack="code-review", repo_path=repo_path
    )
    snap, done = wait(debate_id, wait_seconds)
    return with_manifest(respond(debate_id, snap, done), manifest)


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


def with_manifest(text: str, manifest: List[str]) -> str:
    """The reply, with what happened to each attached file when any was cut, skipped or left for the Coder."""
    if all(": included (" in m for m in manifest):
        return text
    return text + "\n\nAttached files:\n" + "\n".join(f"- {m}" for m in manifest)


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


def with_backend(fn, *args):
    """Run a tool after making sure the engine is up; errors come back as text the coding agent can act on."""
    try:
        note = ensure_backend()
        out = fn(*args)
    except cli.QuorumError as e:
        return f"Quorum couldn't do that: {e}"
    return f"{note}\n\n{out}" if note else out


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
        repo_path: str = "",
    ) -> str:
        """Ask Quorum's council of local models for a second opinion on a decision or question. files: paths to
        include as context (read locally, size-capped). repo_path: a repository the council's Coder (Claude Code or
        Codex, read-only) can read to answer questions about the code. mode: quick (one round, three models, usually
        answers within one call), standard or deep. research: let the council search the web and check its claims.
        Returns the answer, or the conundrum id and a link if it isn't done within wait_seconds (then call
        quorum_result)."""
        return await asyncio.to_thread(
            with_backend, ask, question, files, os.getcwd(), mode, research, wait_seconds, repo_path
        )

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
        line. The council gets specialists for what the change touches (security, performance, tests; database,
        network, concurrency and API when relevant), and its Coder can read repo_path to check the surrounding code.
        By default reviews everything not yet committed in repo_path (git diff HEAD, plus new files' names);
        base can be another ref ("main"), or "" to review only the given files. focus: all, security, tests or
        design. task: what the change was meant to do. Returns the review, or the id and a link to get it later."""
        return await asyncio.to_thread(
            with_backend, review, repo_path, base, files, focus, task, mode, False, wait_seconds
        )

    @server.tool()
    async def quorum_challenge(
        claim: str,
        files: Optional[List[str]] = None,
        mode: str = "standard",
        wait_seconds: int = 45,
    ) -> str:
        """Have Quorum's council argue against a plan, claim or decision: the strongest case against it, the better
        alternative, and whether it survives. Use it before committing to a design, to avoid reflexive agreement."""
        return await asyncio.to_thread(with_backend, challenge, claim, files, os.getcwd(), mode, True, wait_seconds)

    @server.tool()
    async def quorum_result(conundrum_id: str, wait_seconds: int = 45, level: str = "standard") -> str:
        """Get a Quorum answer by conundrum id, waiting up to wait_seconds if the council is still debating. level:
        simple, standard or expert (expert adds detail and a diagram)."""
        return await asyncio.to_thread(with_backend, result, conundrum_id, wait_seconds, level)

    @server.tool()
    async def quorum_followup(conundrum_id: str, message: str, wait_seconds: int = 45) -> str:
        """Ask a follow-up in an existing conundrum; the council continues with everything it already discussed."""
        return await asyncio.to_thread(with_backend, followup, conundrum_id, message, wait_seconds)

    @server.tool()
    async def quorum_list(limit: int = 10) -> str:
        """List recent conundrums with their ids and status."""
        return await asyncio.to_thread(with_backend, recent, limit)

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


# ------------------------------------------------------------------ diagnostics


def doctor(start: bool = False) -> Tuple[str, bool]:
    """Check what the MCP tools need, with a fix for each problem. Returns the report and whether all is well."""
    from . import coder

    lines: List[str] = []
    ok = True

    def check(passed: bool, good: str, bad: str, fix: str = "", warn_only: bool = False) -> None:
        nonlocal ok
        if passed:
            lines.append(f"✓ {good}")
        else:
            lines.append(f"{'!' if warn_only else '✗'} {bad}" + (f"\n    fix: {fix}" if fix else ""))
            ok = ok and warn_only

    check(bool(shutil.which("uv")), "uv is installed", "uv isn't installed (clients start Quorum with it)",
          "curl -LsSf https://astral.sh/uv/install.sh | sh")  # fmt: skip
    up = backend_up()
    if not up and start:
        try:
            ensure_backend()
            up = True
        except cli.QuorumError:
            up = False
    check(up, f"Quorum's engine is running at {cli.API_URL}",
          f"Quorum's engine isn't running at {cli.API_URL} (the MCP tools start it on first use)",
          "./start.sh, or quorum mcp doctor --start", warn_only=True)  # fmt: skip
    if up:
        try:
            inv = cli.get("/inventory")
            fits = [m for m in inv.get("models", []) if m.get("local") and m.get("fit") == "fits"]
            check(len(fits) >= 3, f"{len(fits)} local models fit in memory",
                  f"only {len(fits)} local model(s) fit in memory; three or more make a real council",
                  "Settings → Models → Get more, or: ollama pull qwen3:8b")  # fmt: skip
        except cli.QuorumError as e:
            check(False, "", f"couldn't list models: {e}", "is Ollama running? ollama serve")
        try:
            research = cli.get("/research/status")
            check(bool(research.get("ready")), "web search is ready",
                  "web search isn't available, so answers won't be checked against sources",
                  "Settings → Web search", warn_only=True)  # fmt: skip
        except cli.QuorumError:
            pass
    cli_name = coder.which()
    check(bool(cli_name), f"the Coder can use {coder.label(cli_name)} (read-only) in code debates",
          "no Claude Code or Codex found, so code debates won't have the Coder",
          "install Claude Code or Codex and sign in once (run claude, then /login)", warn_only=True)  # fmt: skip
    if shutil.which("claude"):
        try:
            listed = subprocess.run(["claude", "mcp", "list"], capture_output=True, text=True, timeout=30).stdout
            check("quorum" in listed.lower(), "Claude Code has the quorum MCP server",
                  "Claude Code doesn't have the quorum MCP server", "quorum mcp install --client claude --apply",
                  warn_only=True)  # fmt: skip
        except (subprocess.SubprocessError, OSError):
            lines.append("! couldn't ask Claude Code which MCP servers it has")
    codex_config = Path(os.getenv("CODEX_HOME", "~/.codex")).expanduser() / "config.toml"
    if shutil.which("codex") or codex_config.exists():
        has = codex_config.exists() and "[mcp_servers.quorum]" in codex_config.read_text()
        check(has, "Codex has the quorum MCP server", f"Codex doesn't have the quorum MCP server ({codex_config})",
              "quorum mcp install --client codex --apply", warn_only=True)  # fmt: skip
    lines.append(
        "· ChatGPT isn't supported yet: it only connects to MCP servers at a public HTTPS address, and Quorum's "
        "server runs on your computer (stdio)."
    )
    return "\n".join(lines), ok
