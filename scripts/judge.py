"""Blind second judge: Quorum's council scores two answers to the same question, labeled A and B in random order.

    uv run python scripts/judge.py docs/evals/intermittent-fasting

Reads reference.md and quorum.md from the folder, strips the headers that reveal who wrote which, and writes
judge.md (the council's scores) plus judge-key.json (which label was which). Research is off: the judge scores the
answers as written, and the separate fact-check covers accuracy against sources.
"""

import argparse
import json
import os
import random
import re
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("folder")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    answers = {}
    for name in ("reference", "quorum"):
        with open(os.path.join(args.folder, f"{name}.md"), encoding="utf-8") as f:
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
    with open(os.path.join(args.folder, "judge.md"), "w", encoding="utf-8") as f:
        f.write(cli._result(debate_id, "standard", False, False))
    with open(os.path.join(args.folder, "judge-key.json"), "w", encoding="utf-8") as f:
        json.dump({**key, "conundrum": debate_id}, f, indent=2)
        f.write("\n")
    print(f"A = {key['A']}, B = {key['B']}; scores in {args.folder}/judge.md", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
