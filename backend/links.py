"""Links in a question, read before the council sees it.

Asked to research https://github.com/you/repo, the council used to search the web about it and guess, getting the
repository's contents and its competitors wrong. Now each link in the question is opened first: a GitHub repository
through GitHub's API (its description, stars, topics, last update, top-level files and README), any other page through
Firecrawl when it's set up, else a plain download. The page goes into the question like an attached file.
"""

import html
import re
from datetime import date
from typing import Any, Dict, List

import httpx

from . import firecrawl

URL_RE = re.compile(r"https?://[^\s<>()\"'`]+", re.I)
MAX_LINKS = 3
_GITHUB_REPO = re.compile(r"^https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?(?:[#?].*)?$", re.I)
_UA = {"User-Agent": "Mozilla/5.0 (Quorum; reads links in your question)"}


class LinkError(Exception):
    pass


def links_in(text: str) -> List[str]:
    """The links in a message, in order, without trailing punctuation, at most MAX_LINKS."""
    out: List[str] = []
    for m in URL_RE.finditer(text or ""):
        url = m.group(0).rstrip(".,;:!?]*_")
        if url not in out:
            out.append(url)
    return out[:MAX_LINKS]


def display(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url).rstrip("/")[:120]


async def read(url: str) -> str:
    gh = _GITHUB_REPO.match(url)
    if gh:
        return await _github(gh.group(1), gh.group(2))
    if (await firecrawl.status())["ready"]:
        try:
            return await firecrawl.scrape(url)
        except firecrawl.SearchError:
            pass  # a plain download may still work
    return await _download(url)


def github_summary(repo: Dict[str, Any], readme: str, files: List[str]) -> str:
    """What the council needs to know about a repository, as GitHub shows it today."""
    lic = (repo.get("license") or {}).get("spdx_id") or "none"
    lines = [
        f"GitHub repository {repo.get('full_name')}, as of {date.today().isoformat()}",
        f"Description: {repo.get('description') or '(none)'}",
        f"Stars: {repo.get('stargazers_count', 0):,} · Forks: {repo.get('forks_count', 0):,} · "
        f"Open issues and PRs: {repo.get('open_issues_count', 0):,} · Watchers: {repo.get('subscribers_count', 0):,}",
        f"Language: {repo.get('language') or 'unknown'} · Topics: {', '.join(repo.get('topics') or []) or 'none'} · "
        f"License: {lic}",
        f"Created: {(repo.get('created_at') or '')[:10]} · Last push: {(repo.get('pushed_at') or '')[:10]}",
    ]
    if repo.get("homepage"):
        lines.append(f"Homepage: {repo['homepage']}")
    if files:
        lines.append(f"Top-level files and folders: {', '.join(files[:60])}")
    lines.append("")
    lines.append("README:" if readme.strip() else "README: (none)")
    lines.append(readme.strip())
    return "\n".join(lines).strip()


async def _github(owner: str, name: str) -> str:
    api = f"https://api.github.com/repos/{owner}/{name}"
    headers = {**_UA, "Accept": "application/vnd.github+json"}
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
            r = await c.get(api, headers=headers)
            if r.status_code == 404:
                raise LinkError("GitHub says this repository doesn't exist or is private")
            if r.status_code >= 400:
                raise LinkError(f"GitHub answered HTTP {r.status_code}")
            repo = r.json()
            readme_r = await c.get(f"{api}/readme", headers={**_UA, "Accept": "application/vnd.github.raw"})
            files_r = await c.get(f"{api}/contents", headers=headers)
    except httpx.HTTPError as e:
        raise LinkError(f"couldn't reach GitHub: {e}")
    readme = readme_r.text if readme_r.status_code == 200 else ""
    files = []
    if files_r.status_code == 200 and isinstance(files_r.json(), list):
        files = [f["name"] + ("/" if f.get("type") == "dir" else "") for f in files_r.json()]
    return github_summary(repo, readme, files)


def page_text(url: str, raw: str) -> str:
    """A page's title, description and readable text, from its HTML."""
    title = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
    desc = re.search(r'(?is)<meta[^>]+(?:name|property)="(?:og:)?description"[^>]+content="([^"]*)"', raw)
    body = re.sub(r"(?is)<(script|style|nav|footer|header|noscript|svg)\b.*?</\1>", " ", raw)
    body = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr)>", "\n", body)
    body = html.unescape(re.sub(r"<[^>]+>", " ", body))
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n\s*\n+", "\n\n", body).strip()
    parts = [f"Page: {url}"]
    if title:
        parts.append(f"Title: {html.unescape(title.group(1)).strip()}")
    if desc:
        parts.append(f"Description: {html.unescape(desc.group(1)).strip()}")
    parts.append(body)
    return "\n".join(parts)


async def _download(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
            r = await c.get(url, headers=_UA)
    except httpx.HTTPError as e:
        raise LinkError(f"couldn't open it: {e}")
    if r.status_code >= 400:
        raise LinkError(f"the site answered HTTP {r.status_code}")
    kind = r.headers.get("content-type", "")
    if "html" in kind:
        text = page_text(url, r.text[:2_000_000])
    elif kind.startswith("text/") or "json" in kind:
        text = f"Page: {url}\n{r.text[:200_000]}"
    else:
        raise LinkError(f"it's a {kind.split(';')[0] or 'file'} Quorum can't read from a link; attach it instead")
    if len(re.sub(r"\s", "", text)) < 200:
        raise LinkError("the page has almost no readable text (it may need a login or JavaScript)")
    return text
