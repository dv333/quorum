"""Background sampler for live CPU / memory / GPU charts (last few minutes, in memory only)."""

import asyncio
import re
import shutil
import subprocess
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

import psutil

from . import inventory
from .providers import loaded_models

INTERVAL_S = 2.0
HISTORY = 180  # 6 minutes

_samples: Deque[Dict[str, Any]] = deque(maxlen=HISTORY)
_loaded: List[Dict[str, Any]] = []
_task: Optional[asyncio.Task] = None

_IOREG_UTIL = re.compile(r'"Device Utilization %"=(\d+)')
_IOREG_MEM = re.compile(r'"In use system memory"=(\d+)')


def _gpu_sample() -> Dict[str, Optional[float]]:
    """GPU utilization (%) and memory in use (bytes). Apple Silicon via ioreg (no root), NVIDIA via nvidia-smi."""
    if shutil.which("ioreg"):
        try:
            out = subprocess.run(
                ["ioreg", "-r", "-d", "1", "-w", "0", "-c", "IOAccelerator"], capture_output=True, text=True, timeout=2
            ).stdout
            util, mem = _IOREG_UTIL.search(out), _IOREG_MEM.search(out)
            if util:
                return {"gpu": float(util.group(1)), "gpu_mem": float(mem.group(1)) if mem else None}
        except (subprocess.SubprocessError, OSError):
            pass
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout
            rows = [line.split(",") for line in out.strip().splitlines() if "," in line]
            if rows:
                util = sum(float(r[0]) for r in rows) / len(rows)
                mem = sum(float(r[1]) for r in rows) * 1024 * 1024
                return {"gpu": util, "gpu_mem": mem}
        except (subprocess.SubprocessError, OSError, ValueError):
            pass
    return {"gpu": None, "gpu_mem": None}


async def _sample_once() -> None:
    global _loaded
    vm = psutil.virtual_memory()
    gpu = await asyncio.to_thread(_gpu_sample)
    loaded: List[Dict[str, Any]] = []
    for ep in inventory.endpoints(enabled_only=True):
        if ep.kind == "ollama" and ep.is_local:
            loaded += [{**m, "endpoint": ep.name} for m in await loaded_models(ep)]
    _loaded = loaded
    _samples.append(
        {
            "t": time.time(),
            "cpu": psutil.cpu_percent(None),
            "mem_used": vm.total - vm.available,
            "mem_total": vm.total,
            "models_mem": sum(m.get("size_bytes") or 0 for m in loaded),
            **gpu,
        }
    )


async def _run() -> None:
    psutil.cpu_percent(None)  # prime the counter
    while True:
        try:
            await _sample_once()
        except Exception:
            pass
        await asyncio.sleep(INTERVAL_S)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_run())


async def stop() -> None:
    if _task and not _task.done():
        _task.cancel()


def series() -> Dict[str, Any]:
    sysinfo = inventory.system_info()
    return {
        "interval_s": INTERVAL_S,
        "samples": list(_samples),
        "loaded": _loaded,
        "system": {
            "label": sysinfo["label"],
            "accelerator": sysinfo["accelerator"],
            "total_ram_bytes": sysinfo["total_ram_bytes"],
            "usable_bytes": sysinfo["usable_bytes"],
            "cpu_count": psutil.cpu_count(),
        },
    }
