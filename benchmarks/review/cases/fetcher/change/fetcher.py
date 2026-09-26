"""Fetches many URLs concurrently, with a small cache."""

import asyncio
import time

import httpx


class Fetcher:
    def __init__(self, client: httpx.AsyncClient, limit=10):
        self.client = client
        self.sem = asyncio.Semaphore(limit)
        self.cache = {}

    async def get(self, url, retries=3):
        if url in self.cache:
            return self.cache[url]
        for attempt in range(retries):
            try:
                resp = await self.client.get(url, timeout=10)
                resp.raise_for_status()
                break
            except httpx.HTTPError:
                time.sleep(2**attempt)
        self.cache[url] = resp.text
        return resp.text

    async def get_all(self, urls):
        results = []
        for url in urls:
            results.append(self.get(url))
        print(f"fetched {len(results)} urls")
        return results
