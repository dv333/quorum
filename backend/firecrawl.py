"""Firecrawl client for the Researcher: web search with page content, self-hosted or cloud."""

import re
from typing import Any, Dict, List

import httpx

from . import db
from .config import FIRECRAWL_API_KEY, FIRECRAWL_CLOUD_URL, FIRECRAWL_URL, RESEARCH_PAGE_CHARS, RESEARCH_TIMEOUT


class SearchError(Exception):
    pass


def settings() -> Dict[str, str]:
    """Effective Firecrawl settings: UI overrides stored in SQLite, falling back to the environment."""
    api_key = db.get_setting("firecrawl_api_key", FIRECRAWL_API_KEY) or ""
    mode = db.get_setting("firecrawl_mode", "cloud" if FIRECRAWL_API_KEY else "self")
    url = FIRECRAWL_CLOUD_URL if mode == "cloud" else db.get_setting("firecrawl_url", FIRECRAWL_URL)
    return {"mode": mode, "url": url.rstrip("/"), "api_key": api_key}


def public_settings() -> Dict[str, Any]:
    s = settings()
    key = s["api_key"]
    return {
        "mode": s["mode"],
        "url": s["url"],
        "self_url": db.get_setting("firecrawl_url", FIRECRAWL_URL),
        "api_key_set": bool(key),
        "api_key_hint": f"…{key[-4:]}" if len(key) >= 8 else "",
    }


def _headers(s: Dict[str, str]) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if s["api_key"]:
        headers["Authorization"] = f"Bearer {s['api_key']}"
    return headers


async def status() -> Dict[str, Any]:
    """Whether web research can run. Cloud is 'ready' when a key is set (checking would spend credits)."""
    s = settings()
    info = public_settings()
    if s["mode"] == "cloud":
        return {
            **info,
            "ready": bool(s["api_key"]),
            "error": None if s["api_key"] else "Add a Firecrawl API key to use the cloud service.",
        }
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{s['url']}/")
            ready = r.status_code < 500
            return {**info, "ready": ready, "error": None if ready else f"HTTP {r.status_code}"}
    except httpx.HTTPError:
        return {
            **info,
            "ready": False,
            "error": f"Firecrawl isn't running at {s['url']}. Start it with scripts/firecrawl.sh up.",
        }


def relevant_excerpt(markdown: str, query: str, limit: int = RESEARCH_PAGE_CHARS) -> str:
    """Pick the passages of a (possibly huge) page that best match the query, in page order."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", markdown)  # drop images
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # keep link text only
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= limit:
        return text
    terms = {t for t in re.findall(r"[a-z0-9]{3,}", query.lower())}
    chunks = [c.strip() for c in re.split(r"\n\s*\n", text) if len(c.strip()) > 40]
    scored = []
    for i, chunk in enumerate(chunks):
        words = re.findall(r"[a-z0-9]{3,}", chunk.lower())
        hits = sum(1 for w in words if w in terms)
        # favour dense matches, slightly favour the top of the page
        scored.append((hits / (len(words) ** 0.5 + 1) + (0.2 if i < 3 else 0), i, chunk))
    picked, used = [], 0
    for _, i, chunk in sorted(scored, reverse=True):
        piece = chunk[: limit - used]
        if len(piece) < 60:
            break
        picked.append((i, piece))
        used += len(piece) + 2
        if used >= limit:
            break
    return "\n\n".join(c for _, c in sorted(picked))


async def search(query: str, limit: int) -> List[Dict[str, str]]:
    """Search the web and return [{url, title, description, content}] with query-relevant page excerpts."""
    s = settings()
    if s["mode"] == "cloud" and not s["api_key"]:
        raise SearchError("No Firecrawl API key set")
    payload = {
        "query": query,
        "limit": limit,
        "sources": ["web"],
        "scrapeOptions": {"formats": ["markdown"], "onlyMainContent": True},
        "timeout": int(RESEARCH_TIMEOUT * 1000 * 0.8),
    }
    try:
        async with httpx.AsyncClient(timeout=RESEARCH_TIMEOUT) as client:
            r = await client.post(f"{s['url']}/v2/search", json=payload, headers=_headers(s))
    except httpx.ConnectError:
        raise SearchError(f"Can't reach Firecrawl at {s['url']}")
    except httpx.TimeoutException:
        raise SearchError("Firecrawl search timed out")
    try:
        data = r.json()
    except ValueError:
        raise SearchError(f"Firecrawl returned HTTP {r.status_code}")
    if r.status_code >= 400 or not data.get("success", False):
        raise SearchError(f"Firecrawl error (HTTP {r.status_code}): {data.get('error') or data}")

    body = data.get("data") or {}
    results = body.get("web", []) if isinstance(body, dict) else body  # v2 shape, or v1's flat list
    out = []
    for item in results:
        url = item.get("url") or (item.get("metadata") or {}).get("sourceURL")
        if not url:
            continue
        out.append(
            {
                "url": url,
                "title": item.get("title") or (item.get("metadata") or {}).get("title") or url,
                "description": item.get("description") or "",
                "content": relevant_excerpt(item.get("markdown") or "", query),
            }
        )
    return out
