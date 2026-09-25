"""Installed-model inventory across endpoints, with memory estimates and council planning."""

import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional

from . import db
from .config import AUTO_COUNCIL_MAX, AUTO_COUNCIL_MIN, DEFAULT_NUM_CTX, HANDLES
from .hardware import GB, detect_system, estimate_model_bytes, fit_label, plan_council
from .providers import Endpoint, endpoint_status, list_models, loaded_models

_CACHE_TTL = 15.0
_cache: Dict[str, Any] = {"at": 0.0, "data": None}
_system_cache: Dict[str, Any] = {"at": 0.0, "data": None}


def endpoints(enabled_only: bool = False) -> List[Endpoint]:
    rows = db.query("SELECT * FROM endpoints" + (" WHERE enabled = 1" if enabled_only else "") + " ORDER BY id")
    return [Endpoint.from_row(r) for r in rows]


def endpoint(endpoint_id: int) -> Optional[Endpoint]:
    row = db.query_one("SELECT * FROM endpoints WHERE id = ?", [endpoint_id])
    return Endpoint.from_row(row) if row else None


def _public_endpoint(ep: Endpoint, reachable: Optional[bool], error: Optional[str], n_models: int) -> Dict[str, Any]:
    """Endpoint as shown to the UI: the API key itself never leaves the backend."""
    key = ep.api_key or ""
    return {
        "id": ep.id,
        "name": ep.name,
        "base_url": ep.base_url,
        "kind": ep.kind,
        "enabled": ep.enabled,
        "reachable": reachable,
        "error": error,
        "local": ep.is_local,
        "models": n_models,
        "has_key": bool(key),
        "key_hint": f"…{key[-4:]}" if len(key) >= 8 else "",
    }


def system_info() -> Dict[str, Any]:
    if _system_cache["data"] is None or time.monotonic() - _system_cache["at"] > 30:
        _system_cache.update(at=time.monotonic(), data=detect_system())
    return _system_cache["data"]


def invalidate() -> None:
    _cache.update(at=0.0, data=None)


async def _endpoint_models(ep: Endpoint) -> Dict[str, Any]:
    problem = await endpoint_status(ep)
    if problem:
        return {"endpoint": ep, "reachable": False, "error": problem, "models": [], "loaded": []}
    try:
        models, loaded = await asyncio.gather(list_models(ep), loaded_models(ep))
    except Exception as e:  # server up but misbehaving
        return {"endpoint": ep, "reachable": True, "error": str(e), "models": [], "loaded": []}
    return {"endpoint": ep, "reachable": True, "models": models, "loaded": loaded}


async def scan(force: bool = False) -> List[Dict[str, Any]]:
    if not force and _cache["data"] is not None and time.monotonic() - _cache["at"] < _CACHE_TTL:
        return _cache["data"]
    data = await asyncio.gather(*[_endpoint_models(ep) for ep in endpoints(enabled_only=True)])
    _cache.update(at=time.monotonic(), data=data)
    return data


def _context_length(model_info: Dict[str, Any]) -> Optional[int]:
    for k, v in model_info.items():
        if k.endswith(".context_length") and isinstance(v, (int, float)):
            return int(v)
    return None


async def inventory(num_ctx: int = DEFAULT_NUM_CTX, force: bool = False) -> Dict[str, Any]:
    sysinfo = system_info()
    usable = sysinfo["usable_bytes"]
    scanned = await scan(force)
    eps, models, loaded = [], [], []
    for entry in scanned:
        ep: Endpoint = entry["endpoint"]
        eps.append(_public_endpoint(ep, entry["reachable"], entry.get("error"), len(entry["models"])))
        for m in entry["loaded"]:
            loaded.append({**m, "endpoint_id": ep.id})
        for m in entry["models"]:
            est = estimate_model_bytes(m["size_bytes"], m["model_info"], num_ctx) if ep.is_local else None
            details = m["details"] or {}
            models.append(
                {
                    "key": f"{ep.id}|{m['model']}",
                    "endpoint_id": ep.id,
                    "endpoint_name": ep.name,
                    "kind": ep.kind,
                    "model": m["model"],
                    "size_bytes": m["size_bytes"],
                    "est_bytes": est,
                    "fit": fit_label(est, usable) if ep.is_local else "cloud",
                    "local": ep.is_local,
                    "family": details.get("family"),
                    "params": details.get("parameter_size"),
                    "quant": details.get("quantization_level"),
                    "context_length": _context_length(m["model_info"]),
                    "thinking": "thinking" in (m["capabilities"] or []),
                    # Embedding-only models can't chat
                    "chat": ("completion" in m["capabilities"] if m["capabilities"] else True)
                    and "embed" not in m["model"].lower(),
                }
            )
    # Endpoints that are disabled are still listed so they can be re-enabled
    for ep in endpoints():
        if not ep.enabled:
            eps.append(_public_endpoint(ep, None, None, 0))
    return {"system": sysinfo, "endpoints": eps, "models": models, "loaded": loaded, "num_ctx": num_ctx}


