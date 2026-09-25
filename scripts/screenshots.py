"""Capture README screenshots from a running Quorum (./start.sh) with Playwright.

    uv run --with playwright python scripts/screenshots.py --answer <debate-id> [--clarify <debate-id>] \
        [--live <running-debate-id>] [--show <debate-id> ...] [--only home-light why ...]

Uses your installed Google Chrome (no browser download). Images go to docs/images/.
The sidebar only lists the conundrums given with --answer, --clarify and --show, so your other history stays private.
"""

import argparse
import asyncio
import json
import os

from playwright.async_api import async_playwright

BASE = "http://localhost:5173"
OUT = os.path.join(os.path.dirname(__file__), "..", "docs", "images")
QUESTION = "Should I rent or buy a home in Cupertino in 2026?"
SHOWCASE: set[str] = set()


async def only_showcase(route):
    """Serve the sidebar's conundrum list with only the showcase entries."""
    if route.request.method != "GET":
        return await route.continue_()
    response = await route.fetch()
    debates = [d for d in await response.json() if d["id"] in SHOWCASE]
    await route.fulfill(response=response, body=json.dumps(debates))


async def shot(browser, name, url, *, dark=False, width=1440, height=900, prepare=None, wait=1500):
    ctx = await browser.new_context(
        viewport={"width": width, "height": height}, device_scale_factor=2, color_scheme="dark" if dark else "light"
    )
    await ctx.add_init_script("localStorage.setItem('quorum.onboarded', '1')")
    page = await ctx.new_page()
    if SHOWCASE:
        await page.route(f"{BASE}/api/debates", only_showcase)
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
    SHOWCASE.update(i for i in [args.answer, args.clarify, args.live, *args.show] if i)
    only = set(args.only or [])

    async def take(name, *a, **kw):
        if not only or name in only:
            await shot(browser, name, *a, **kw)

    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome")

        async def type_question(page):
            await page.fill("textarea", QUESTION)

        async def choose_pack(page):
            await page.click("button.chip:has-text('More')")
            await page.click("button.chip:has-text('Stress-test a decision')")
            await page.keyboard.type("buy a home in Cupertino in 2026 instead of renting.")

        await take("home-light", f"{BASE}/", prepare=type_question)
        await take("home-dark", f"{BASE}/", dark=True, prepare=type_question)
        await take("packs", f"{BASE}/", prepare=choose_pack)
        await take("welcome", f"{BASE}/#welcome")

        async def open_customize(page):
            await page.click("button.linkish:has-text('Customize')")

        await take("customize", f"{BASE}/", prepare=open_customize)

        if args.answer:
            url = f"{BASE}/#q/{args.answer}"
            await take("answer-light", url, wait=2500)

            async def open_expert(page):
                await page.click("button:has-text('Expert')")
                await page.wait_for_selector(".diagram svg", timeout=120000)
                await page.evaluate("document.querySelector('.diagram').scrollIntoView({block: 'center'})")

            await take("expert", url, wait=2500, prepare=open_expert)

            async def open_metrics(page):
                await page.evaluate("document.querySelector('.behind').scrollIntoView({block: 'center'})")

            await take("metrics", url, wait=2500, prepare=open_metrics)

            async def open_export(page):
                await page.click("button[aria-label=Export]")

            await take("export", url, wait=2500, prepare=open_export)

            async def open_debate(page):
                await page.click(".how")
                await page.wait_for_timeout(500)
                await page.evaluate(
                    "[...document.querySelectorAll('.round-divider')][1]?.scrollIntoView({block: 'start'})"
                )

            await take("debate", url, wait=2500, prepare=open_debate)

            async def open_why(page):
                # Select the first key point and ask the council where it came from
                await page.evaluate(
                    """() => {
                      const li = document.querySelector('.answer-text li') || document.querySelector('.answer-text p')
                      const range = document.createRange(); range.selectNodeContents(li)
                      const s = getSelection(); s.removeAllRanges(); s.addRange(range)
                      li.scrollIntoView({block: 'center'})
                      document.dispatchEvent(new MouseEvent('mouseup'))
                    }"""
                )
                await page.wait_for_selector(".why-btn")
                await page.click(".why-btn")
                await page.wait_for_selector(".why-panel .why-summary, .why-panel .why-group", timeout=240000)

            await take("why", url, wait=2500, prepare=open_why)

            async def open_mentions(page):
                await page.click(".composer textarea")
                await page.keyboard.type("@")

            await take("mentions", url, wait=2500, prepare=open_mentions)

        if args.clarify:

            async def to_summary(page):
                if await page.query_selector(".how"):  # answered: the interview is inside the collapsed debate
                    await page.click(".how")
                    await page.wait_for_timeout(400)
                await page.evaluate(
                    "[...document.querySelectorAll('.bubble.summary')].pop()?.scrollIntoView({block: 'end'})"
                )

            await take("clarify", f"{BASE}/#q/{args.clarify}", wait=2500, prepare=to_summary)

        if args.live:

            async def open_draft(page):
                await page.wait_for_selector(".living-head", timeout=600000)
                await page.click(".living-head")

            await take("live", f"{BASE}/#q/{args.live}", wait=3000, prepare=open_draft)

        async def open_activity(page):
            await page.click(".res-card")

        await take("activity", f"{BASE}/#settings/models", wait=4000, prepare=open_activity)
        await take("settings", f"{BASE}/#settings/providers", wait=2500)
        await take("models", f"{BASE}/#settings/models", wait=2500)
        await browser.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--answer", help="id of a concluded debate")
    ap.add_argument("--clarify", help="id of a debate whose chair asked questions")
    ap.add_argument("--live", help="id of a debate that is running, with at least one draft answer")
    ap.add_argument("--show", nargs="*", default=[], help="more debate ids to list in the sidebar")
    ap.add_argument("--only", nargs="*", help="take only these screenshots (by name)")
    asyncio.run(main(ap.parse_args()))
