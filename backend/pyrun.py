"""Python checks: runs the short programs agents write to check a calculation, in a sandbox.

An agent (or the chair) writes "@Python:" and a ```python block; Quorum runs it and hands back what it printed, so a
count, a date or a program's output is computed rather than guessed. The program can't reach the network, write outside
its own temporary folder or start other programs, and it gets a few seconds of CPU.

Sandbox: sandbox-exec on macOS, bubblewrap (bwrap) on Linux. Without one (Windows, or Linux without bwrap) the checks
are off. QUORUM_PYTHON=off turns them off everywhere.
"""

import asyncio
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import List, Optional

TIMEOUT_S = float(os.getenv("QUORUM_PYTHON_TIMEOUT", "10"))
MAX_OUTPUT_CHARS = 3000
MAX_CODE_CHARS = 6000

_REQUEST = re.compile(r"@Python\s*:?\s*```(?:python|py)?[^\n]*\n(.*?)```", re.I | re.S)

# Installed before the agent's code runs: a second fence inside the sandbox, not the main one
_GUARD = r"""
import sys

def _guard(event, args):
    if event.startswith(("socket.", "subprocess.", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork",
                         "ctypes.", "os.remove", "os.rmdir", "os.rename", "shutil.rmtree", "os.unlink")):
        raise PermissionError(f"not allowed in a Python check: {event}")
    if event == "open" and len(args) > 1 and isinstance(args[1], str) and any(c in args[1] for c in "wax+"):
        raise PermissionError("not allowed in a Python check: writing files")

sys.addaudithook(_guard)
del _guard
with open(sys.argv[1]) as _f:
    _code = _f.read()
exec(compile(_code, "check.py", "exec"), {"__name__": "__main__"})
"""


def _macos_profile(workdir: str) -> str:
    return f"""(version 1)
(allow default)
(deny network*)
(deny process-fork)
(deny file-write*)
(allow file-write* (subpath "{workdir}") (literal "/dev/null"))
"""


@dataclass
class Result:
    ok: bool
    output: str
    seconds: float


def sandbox() -> Optional[str]:
    """The sandbox Python checks run in, or None when they're off."""
    if os.getenv("QUORUM_PYTHON", "auto").strip().lower() == "off":
        return None
    if sys.platform == "darwin" and shutil.which("sandbox-exec"):
        return "sandbox-exec"
    if sys.platform.startswith("linux") and shutil.which("bwrap"):
        return "bwrap"
    return None


def parse_requests(text: str, limit: int = 2) -> List[str]:
    """The programs in a message, each after an @Python: line."""
    return [m.strip() for m in _REQUEST.findall(text or "") if m.strip()][:limit]


def before_output(text: str, limit: int = 2) -> str:
    """The text up to the end of the last program it asks to run: anything written after it was written before the
    output existed, so it's a guess."""
    ends = [m.end() for m in _REQUEST.finditer(text or "")][:limit]
    return text[: ends[-1]] if ends else text


def tool_call_error(e: Exception) -> bool:
    """The model tried to call a tool of its own (gpt-oss has a Python tool) and the server couldn't parse it."""
    return "tool call" in str(e).lower()


def _command(kind: str, workdir: str) -> List[str]:
    py = [sys.executable, "-I", "-S", "-B", os.path.join(workdir, "guard.py"), os.path.join(workdir, "check.py")]
    if kind == "sandbox-exec":
        return ["sandbox-exec", "-p", _macos_profile(os.path.realpath(workdir)), *py]
    # bwrap: a read-only view of the system, a private writable folder, no network, no other processes
    return [
        "bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
        "--bind", workdir, workdir, "--chdir", workdir, "--unshare-all", "--die-with-parent", "--new-session", *py,
    ]  # fmt: skip


def _limits() -> None:  # runs in the child before Python starts
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (int(TIMEOUT_S), int(TIMEOUT_S) + 1))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1 << 20, 1 << 20))


async def run(code: str) -> Result:
    """Run one program and return what it printed (stdout, then stderr), clipped."""
    kind = sandbox()
    if not kind:
        return Result(False, "Python checks aren't available on this computer.", 0.0)
    if len(code) > MAX_CODE_CHARS:
        return Result(False, f"The program is too long for a check (over {MAX_CODE_CHARS} characters).", 0.0)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="quorum-py-") as workdir:
        with open(os.path.join(workdir, "guard.py"), "w") as f:
            f.write(_GUARD)
        with open(os.path.join(workdir, "check.py"), "w") as f:
            f.write(code)
        proc = await asyncio.create_subprocess_exec(
            *_command(kind, workdir),
            cwd=workdir,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin", "HOME": workdir, "TMPDIR": workdir, "PYTHONIOENCODING": "utf-8"},
            preexec_fn=_limits,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), TIMEOUT_S + 2)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return Result(False, f"Stopped after {int(TIMEOUT_S)} seconds without finishing.", TIMEOUT_S)
    text = out.decode(errors="replace")
    errors = err.decode(errors="replace").strip()
    if proc.returncode and proc.returncode < 0:
        errors = f"Stopped: it used more than {int(TIMEOUT_S)} seconds of CPU."
    if errors:
        text = f"{text.rstrip()}\n{error_summary(errors)}".strip()
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS] + "\n… (output clipped)"
    return Result(proc.returncode == 0, text.strip() or "(printed nothing)", round(time.monotonic() - started, 2))


def error_summary(stderr: str) -> str:
    """A traceback cut to what the program's author needs: the line of their program and the error."""
    lines = stderr.strip().splitlines()
    where = [ln.strip() for ln in lines if ln.strip().startswith('File "check.py"')]
    last = lines[-1] if lines else ""
    return f"{where[-1]}\n{last}" if where else last


def results_text(programs: List[str], results: List[Result]) -> str:
    """What the programs printed, for the model that wrote them."""
    parts = []
    for i, (code, r) in enumerate(zip(programs, results), 1):
        label = f"Program {i}" if len(programs) > 1 else "Your program"
        status = "printed" if r.ok else "failed with"
        parts.append(f"{label} {status}:\n```\n{r.output}\n```")
    return "\n\n".join(parts)