async def model_meta(endpoint_id: int, model: str, num_ctx: int = DEFAULT_NUM_CTX) -> Optional[Dict[str, Any]]:
    inv = await inventory(num_ctx)
    for m in inv["models"]:
        if m["endpoint_id"] == endpoint_id and m["model"] == model:
            return m
    return None


async def plan(selection: List[Dict[str, Any]], num_ctx: int = DEFAULT_NUM_CTX) -> Dict[str, Any]:
    """selection: [{endpoint_id, model}] for seats and chair."""
    inv = await inventory(num_ctx)
    by_key = {m["key"]: m for m in inv["models"]}
    items = []
    for s in selection:
        key = f"{s['endpoint_id']}|{s['model']}"
        items.append({"key": key, "est_bytes": (by_key.get(key) or {}).get("est_bytes")})
    result = plan_council(items, inv["system"]["usable_bytes"])
    result["missing"] = [i["key"] for i in items if i["key"] not in by_key]
    return result


def catalog(num_ctx: int = DEFAULT_NUM_CTX) -> List[Dict[str, Any]]:
    path = os.path.join(os.path.dirname(__file__), "catalog.json")
    with open(path) as f:
        entries = json.load(f)["models"]
    usable = system_info()["usable_bytes"]
    out = []
    for e in entries:
        size = int(e["size_gb"] * GB)
        # No GGUF metadata before pulling: use the size-based KV heuristic
        est = estimate_model_bytes(size, {}, num_ctx)
        out.append({**e, "size_bytes": size, "est_bytes": est, "fit": fit_label(est, usable)})
    return out


def pick_council(
    models: List[Dict[str, Any]], usable_bytes: int, max_size: int = AUTO_COUNCIL_MAX, min_size: int = AUTO_COUNCIL_MIN
) -> List[Dict[str, Any]]:
    """Every runnable local chat model joins the council, largest first (one seat per model).

    Models that don't fit in memory together are simply run one at a time. Models too big to run at
    all, embedding models and cloud models (which cost money) are left out. Repeats models to reach
    min_size so even a single installed model gets a real debate.
    """
    seen, chosen = set(), []
    for m in sorted(models, key=lambda m: m["est_bytes"] or 0, reverse=True):
        if not m.get("chat", True) or not m.get("local", True) or m["fit"] == "too_big" or m["model"] in seen:
            continue
        seen.add(m["model"])
        chosen.append(m)
    chosen = chosen[:max_size]
    seats = list(chosen)
    i = 0
    while seats and len(seats) < min_size:
        seats.append(chosen[i % len(chosen)])
        i += 1
    return seats


def pick_researcher(seats: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Beagle gets a mid-sized member: capable enough to summarize sources, quick enough for many lookups."""
    if not seats:
        return None
    distinct = list({s["model"]: s for s in seats}.values())
    return distinct[len(distinct) // 2]


async def auto_council(num_ctx: int = DEFAULT_NUM_CTX) -> Dict[str, Any]:
    """The council a new question gets by default, plus who will pick the chair (the largest model)."""
    inv = await inventory(num_ctx)
    seats = pick_council(inv["models"], inv["system"]["usable_bytes"])
    members = [
        {
            "handle": HANDLES[i],
            "endpoint_id": m["endpoint_id"],
            "endpoint_name": m["endpoint_name"],
            "model": m["model"],
            "family": m["family"],
            "params": m["params"],
            "est_bytes": m["est_bytes"],
            "thinking": m["thinking"],
        }
        for i, m in enumerate(seats)
    ]
    p = plan_council(
        [{"key": f"{m['endpoint_id']}|{m['model']}", "est_bytes": m["est_bytes"]} for m in members],
        inv["system"]["usable_bytes"],
    )
    beagle = pick_researcher(members)
    researcher = (
        {"endpoint_id": beagle["endpoint_id"], "model": beagle["model"], "endpoint_name": beagle["endpoint_name"]}
        if beagle
        else None
    )
    return {"seats": members, "picker": members[0]["handle"] if members else None, "researcher": researcher, "plan": p}
