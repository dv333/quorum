"""First-launch helpers: what's installed, a starter model pack for this machine, and starting local web search."""

import asyncio
import os
import platform
import shutil
from typing import Any, Dict, List, Optional

import httpx

from . import firecrawl, inventory
from .hardware import GB

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Three different families per tier, so the first council already disagrees usefully
STARTER_TIERS = [
    (30, ["qwen3:14b", "gemma3:12b", "llama3.1:8b"]),
    (14, ["qwen3:8b", "gemma3:4b", "llama3.2:3b"]),
    (0, ["qwen3:4b", "llama3.2:3b", "gemma3:4b"]),
]

SAMPLES = [
    "Which came first: the egg or the chicken?",
    "Should I rent or buy a home in 2026?",
    "What's the best way to learn a new programming language in a month?",
]

_job: Dict[str, Any] = {"state": "idle", "log": [], "returncode": None}


def starter_pack(usable_bytes: int) -> List[str]:
    for min_gb, models in STARTER_TIERS:
        if usable_bytes >= min_gb * GB:
            return models
    return STARTER_TIERS[-1][1]


def _ollama_installed() -> bool:
    if shutil.which("ollama"):
        return True
    return platform.system() == "Darwin" and os.path.exists("/Applications/Ollama.app")


async def _docker_running() -> bool:
    if not shutil.which("docker"):
        return False
    proc = await asyncio.create_subprocess_exec(
        "docker", "info", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        return await asyncio.wait_for(proc.wait(), 5) == 0
    except asyncio.TimeoutError:
        proc.kill()
        return False


async def status() -> Dict[str, Any]:
    inv = await inventory.inventory(force=True)
    ollama_ep = next((e for e in inv["endpoints"] if e["kind"] == "ollama" and e["local"]), None)
    version: Optional[str] = None
    if ollama_ep and ollama_ep["reachable"]:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                version = (await client.get(f"{ollama_ep['base_url']}/api/version")).json().get("version")
        except (httpx.HTTPError, ValueError):
            pass
    local_models = [m for m in inv["models"] if m["local"] and m["chat"]]
    installed = {m["model"] for m in local_models}
    catalog = {c["model"]: c for c in inventory.catalog()}
    starter = []
    for name in starter_pack(inv["system"]["usable_bytes"]):
        c = catalog.get(name, {"model": name, "size_bytes": None, "est_bytes": None, "family": "", "strengths": ""})
        starter.append({**c, "installed": name in installed or f"{name}:latest" in installed})
    return {
        "os": platform.system(),
        "system": inv["system"],
        "ollama": {
            "installed": _ollama_installed() or bool(ollama_ep and ollama_ep["reachable"]),
            "running": bool(ollama_ep and ollama_ep["reachable"]),
            "version": version,
            "endpoint_id": ollama_ep["id"] if ollama_ep else None,
        },
        "models": {"count": len(local_models), "names": sorted(installed)},
        "starter": starter,
        "docker": {"installed": bool(shutil.which("docker")), "running": await _docker_running()},
        "research": await firecrawl.status(),
        "firecrawl_job": {k: _job[k] for k in ("state", "returncode")} | {"log": _job["log"][-12:]},
        "samples": SAMPLES,
    }


async def _run_firecrawl() -> None:
    _job.update(state="running", log=[], returncode=None)
    if platform.system() == "Windows":
        cmd = [
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            os.path.join(ROOT, "scripts", "firecrawl.ps1"),
            "up",
        ]
    else:
        cmd = [os.path.join(ROOT, "scripts", "firecrawl.sh"), "up"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=ROOT, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            # docker pull prints a line per layer; keep the log readable
            if line and not any(
                k in line for k in ("Downloading", "Extracting", "Waiting", "Verifying", "Pulling fs layer")
            ):
                _job["log"].append(line)
        _job["returncode"] = await proc.wait()
        _job["state"] = "done" if _job["returncode"] == 0 else "failed"
    except OSError as e:
        _job["log"].append(str(e))
        _job.update(state="failed", returncode=-1)


def start_firecrawl() -> Dict[str, Any]:
    if _job["state"] != "running":
        asyncio.create_task(_run_firecrawl())
        _job["state"] = "running"
    return {"state": _job["state"]}
