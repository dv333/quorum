"""Hardware detection and model memory-fit estimation."""

import json
import platform
import shutil
import subprocess
from typing import Any, Dict, Iterable, List, Optional

import psutil

from .config import MEMORY_RESERVE_GB

GB = 1024**3
RUNTIME_OVERHEAD_BYTES = int(0.5 * GB)


def _run(cmd: List[str]) -> Optional[str]:
    if not shutil.which(cmd[0]):
        return None
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=True).stdout
    except (subprocess.SubprocessError, OSError):
        return None


def _nvidia_gpus() -> List[Dict[str, Any]]:
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    gpus = []
    for line in (out or "").strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 2 and parts[1].isdigit():
            gpus.append({"name": parts[0], "vram_bytes": int(parts[1]) * 1024 * 1024})
    return gpus


def _amd_gpus() -> List[Dict[str, Any]]:
    out = _run(["rocm-smi", "--showmeminfo", "vram", "--json"])
    if not out:
        return []
    try:
        data = json.loads(out)
    except ValueError:
        return []
    gpus = []
    for card, info in data.items():
        total = info.get("VRAM Total Memory (B)") if isinstance(info, dict) else None
        if total and str(total).isdigit():
            gpus.append({"name": f"AMD {card}", "vram_bytes": int(total)})
    return gpus


def _mac_chip() -> Optional[str]:
    out = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
    return out.strip() if out else None


def detect_system(reserve_gb: float = MEMORY_RESERVE_GB) -> Dict[str, Any]:
    """Describe the machine and how much memory local models may use.

    - Apple Silicon: unified memory; Metal caps GPU working set at ~75% of RAM.
    - NVIDIA/AMD: budget is total VRAM (models that spill to CPU run, but slowly).
    - Otherwise: CPU inference from system RAM.
    """
    total_ram = psutil.virtual_memory().total
    available_ram = psutil.virtual_memory().available
    system = platform.system()
    machine = platform.machine().lower()
    reserve = int(reserve_gb * GB)

    info: Dict[str, Any] = {
        "os": system,
        "arch": machine,
        "total_ram_bytes": total_ram,
        "available_ram_bytes": available_ram,
        "reserve_bytes": reserve,
        "gpus": [],
    }

    if system == "Darwin" and machine in ("arm64", "aarch64"):
        chip = _mac_chip() or "Apple Silicon"
        info.update(
            accelerator="apple",
            label=f"{chip} · {round(total_ram / GB)} GB unified",
            usable_bytes=max(0, min(total_ram - reserve, int(total_ram * 0.75))),
        )
        return info

    gpus = _nvidia_gpus()
    accel = "nvidia"
    if not gpus:
        gpus, accel = _amd_gpus(), "amd"
    if gpus:
        vram = sum(g["vram_bytes"] for g in gpus)
        names = ", ".join(g["name"] for g in gpus)
        info.update(
            accelerator=accel,
            gpus=gpus,
            label=f"{names} · {round(vram / GB)} GB VRAM",
            usable_bytes=max(0, vram - int(0.5 * GB)),
        )
        return info

    info.update(
        accelerator="cpu", label=f"CPU only · {round(total_ram / GB)} GB RAM", usable_bytes=max(0, total_ram - reserve)
    )
    return info


def _info_value(model_info: Dict[str, Any], suffix: str) -> Optional[float]:
    for key, value in model_info.items():
        if key.endswith(suffix) and isinstance(value, (int, float)):
            return float(value)
    return None


def kv_cache_bytes(model_info: Dict[str, Any], num_ctx: int, size_bytes: Optional[int]) -> int:
    """Estimate f16 KV-cache size for num_ctx tokens from GGUF metadata."""
    blocks = _info_value(model_info, ".block_count")
    kv_heads = _info_value(model_info, ".attention.head_count_kv")
    key_len = _info_value(model_info, ".attention.key_length")
    val_len = _info_value(model_info, ".attention.value_length")
    if not (key_len and val_len):
        emb = _info_value(model_info, ".embedding_length")
        heads = _info_value(model_info, ".attention.head_count")
        if emb and heads:
            key_len = val_len = emb / heads
    if blocks and kv_heads and key_len and val_len:
        return int(blocks * kv_heads * (key_len + val_len) * num_ctx * 2)
    # Fallback heuristic: ~6% of weights per 8k tokens of context
    return int((size_bytes or 0) * 0.06 * (num_ctx / 8192))


def estimate_model_bytes(size_bytes: Optional[int], model_info: Dict[str, Any], num_ctx: int) -> Optional[int]:
    if not size_bytes:
        return None
    return int(size_bytes + kv_cache_bytes(model_info, num_ctx, size_bytes) + RUNTIME_OVERHEAD_BYTES)


def fit_label(est_bytes: Optional[int], usable_bytes: int) -> str:
    if est_bytes is None:
        return "unknown"
    return "fits" if est_bytes <= usable_bytes else "too_big"


def plan_council(models: Iterable[Dict[str, Any]], usable_bytes: int) -> Dict[str, Any]:
    """Decide whether the selected (deduplicated) models can stay loaded together.

    models: dicts with 'key' (endpoint|model) and 'est_bytes' (None when unknown).
    """
    unique: Dict[str, Optional[int]] = {}
    for m in models:
        unique.setdefault(m["key"], m.get("est_bytes"))
    known = [b for b in unique.values() if b is not None]
    total = sum(known)
    largest = max(known) if known else 0

    if largest > usable_bytes:
        mode = "too_big"
    elif total <= usable_bytes:
        mode = "parallel"
    else:
        mode = "sequential"
    return {
        "mode": mode,
        "total_bytes": total,
        "largest_bytes": largest,
        "usable_bytes": usable_bytes,
        "unknown_models": [k for k, b in unique.items() if b is None],
        "distinct_models": len(unique),
    }
