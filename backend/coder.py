"""Coder: a coding agent (Claude Code or Codex) that reads the conundrum's repository and answers the council's
questions about it, read-only.

Council members write "@Coder: where is auth handled?"; Quorum runs the coding agent headless in the repo with only
read and search tools, then checks every file:line it cites against the files. If the repository changes during the
call (it shouldn't: no write tools are allowed), the answer is discarded.

Which agent: QUORUM_CODER=claude|codex|off (default: Claude Code if installed, else Codex).
"""

import asyncio
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

CODER_NAME = "Coder"
TIMEOUT_S = float(os.getenv("QUORUM_CODER_TIMEOUT", "240"))
MAX_ANSWER_CHARS = 6000
LABEL = {"claude": "Claude Code", "codex": "Codex"}

# Read and search only: Claude Code in print mode denies every tool not listed
CLAUDE_TOOLS = "Read,Grep,Glob"


class CoderError(Exception):
    pass


def which() -> Optional[str]:
    """The coding agent to use, or None when none is installed or it's turned off."""
    choice = os.getenv("QUORUM_CODER", "auto").strip().lower()
    if choice == "off":
        return None
    for cli in ["claude", "codex"] if choice == "auto" else [choice]:
        if cli in LABEL and shutil.which(cli):
            return cli
    return None


def label(cli: Optional[str]) -> str:
    return LABEL.get(cli or "", "coding agent")


def valid_repo(path: Optional[str]) -> Optional[str]:
    """The absolute path when it's an existing directory, else None."""
    if not path:
        return None
    p = Path(path).expanduser()
    return str(p.resolve()) if p.is_dir() else None


BRIEF = """Read the parts of this repository that matter for the question below, then brief a council of AI agents
who are about to debate it but can't see the code. Say how the relevant code works today, the key files and
functions, and any constraints or risks that bear on the question. Cite every claim about the code as path:line
(for example backend/engine.py:120). Under 250 words. Don't change anything; only read.

Question: {question}"""

ASK = """A council of AI agents is debating this question: {question}

One of them ({asked_by}) asks you about the code in this repository: {request}

Answer from the code itself, citing path:line for every claim (for example backend/engine.py:120). If the code
doesn't settle it, say so. Under 200 words. Don't change anything; only read."""


def brief_prompt(question: str) -> str:
    return BRIEF.format(question=question.strip()[:2000])


def ask_prompt(question: str, request: str, asked_by: Optional[str]) -> str:
    return ASK.format(question=question.strip()[:1500], request=request.strip(), asked_by=asked_by or "the user")


# A reasoning effort every Codex version understands, for when the user's config names one this Codex doesn't
CODEX_SAFE_EFFORT = "high"
_BAD_CONFIG = re.compile(r"config\.toml|unknown variant|expected one of", re.I)


def command(cli: str, prompt: str, repo: str, effort: Optional[str] = None) -> List[str]:
    if cli == "claude":
        return ["claude", "-p", prompt, "--allowedTools", CLAUDE_TOOLS, "--output-format", "text", "--max-turns", "20"]
    if cli == "codex":
        # effort overrides the user's config for this one run only; their config file is never changed
        override = ["-c", f'model_reasoning_effort="{effort}"'] if effort else []
        return ["codex", "exec", "--sandbox", "read-only", "--skip-git-repo-check", *override, "-C", repo, prompt]
    raise CoderError(f"Unknown coding agent: {cli}")


async def _git_state(repo: str) -> Optional[str]:
    """What the working tree looks like (HEAD plus status); None outside a git repository."""
    if not shutil.which("git"):
        return None
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", repo, "status", "--porcelain=v1", "--untracked-files=all", "-z",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        return None
    head = await asyncio.create_subprocess_exec(
        "git", "-C", repo, "rev-parse", "HEAD", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )  # fmt: skip
    rev, _ = await head.communicate()
    return rev.decode().strip() + "\n" + out.decode(errors="replace")


async def run(cli: str, prompt: str, repo: str, timeout: float = TIMEOUT_S) -> Tuple[str, Dict[str, Any]]:
    """Run the coding agent headless in the repo and return its answer and run details. Raises CoderError when it
    fails, times out, or changes the repository."""
    before = await _git_state(repo)
    started = time.monotonic()
    code, out, detail = await _exec(command(cli, prompt, repo), repo, cli, timeout)
    if code != 0 and cli == "codex" and _BAD_CONFIG.search(detail):
        # An older Codex can't read a setting in the user's config (e.g. model_reasoning_effort = "ultra"): try once
        # with a value it knows, for this run only
        code, out, detail = await _exec(command(cli, prompt, repo, CODEX_SAFE_EFFORT), repo, cli, timeout)
        if code != 0 and _BAD_CONFIG.search(detail):
            raise CoderError(
                f"Codex can't load its settings (~/.codex/config.toml): {detail[-200:]}. Fix that setting or update "
                "Codex, or set QUORUM_CODER=claude to use Claude Code instead"
            )
    if code != 0:
        if re.search(r"not logged in|/login|unauthori[sz]ed|authenticat", detail, re.I):
            raise CoderError(f"{label(cli)} isn't signed in: run `{cli}` in a terminal and sign in, then ask again")
        raise CoderError(f"{label(cli)} failed: {detail or f'exit code {code}'}")
    after = await _git_state(repo)
    if before is not None and after != before:
        raise CoderError(f"{label(cli)} changed the repository, so its answer was discarded; check `git status`")
    text = out.decode(errors="replace").strip()
    if len(text) > MAX_ANSWER_CHARS:
        text = text[:MAX_ANSWER_CHARS] + "…"
    return text, {"cli": cli, "duration_ms": int((time.monotonic() - started) * 1000)}


async def _exec(cmd: List[str], repo: str, cli: str, timeout: float) -> Tuple[int, bytes, str]:
    """Run one command in the repo: its exit code, output, and the tail of its error text."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=repo,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        raise CoderError(f"{label(cli)} isn't installed")
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise CoderError(f"{label(cli)} didn't answer within {int(timeout)} seconds")
    except asyncio.CancelledError:
        proc.kill()
        raise
    detail = (err.decode(errors="replace").strip() or out.decode(errors="replace").strip())[-400:]
    return proc.returncode, out, detail


# ------------------------------------------------------------------ checking what it cites

_REF = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.[A-Za-z0-9]{1,8}):(\d+)(?:[-–](\d+))?")


def check_refs(text: str, repo: str) -> Dict[str, List[str]]:
    """Every path:line (or path:start-end) the answer cites: found when the file is inside the repo and has that
    many lines, missing otherwise."""
    root = Path(repo).resolve()
    found: List[str] = []
    missing: List[str] = []
    lines_cache: Dict[str, int] = {}
    for m in _REF.finditer(text):
        ref = m.group(0)
        if ref in found or ref in missing:
            continue
        path = (root / m.group(1)).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            missing.append(ref)
            continue
        if not path.is_file():
            missing.append(ref)
            continue
        key = str(path)
        if key not in lines_cache:
            with open(path, "rb") as f:
                lines_cache[key] = sum(1 for _ in f)
        last = int(m.group(3) or m.group(2))
        (found if 1 <= int(m.group(2)) <= last <= lines_cache[key] + 1 else missing).append(ref)
    return {"found": found, "missing": missing}


def with_ref_note(text: str, refs: Dict[str, List[str]]) -> str:
    """The answer, noting any citation that doesn't match the files."""
    if not refs["missing"]:
        return text
    return text + "\n\n_Couldn't find in the repository: " + ", ".join(f"`{r}`" for r in refs["missing"][:8]) + "_"
