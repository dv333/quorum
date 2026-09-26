"""Model endpoint clients.

Two wire formats are supported:
- ``ollama``: Ollama's native API (/api/chat), which exposes thinking, num_ctx and keep_alive
  and model metadata (/api/tags, /api/show, /api/ps, /api/pull).
- ``openai_compat``: any server speaking /v1/chat/completions (LM Studio, llama.cpp, vLLM, ...).
"""

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from .config import REQUEST_TIMEOUT
from .parsing import ThinkSplitter


@dataclass
class Endpoint:
    id: int
    name: str
    base_url: str
    kind: str  # "ollama" | "openai_compat"
    enabled: bool = True
    api_key: Optional[str] = None

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "Endpoint":
        return cls(
            row["id"],
            row["name"],
            row["base_url"].rstrip("/"),
            row["kind"],
            bool(row["enabled"]),
            row.get("api_key") or None,
        )

    @property
    def is_local(self) -> bool:
        host = urlparse(self.base_url).hostname or ""
        return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local")

    def headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
            if "anthropic.com" in self.base_url:
                h["x-api-key"] = self.api_key
                h["anthropic-version"] = "2023-06-01"
        return h


@dataclass
class Chunk:
    """One streamed piece of a reply. kind is 'content', 'thinking' or 'done'."""

    kind: str
    text: str = ""
    stats: Dict[str, Any] = field(default_factory=dict)


class ProviderError(Exception):
    pass


class ChatClient:
    """Streams chat completions from any registered endpoint."""

    def __init__(self, timeout: float = REQUEST_TIMEOUT) -> None:
        self.timeout = timeout

    async def stream(
        self,
        endpoint: Endpoint,
        model: str,
        messages: List[Dict[str, str]],
        *,
        think: Optional[bool] = None,
        num_ctx: Optional[int] = None,
        keep_alive: Optional[Any] = None,
    ) -> AsyncIterator[Chunk]:
        if endpoint.kind == "ollama":
            gen = self._stream_ollama(endpoint, model, messages, think, num_ctx, keep_alive)
        else:
            gen = self._stream_openai(endpoint, model, messages)
        async for chunk in gen:
            yield chunk

    async def complete(self, endpoint: Endpoint, model: str, messages: List[Dict[str, str]], **kw: Any) -> str:
        """Non-streaming convenience wrapper; returns content only (thinking dropped)."""
        parts = []
        async for chunk in self.stream(endpoint, model, messages, **kw):
            if chunk.kind == "content":
                parts.append(chunk.text)
        return "".join(parts)

    async def _stream_ollama(self, ep, model, messages, think, num_ctx, keep_alive) -> AsyncIterator[Chunk]:
        payload: Dict[str, Any] = {"model": model, "messages": messages, "stream": True}
        if think is not None:
            payload["think"] = think
        if num_ctx:
            payload["options"] = {"num_ctx": num_ctx}
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive

        splitter = ThinkSplitter()  # some models still inline <think> tags in content
        started = time.monotonic()
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=10.0)) as client:
            async with client.stream("POST", f"{ep.base_url}/api/chat", json=payload, headers=ep.headers()) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode(errors="replace")
                    raise ProviderError(_error_text(body, resp.status_code))
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if data.get("error"):
                        raise ProviderError(data["error"])
                    msg = data.get("message") or {}
                    if msg.get("thinking"):
                        yield Chunk("thinking", msg["thinking"])
                    if msg.get("content"):
                        content, thinking = splitter.feed(msg["content"])
                        if thinking:
                            yield Chunk("thinking", thinking)
                        if content:
                            yield Chunk("content", content)
                    if data.get("done"):
                        content, thinking = splitter.flush()
                        if thinking:
                            yield Chunk("thinking", thinking)
                        if content:
                            yield Chunk("content", content)
                        eval_count = data.get("eval_count")
                        eval_ns = data.get("eval_duration") or 0
                        yield Chunk(
                            "done",
                            stats={
                                "tokens": eval_count,
                                "prompt_tokens": data.get("prompt_eval_count"),
                                "tok_per_s": round(eval_count / (eval_ns / 1e9), 1) if eval_count and eval_ns else None,
                                "seconds": round(time.monotonic() - started, 1),
                            },
                        )
                        return

    async def _stream_openai(self, ep, model, messages) -> AsyncIterator[Chunk]:
        payload = {"model": model, "messages": messages, "stream": True}
        splitter = ThinkSplitter()
        started = time.monotonic()
        first_token_at: Optional[float] = None
        n_chunks = 0
        usage_tokens = None
        prompt_tokens = None
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=10.0)) as client:
            async with client.stream("POST", _v1(ep, "/chat/completions"), json=payload, headers=ep.headers()) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode(errors="replace")
                    raise ProviderError(_error_text(body, resp.status_code))
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    data = json.loads(data_str)
                    if data.get("usage"):
                        usage_tokens = data["usage"].get("completion_tokens")
                        prompt_tokens = data["usage"].get("prompt_tokens")
                    for choice in data.get("choices") or []:
                        delta = choice.get("delta") or {}
                        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                        if reasoning:
                            yield Chunk("thinking", reasoning)
                        if delta.get("content"):
                            first_token_at = first_token_at or time.monotonic()
                            n_chunks += 1
                            content, thinking = splitter.feed(delta["content"])
                            if thinking:
                                yield Chunk("thinking", thinking)
                            if content:
                                yield Chunk("content", content)
        content, thinking = splitter.flush()
        if thinking:
            yield Chunk("thinking", thinking)
        if content:
            yield Chunk("content", content)
        tokens = usage_tokens or n_chunks
        gen_s = time.monotonic() - (first_token_at or started)
        yield Chunk(
            "done",
            stats={
                "tokens": tokens,
                "prompt_tokens": prompt_tokens,
                "tok_per_s": round(tokens / gen_s, 1) if tokens and gen_s > 0 else None,
                "seconds": round(time.monotonic() - started, 1),
            },
        )


