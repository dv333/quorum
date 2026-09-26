"""Blind second judge: Quorum's council scores two answers to the same question, labeled A and B in random order.

    uv run python scripts/judge.py docs/evals/intermittent-fasting [--answer quorum-v9.md]

Reads reference.md and Quorum's answer (quorum.md unless --answer names another file) from the folder, strips the headers that reveal who wrote which, and writes
judge.md (each agent's blind scores, then the council's answer) plus judge-key.json (which label was which). The
chair's answer follows Quorum's answer format, so the scores are read from the agents' own turns: a table of the five
dimensions, or totals like "A=35/50, B=39/50". `--rescore` rebuilds judge.md from the conundrum in judge-key.json. Research is off: the judge scores the
answers as written, and the separate fact-check covers accuracy against sources.
"""

import argparse
import json
import os
import random
import re
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend import cli  # noqa: E402

RUBRIC = """Score each answer from 0 to 10 on each dimension:
- Factual accuracy: are the material claims correct? A wrong decisive fact costs heavily.
- Evidence quality: does it rest on strong, current, clearly identified sources (for example systematic reviews and
  randomized trials), with honest labels on uncertainty?
- Reasoning: does it weigh alternatives and say when the answer would change?
- Completeness: does it cover every part of the question?
- Clarity and actionability: a clear bottom line and guidance a reader can follow?"""


def body(markdown: str) -> str:
    """The answer itself, without the title, metadata or export footer that reveal the author."""
    text = re.sub(r"^# .*\n", "", markdown, count=1)
    text = re.sub(r"^\*(Asked|Reference|Answer)[^\n]*\*\n", "", text, flags=re.M)
    text = re.sub(r"^> .*\n", "", text, flags=re.M)  # the quoted question
    text = text.split("\n---\n")[0]  # export footer
    return text.strip()


DIMENSIONS = {
    "accuracy": "Factual accuracy",
    "evidence": "Evidence quality",
    "reasoning": "Reasoning",
    "completeness": "Completeness",
    "clarity": "Clarity",
}
_ROW = re.compile(r"^\|?\s*\**([A-Za-z &-]+?)\**\s*\|\s*\**(\d{1,2})\**\s*\|\s*\**(\d{1,2})\**\s*\|", re.M)
_TOTALS = re.compile(r"\bA\s*[=:]\s*(\d{1,2})\s*/\s*50\W+B\s*[=:]\s*(\d{1,2})\s*/\s*50")


def agent_scores(text: str) -> dict:
    """An agent's scores from its turn: per-dimension A/B scores from a table (totals recomputed, since models add up
    wrong), or just the totals when that's all it gave. Empty when it scored nothing."""
    dims = {}
    for name, a, b in _ROW.findall(text):
        key = next((k for k in DIMENSIONS if k in name.lower()), None)
        if key and int(a) <= 10 and int(b) <= 10:
            dims[key] = (int(a), int(b))
    if len(dims) >= 4:
        return {
            "dims": dims,
            "A": sum(a for a, _ in dims.values()),
            "B": sum(b for _, b in dims.values()),
            "of": 10 * len(dims),
        }
    m = _TOTALS.search(text)
    return {"dims": {}, "A": int(m.group(1)), "B": int(m.group(2)), "of": 50} if m else {}


def scores_markdown(snap: dict, key: dict) -> str:
    """Each agent's latest blind scores, labeled with who wrote which answer."""
    handles = {s["id"]: s["handle"] for s in snap["seats"]}
    latest = {}
    for m in snap["messages"]:
        if m["author_kind"] == "seat" and m["status"] == "done":
            found = agent_scores(m["content"])
            if found:
                latest[handles.get(m["seat_id"], "?")] = found
    a, b = key["A"], key["B"]
    lines = ["## Blind scores from the agents", "", f"A was {a}, B was {b}; the agents didn't know.", ""]
    if not latest:
        return "\n".join(lines + ["No agent gave scores.", ""])
    lines += [f"| Agent | {a} (A) | {b} (B) | Per dimension (A/B) |", "|---|---|---|---|"]
    for handle, s in latest.items():
        per = ", ".join(f"{DIMENSIONS[k]} {x}/{y}" for k, (x, y) in s["dims"].items()) or "totals only"
        lines.append(f"| {handle} | {s['A']}/{s['of']} | {s['B']}/{s['of']} | {per} |")
    medians = []
    for k, label in DIMENSIONS.items():
        pairs = [s["dims"][k] for s in latest.values() if k in s["dims"]]
        if pairs:
            ma, mb = statistics.median(x for x, _ in pairs), statistics.median(y for _, y in pairs)
            medians.append(f"{label} {ma:g}/{mb:g}")
    full = [s for s in latest.values() if s["of"] == 50]
    if full:
        ta, tb = statistics.median(s["A"] for s in full), statistics.median(s["B"] for s in full)
        lines.append(f"| **Median** | **{ta:g}/50** | **{tb:g}/50** | {', '.join(medians)} |")
    return "\n".join(lines + [""])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("folder")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--answer", default="quorum.md", help="Quorum's answer file in the folder")
    ap.add_argument("--rescore", action="store_true", help="rebuild judge.md from the last judging conundrum")
    args = ap.parse_args()
    if args.rescore:
        with open(os.path.join(args.folder, "judge-key.json"), encoding="utf-8") as f:
            key = json.load(f)
        write_judge(args.folder, key["conundrum"], key)
        return 0
    answers = {}
    for name, file in (("reference", "reference.md"), ("quorum", args.answer)):
        with open(os.path.join(args.folder, file), encoding="utf-8") as f:
            answers[name] = f.read()
    question = re.search(r"^> (.+)$", answers["reference"], re.M).group(1)
    order = ["reference", "quorum"]
    random.Random(args.seed).shuffle(order)
    key = {"A": order[0], "B": order[1]}
    prompt = (
        f"Two answers to the same question need an impartial evaluation. Question: {question}\n\n"
        f"ANSWER A:\n{body(answers[key['A']])}\n\nANSWER B:\n{body(answers[key['B']])}\n\n{RUBRIC}\n\n"
        "Debate the scores, then give a final table: each dimension with A's score and B's score, the totals out "
        "of 50, and one sentence on the biggest difference between the answers."
    )
    snap = cli.post("/debates", {"question": prompt, "research_enabled": False})
    debate_id = snap["debate"]["id"]
    print(f"judging in conundrum {debate_id} · {cli.APP_URL}/#q/{debate_id}", file=sys.stderr)
    cli.wait_for_answer(debate_id, interactive=False, progress=cli.Progress(quiet=True))
    write_judge(args.folder, debate_id, key)
    with open(os.path.join(args.folder, "judge-key.json"), "w", encoding="utf-8") as f:
        json.dump({**key, "quorum_answer": args.answer, "conundrum": debate_id}, f, indent=2)
        f.write("\n")
    print(f"A = {key['A']}, B = {key['B']}; scores in {args.folder}/judge.md", file=sys.stderr)
    return 0


def write_judge(folder: str, debate_id: str, key: dict) -> None:
    snap = cli.get(f"/debates/{debate_id}")
    with open(os.path.join(folder, "judge.md"), "w", encoding="utf-8") as f:
        f.write(scores_markdown(snap, key) + "\n" + cli._result(debate_id, "standard", False, False))


if __name__ == "__main__":
    sys.exit(main())
