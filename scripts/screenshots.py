"""Capture README screenshots from a running Quorum (./start.sh) with Playwright.

    uv run --with playwright python scripts/screenshots.py --answer <debate-id> [--clarify <debate-id>]

Uses your installed Google Chrome (no browser download). Images go to docs/images/.
"""

import argparse
import asyncio
import os

from playwright.async_api import async_playwright

BASE = "http://localhost:5173"
OUT = os.path.join(os.path.dirname(__file__), "..", "docs", "images")


async def shot(browser, name, url, *, dark=False, width=1440, height=900, prepare=None, wait=1500):
    ctx = await browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=2,
                                    color_scheme="dark" if dark else "light")
    await ctx.add_init_script("localStorage.setItem('quorum.onboarded', '1')")
    page = await ctx.new_page()
    await page.goto(url)
    await page.wait_for_timeout(wait)
    if prepare:
        await prepare(page)
        await page.wait_for_timeout(900)
    path = os.path.join(OUT, f"{name}.png")
    await page.screenshot(path=path)
    await ctx.close()
    print("saved", os.path.normpath(path))


async def main(args):
    os.makedirs(OUT, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome")

        async def type_question(page):
            await page.fill("textarea", "Which came first: the egg or the chicken?")

        await shot(browser, "home-light", f"{BASE}/", prepare=type_question)
        await shot(browser, "home-dark", f"{BASE}/", dark=True, prepare=type_question)
        await shot(browser, "welcome", f"{BASE}/#welcome")

        if args.answer:
            url = f"{BASE}/#q/{args.answer}"
            await shot(browser, "answer-light", url, wait=2500)

            async def open_metrics(page):
                await page.evaluate("document.querySelector('.behind').open = true;"
                                    "document.querySelector('.behind').scrollIntoView({block: 'end'})")
            await shot(browser, "metrics", url, wait=2500, prepare=open_metrics)

            async def open_expert(page):
                await page.click("button:has-text('Expert')")
                await page.wait_for_selector(".diagram svg", timeout=120000)
                await page.evaluate("document.querySelector('.diagram').scrollIntoView({block: 'center'})")
            await shot(browser, "expert", url, wait=2500, prepare=open_expert)


        if args.clarify:
            async def to_summary(page):
                if await page.query_selector(".how"):  # answered: the interview is inside the collapsed debate
                    await page.click(".how")
                    await page.wait_for_timeout(400)
                await page.evaluate("[...document.querySelectorAll('.bubble.summary')].pop()?.scrollIntoView({block: 'end'})")
            await shot(browser, "clarify", f"{BASE}/#q/{args.clarify}", wait=2500, prepare=to_summary)

        async def open_activity(page):
            await page.click(".res-card")
        await shot(browser, "activity", f"{BASE}/#settings/models", wait=4000, prepare=open_activity)
        await shot(browser, "settings", f"{BASE}/#settings/providers", wait=2500)
        await browser.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--answer", help="id of a concluded debate")
    ap.add_argument("--clarify", help="id of a debate whose chair asked questions")
    asyncio.run(main(ap.parse_args()))
