"""Fetches many URLs concurrently, with a small cache."""

import asyncio

import httpx


class Fetcher:
    def __init__(self, client: httpx.AsyncClient, limit=10):
        self.client = client
        self.sem = asyncio.Semaphore(limit)
        self.cache = {}

    async def get(self, url):
        if url in self.cache:
            return self.cache[url]
        async with self.sem:
            resp = await self.client.get(url, timeout=10)
        resp.raise_for_status()
        self.cache[url] = resp.text
        return resp.text

    async def get_all(self, urls):
        return await asyncio.gather(*(self.get(u) for u in urls))
