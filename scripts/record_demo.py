"""Record the README demo: a real conundrum from question to answer, driven like a user would.

    uv run --with playwright python scripts/record_demo.py [--question "..."]
    python scripts/make_gifs.py

Needs a running Quorum (./start.sh) with models and web search. Frames go to .demo-frames/ (gitignored).
The script plays the user: it types the question, taps a suggested answer, types details for the next
question, confirms the chair's summary, then explores the answer.
"""

import argparse
import asyncio
import json
import os
import shutil
import time

from playwright.async_api import async_playwright

BASE = "http://localhost:5173"
ROOT = os.path.join(os.path.dirname(__file__), "..")
FRAMES = os.path.join(ROOT, ".demo-frames")

QUESTION = "Should I rent or buy a home in Cupertino in 2026?"
DETAILS = (
    "We're a family of three with about $600k household income and $800k saved, "
    "and we can spend up to $12k a month on housing. Good schools matter most to us."
)


class Recorder:
    def __init__(self, page):
        self.page = page
        self.frames = []
        self.start = time.monotonic()

    async def snap(self, phase):
        path = os.path.join(FRAMES, f"{len(self.frames):05d}-{int(time.time() * 1000) % 100000}.png")
        await self.page.screenshot(path=path)
        self.frames.append({"t": round(time.monotonic() - self.start, 2), "phase": phase, "file": os.path.basename(path)})

    async def hold(self, phase, seconds, every=0.5):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            await self.snap(phase)
            await asyncio.sleep(every)

    def save(self):
        with open(os.path.join(FRAMES, "frames.json"), "w") as f:
            json.dump(self.frames, f, indent=1)


async def debate_state(page):
    return await page.evaluate("""async () => {
        const id = location.hash.split('/')[1]
        if (!id) return null
        const s = await (await fetch('/api/debates/' + id)).json()
        const mods = s.messages.filter(m => m.author_kind === 'moderator')
        return { status: s.debate.status, moderators: mods.length, lastKind: mods.length ? mods[mods.length - 1].meta?.kind : null }
    }""")


async def switch_level(page, rec, label, phase):
    await page.click(f"button:has-text('{label}')")
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        await rec.snap(phase)
        if not await page.query_selector(".answer.rewriting"):
            break
        await asyncio.sleep(0.8)


async def record_answer(page, rec):
    """The answer: let it land, scroll through it, show the metrics, then the Simple and Expert views."""
    await rec.hold("answer", 4.0)
    scroller = "document.querySelector('.scroller')"
    top = await page.evaluate(f"{scroller}.scrollTop")
    end = await page.evaluate(
        "document.querySelector('.answer').offsetTop + document.querySelector('.answer').offsetHeight"
        f" - {scroller}.clientHeight + 40"
    )
    for y in range(int(top), int(end), 45):
        await page.evaluate(f"{scroller}.scrollTop = {y}")
        await rec.snap("answer")
    await rec.hold("answer", 2.5)
    await page.evaluate("document.querySelector('.answer').scrollIntoView({block: 'start', behavior: 'smooth'})")
    await rec.hold("answer", 1.2)
    await switch_level(page, rec, "Simple", "simple")
    await rec.hold("simple", 4.0)
    await switch_level(page, rec, "Expert", "expert")
    await rec.hold("expert", 2.0)
    try:  # Mermaid renders asynchronously
        await page.wait_for_selector(".diagram svg", timeout=15000)
    except Exception:
        pass
    await page.evaluate("document.querySelector('.diagram')?.scrollIntoView({block: 'center', behavior: 'smooth'})")
    await rec.hold("expert", 4.0)


async def answer_only(debate_id):
    """Re-record just the answer of a finished debate, keeping the frames recorded before it."""
    with open(os.path.join(FRAMES, "frames.json")) as fh:
        kept = [f for f in json.load(fh) if f["phase"] not in ("answer", "simple", "expert")]
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome")
        ctx = await browser.new_context(viewport={"width": 1280, "height": 800}, color_scheme="light")
        await ctx.add_init_script("localStorage.setItem('quorum.onboarded', '1')")
        page = await ctx.new_page()
        rec = Recorder(page)
        rec.frames = list(kept)
        rec.start = time.monotonic() - (kept[-1]["t"] if kept else 0)
        await page.goto(f"{BASE}/#q/{debate_id}")
        await page.wait_for_selector(".answer")
        await page.evaluate("document.querySelector('.scroller').scrollTop = 0")
        await record_answer(page, rec)
        rec.save()
        print(f"{len(rec.frames)} frames → {os.path.normpath(FRAMES)}")
        await browser.close()


async def main(question):
    shutil.rmtree(FRAMES, ignore_errors=True)
    os.makedirs(FRAMES)
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome")
        ctx = await browser.new_context(viewport={"width": 1280, "height": 800}, color_scheme="light")
        await ctx.add_init_script("localStorage.setItem('quorum.onboarded', '1')")
        page = await ctx.new_page()
        rec = Recorder(page)
        await page.goto(f"{BASE}/")
        await page.wait_for_selector(".member")
        await rec.hold("ask", 1.5)

        # Type the question like a person
        await page.click("textarea")
        for i, ch in enumerate(question):
            await page.keyboard.type(ch)
            await asyncio.sleep(0.035)
            if i % 2 == 0:
                await rec.snap("ask")
        await rec.hold("ask", 1.0)
        await page.keyboard.press("Enter")

        # Intake: answer the chair, then confirm its summary
        answered = 0
        deadline = time.monotonic() + 90 * 60
        while time.monotonic() < deadline:
            await rec.snap("interview")
            state = await debate_state(page)
            if not state:
                await asyncio.sleep(0.6)
                continue
            if state["status"] == "clarifying" and state["moderators"] > answered:
                await rec.hold("interview", 2.5)
                chips = await page.query_selector_all(".quick-replies .chip")
                if answered != 1 and chips:
                    # Tap the last suggestion: usually the long-term, well-off end, which fits the family's details
                    target = chips[-1]
                    await target.hover()
                    await rec.hold("interview", 0.8)
                    await target.click()
                else:  # the second question gets the family's details, typed
                    await page.click(".composer textarea")
                    for i, ch in enumerate(DETAILS):
                        await page.keyboard.type(ch)
                        if i % 4 == 0:
                            await rec.snap("interview")
                    await rec.hold("interview", 0.8)
                    await page.keyboard.press("Enter")
                answered = state["moderators"]
            elif state["status"] == "confirming":
                await rec.hold("interview", 3.5)
                button = await page.query_selector("text=Looks right, start")
                await button.hover()
                await rec.hold("interview", 0.8)
                await button.click()
                break
            elif state["status"] in ("running", "concluded"):
                break  # the chair found the conundrum clear
            await asyncio.sleep(0.6)

        # The debate: a frame every two seconds until the answer is written
        while time.monotonic() < deadline:
            await rec.snap("debate")
            state = await debate_state(page)
            if state and state["status"] == "concluded":
                break
            await asyncio.sleep(2.0)

        await record_answer(page, rec)

        rec.save()
        print(f"{len(rec.frames)} frames in {rec.frames[-1]['t']:.0f}s → {os.path.normpath(FRAMES)}")
        await browser.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--question", default=QUESTION)
    ap.add_argument("--answer-only", metavar="DEBATE_ID", help="re-record only the answer of a finished debate")
    args = ap.parse_args()
    asyncio.run(answer_only(args.answer_only) if args.answer_only else main(args.question))
