"""Is Quorum's advice better than its best model's alone?

Everyday questions (benchmarks/advice/questions.json) have no computed answer, so each pair is judged blind:

  single  the largest local model, alone, with the same instructions and answer format as the chair's first answer
          (so formats match and the judge can't tell the two apart by their shape)
  quorum  a Quorum conundrum, with or without web research

Then a local model that isn't on the advice council judges every pair twice, once in each order; a pair only counts as
a win when both orders agree. blind.md holds the pairs as A and B for a human judge, with the key in key.json.

    uv run python scripts/benchmark_advice.py --out /tmp/advice             # ask both (hours; resumes)
    uv run python scripts/benchmark_advice.py --out /tmp/advice --judge     # then judge and write the report
    uv run python scripts/benchmark_advice.py --out /tmp/advice --research  # Quorum with web research

Needs Quorum running (./start.sh) and Ollama.
"""

import argparse
import json
import random
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from backend import cli, prompts  # noqa: E402
from backend.engine import is_plan  # noqa: E402
from benchmark_review import OLLAMA, largest_local_model  # noqa: E402

QUESTIONS = ROOT / "benchmarks" / "advice" / "questions.json"
JUDGE = "phi4:14b"  # not on the advice council (the four largest models), so it judges no answer of its own

HANDLES = r"\b(Otter|Panda|Koala|Penguin|Hedgehog|Bunny|Turtle|Dolphin|Beagle)\b"


def single_messages(question: str) -> List[Dict[str, str]]:
    return [
        {"role": "system", "content": "You are a careful expert adviser."},
        {
            "role": "user",
            "content": f"""Today is {prompts.today()}.
Question: {question}

Think it through, then write the best complete answer you can: the one a careful expert would give this user, specific to their situation and numbers. {prompts.ANSWER_RULES} {prompts.MAINSTREAM} Every section must agree with the bottom line.

{prompts.answer_format(sourced=False, plan=is_plan(question), debate=False)}""",
        },
    ]