def _error_text(body: str, status: int) -> str:
    try:
        data = json.loads(body)
        err = data.get("error")
        if isinstance(err, dict):
            err = err.get("message")
        if err:
            return f"HTTP {status}: {err}"
    except ValueError:
        pass
    return f"HTTP {status}: {body[:300]}"


# ---------------------------------------------------------------------------
# Model metadata
# ---------------------------------------------------------------------------

_show_cache: Dict[str, Dict[str, Any]] = {}


async def endpoint_status(ep: Endpoint) -> Optional[str]:
    """None when the server answers normally, otherwise a short reason."""
    url = f"{ep.base_url}/api/version" if ep.kind == "ollama" else _v1(ep, "/models")
    try:
        async with httpx.AsyncClient(timeout=3.0 if ep.is_local else 8.0) as client:
            r = await client.get(url, headers=ep.headers())
    except httpx.HTTPError:
        return "not running" if ep.is_local else "unreachable"
    if r.status_code in (401, 403):
        return "API key rejected" if ep.api_key else "needs an API key"
    return None if r.status_code < 500 else f"HTTP {r.status_code}"


async def endpoint_reachable(ep: Endpoint) -> bool:
    return await endpoint_status(ep) is None


def _v1(ep: Endpoint, path: str) -> str:
    """Append /v1 only to bare hosts; provider URLs like .../api/v1 or .../v1beta/openai are used as given."""
    has_path = urlparse(ep.base_url).path.strip("/") != ""
    return f"{ep.base_url}{path}" if has_path else f"{ep.base_url}/v1{path}"


# Model ids from cloud catalogs that can't chat
_NON_CHAT = re.compile(r"embed|tts|whisper|dall-e|image|audio|moderation|realtime|transcribe|rerank|guard|speech", re.I)