def _chat(model: str, messages: List[Dict[str, str]], think: Optional[bool], num_ctx: int = 16384) -> str:
    body: Dict[str, Any] = {"model": model, "messages": messages, "stream": False, "options": {"num_ctx": num_ctx}}
    if think is not None:
        body["think"] = think
    req = urllib.request.Request(f"{OLLAMA}/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1200) as r:
        text = json.load(r)["message"]["content"]
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def ask_single(question: str, model: str) -> Dict[str, Any]:
    """The model alone, thinking first; one more try without thinking when it sends nothing, as Quorum does."""
    started = time.monotonic()
    text, retried = _chat(model, single_messages(question), None), False
    if not text:
        text, retried = _chat(model, single_messages(question), False), True
    return {"model": model, "answer": text, "seconds": round(time.monotonic() - started), "retried": retried}


def ask_quorum(question: str, research: bool) -> Dict[str, Any]:
    started = time.monotonic()
    debate_id = cli.post("/debates", {"question": question, "research_enabled": research})["debate"]["id"]
    print(f"    quorum: {cli.APP_URL}/#q/{debate_id}", flush=True)
    while True:
        snap = cli.get(f"/debates/{debate_id}")
        status = snap["debate"]["status"]
        if status in ("clarifying", "confirming"):
            cli.post(f"/debates/{debate_id}/intake/confirm")
        if status in ("concluded", "failed", "cancelled"):
            break
        time.sleep(3)
    v = snap["verdicts"][-1] if snap["verdicts"] else None
    msg = next((m for m in snap["messages"] if v and m["id"] == v["message_id"]), {})
    return {
        "id": debate_id,
        "status": status,
        "seconds": round(time.monotonic() - started),
        "answer": msg.get("content", "") if msg.get("status") == "done" else "",
        "meta": msg.get("meta"),
        "seats": [s["model"] for s in snap["seats"]],
        "research": research,
    }


# ------------------------------------------------------------------ blind judging


def blind_text(answer: str) -> str:
    """An answer without what gives Quorum away: the "Where they differed" section and agents' names."""
    text = re.sub(r"(?ims)^##\s*Where they differed.*?(?=^##\s|\Z)", "", answer or "")
    text = re.sub(r"\*(The full answer couldn't be written.*?)\*", "", text)
    return re.sub(HANDLES, "an adviser", text).strip()


JUDGE_PROMPT = """You compare two answers to the same question from someone asking for advice. Judge which one would
serve this person better, on:
1. Correct and safe: no wrong facts, no advice that could hurt them.
2. Fits them: uses their numbers and situation, and the numbers add up.
3. Complete and specific: covers what an expert would cover, with concrete steps.
4. Honest: says what's uncertain; no made-up statistics or sources.
Length and formatting don't count by themselves.

Question: {question}

Answer A:
{a}

Answer B:
{b}

Reply with a single JSON object: {{"winner": "A" | "B" | "tie", "reason": "<one sentence>"}}"""


def judge_pair(question: str, a: str, b: str, model: str) -> Dict[str, str]:
    text = _chat(model, [{"role": "user", "content": JUDGE_PROMPT.format(question=question, a=a, b=b)}], None)
    m = re.search(r"\{.*\}", text, re.S)
    try:
        out = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        out = {}
    winner = str(out.get("winner", "")).strip().upper()
    return {"winner": winner if winner in ("A", "B") else "TIE", "reason": str(out.get("reason", ""))[:300]}


def judge(results: Dict[str, Any], questions: List[Dict[str, str]], model: str) -> None:
    """Each pair twice, Quorum first and then the model alone first. A win needs both orders to agree."""
    for q in questions:
        r = results.get(q["id"]) or {}
        if "single" not in r or "quorum" not in r or "judge" in r:
            continue
        s, qu = blind_text(r["single"]["answer"]), blind_text(r["quorum"]["answer"])
        if not qu:
            r["judge"] = {"verdict": "single", "note": "Quorum gave no answer"}
            continue
        first = judge_pair(q["question"], qu, s, model)  # A = Quorum
        second = judge_pair(q["question"], s, qu, model)  # A = the model alone
        votes = [
            {"A": "quorum", "B": "single"}.get(first["winner"], "tie"),
            {"A": "single", "B": "quorum"}.get(second["winner"], "tie"),
        ]
        verdict = votes[0] if votes[0] == votes[1] else "tie"
        r["judge"] = {"model": model, "verdict": verdict, "votes": votes, "reasons": [first["reason"], second["reason"]]}
        print(f"{q['id']}: {verdict} ({votes[0]}, {votes[1]})", flush=True)


def write_blind(results: Dict[str, Any], questions: List[Dict[str, str]], out: Path) -> None:
    """blind.md for a human judge: each pair as A and B in a random order; key.json says which is which."""
    rng = random.SystemRandom()
    key, parts = {}, []
    for q in questions:
        r = results.get(q["id"]) or {}
        if "single" not in r or "quorum" not in r:
            continue
        pair = [("single", r["single"]["answer"]), ("quorum", r["quorum"]["answer"])]
        rng.shuffle(pair)
        key[q["id"]] = {"A": pair[0][0], "B": pair[1][0]}
        parts.append(f"# {q['id']}: {q['question']}\n\n## A\n\n{blind_text(pair[0][1])}\n\n## B\n\n{blind_text(pair[1][1])}\n")
    (out / "blind.md").write_text("\n---\n\n".join(parts))
    (out / "key.json").write_text(json.dumps(key, indent=1))


def report(results: Dict[str, Any], questions: List[Dict[str, str]]) -> str:
    judged = [(q, results[q["id"]]) for q in questions if "judge" in results.get(q["id"], {})]
    counts = {v: sum(r["judge"]["verdict"] == v for _, r in judged) for v in ("quorum", "single", "tie")}
    decided = counts["quorum"] + counts["single"]
    minutes = sorted(r["quorum"]["seconds"] / 60 for _, r in judged)
    lines = [
        "# Advice benchmark: Quorum vs its best model alone",
        "",
        f"{len(judged)} questions, judged blind by a local model in both orders (a win needs both to agree).",
        "",
        "| | Pairs |",
        "|---|---|",
        f"| Quorum better | {counts['quorum']} |",
        f"| Model alone better | {counts['single']} |",
        f"| Tie or the orders disagreed | {counts['tie']} |",
        f"| **Quorum's share of decided pairs** | **{round(100 * counts['quorum'] / decided) if decided else 0}%** |",
        "",
        f"Median time per question: Quorum {minutes[len(minutes) // 2]:.1f} min." if minutes else "",
        "",
        "| Question | Verdict | Why (Quorum first / alone first) |",
        "|---|---|---|",
    ]
    for q, r in judged:
        j = r["judge"]
        why = " / ".join(x.replace("|", "/") for x in j.get("reasons", [])) or j.get("note", "")
        lines.append(f"| {q['id']} | {j['verdict']} | {why} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="folder for results.json, blind.md and the report")
    ap.add_argument("--ids", default="", help="comma-separated question ids (default: all)")
    ap.add_argument("--research", action="store_true", help="Quorum with web research")
    ap.add_argument("--judge", action="store_true", help="judge the pairs and write the report")
    ap.add_argument("--judge-model", default=JUDGE)
    args = ap.parse_args()
    questions = json.loads(QUESTIONS.read_text())["questions"]
    wanted = {i for i in args.ids.split(",") if i}
    questions = [q for q in questions if not wanted or q["id"] in wanted]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "results.json"
    results = json.loads(path.read_text()) if path.exists() else {}
    if args.judge:
        judge(results, questions, args.judge_model)
        path.write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n")
        write_blind(results, questions, out)
        (out / "README.md").write_text(report(results, questions))
        print(f"wrote {out / 'README.md'}")
        return
    model = largest_local_model()
    for q in questions:
        r = results.setdefault(q["id"], {})
        print(q["id"], flush=True)
        if "single" not in r:
            r["single"] = ask_single(q["question"], model)
            print(f"    alone: {r['single']['seconds']}s", flush=True)
        if "quorum" not in r:
            r["quorum"] = ask_quorum(q["question"], args.research)
            print(f"    quorum: {r['quorum']['status']} ({r['quorum']['seconds']}s)", flush=True)
        path.write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