async def list_models(ep: Endpoint) -> List[Dict[str, Any]]:
    """Installed models on an endpoint with whatever metadata it exposes."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        if ep.kind != "ollama":
            r = await client.get(_v1(ep, "/models"), headers=ep.headers())
            r.raise_for_status()
            return [
                {
                    "model": m["id"],
                    "size_bytes": None,
                    "details": {"family": m.get("owned_by")},
                    "capabilities": [] if not _NON_CHAT.search(m["id"]) else ["embedding"],
                    "model_info": {},
                }
                for m in r.json().get("data", [])
            ]

        r = await client.get(f"{ep.base_url}/api/tags")
        r.raise_for_status()
        out = []
        for m in r.json().get("models", []):
            name = m.get("model") or m.get("name")
            show = await _show(client, ep, name, m.get("digest", ""))
            out.append(
                {
                    "model": name,
                    "size_bytes": m.get("size"),
                    "details": m.get("details") or show.get("details") or {},
                    "capabilities": show.get("capabilities") or [],
                    "model_info": show.get("model_info") or {},
                }
            )
        return out


async def _show(client: httpx.AsyncClient, ep: Endpoint, model: str, digest: str) -> Dict[str, Any]:
    key = f"{ep.base_url}|{model}|{digest}"
    if key not in _show_cache:
        try:
            r = await client.post(f"{ep.base_url}/api/show", json={"model": model})
            r.raise_for_status()
            data = r.json()
            # model_info can hold huge tokenizer arrays; keep only scalars
            info = {k: v for k, v in (data.get("model_info") or {}).items() if isinstance(v, (int, float, str))}
            _show_cache[key] = {
                "details": data.get("details"),
                "capabilities": data.get("capabilities"),
                "model_info": info,
            }
        except httpx.HTTPError:
            _show_cache[key] = {}
    return _show_cache[key]


async def loaded_models(ep: Endpoint) -> List[Dict[str, Any]]:
    """Models currently resident in memory (Ollama only)."""
    if ep.kind != "ollama":
        return []
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{ep.base_url}/api/ps")
            r.raise_for_status()
            return [
                {
                    "model": m.get("model") or m.get("name"),
                    "size_bytes": m.get("size"),
                    "vram_bytes": m.get("size_vram"),
                }
                for m in r.json().get("models", [])
            ]
    except httpx.HTTPError:
        return []


OLLAMA_REGISTRY = "https://registry.ollama.ai/v2"
_MODEL_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)?(:[a-zA-Z0-9._-]+)?$")


async def lookup_model(name: str) -> Optional[int]:
    """The download size of any model in Ollama's public library ("qwen3:30b", "user/model:tag"), read from the
    registry's manifest; None when there's no such model or tag. This is how `ollama pull` finds models too."""
    name = name.strip().lower()
    if not _MODEL_NAME.match(name):
        return None
    repo, _, tag = name.partition(":")
    path = repo if "/" in repo else f"library/{repo}"
    url = f"{OLLAMA_REGISTRY}/{path}/manifests/{tag or 'latest'}"
    headers = {"Accept": "application/vnd.docker.distribution.manifest.v2+json"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        try:
            resp = await client.get(url, headers=headers)
        except httpx.HTTPError as e:
            raise ProviderError(f"Couldn't reach Ollama's library: {e}") from e
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        raise ProviderError(_error_text(resp.text, resp.status_code))
    layers = resp.json().get("layers") or []
    return sum(int(layer.get("size") or 0) for layer in layers)


async def delete_model(ep: Endpoint, model: str) -> None:
    """Remove a downloaded model from an Ollama server, freeing its disk space."""
    if ep.kind != "ollama":
        raise ProviderError("Removing models is only supported for Ollama endpoints")
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.request("DELETE", f"{ep.base_url}/api/delete", json={"model": model})
    if resp.status_code >= 400:
        raise ProviderError(_error_text(resp.text, resp.status_code))


async def pull_model(ep: Endpoint, model: str) -> AsyncIterator[Dict[str, Any]]:
    """Stream Ollama pull progress events."""
    if ep.kind != "ollama":
        raise ProviderError("Pulling models is only supported for Ollama endpoints")
    async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10.0)) as client:
        async with client.stream("POST", f"{ep.base_url}/api/pull", json={"model": model, "stream": True}) as resp:
            if resp.status_code >= 400:
                raise ProviderError(_error_text((await resp.aread()).decode(errors="replace"), resp.status_code))
            async for line in resp.aiter_lines():
                if line.strip():
                    yield json.loads(line)
