"""Does the debate change the answer, and for the better?

Runs every question in benchmarks/debate/questions.json (each has one computed, checkable answer) twice:

  single  the largest local model, alone, one call
  quorum  a Quorum debate of two rounds, web research off (the debate on its own)

and grades each agent's round-1 position, the chair's draft after round 1, and the final answer. From these: how often
the agents disagreed (some right, some wrong), how often round 2 changed the answer, whether changes fixed or broke
it, and how the council compares with its best model alone.

    uv run python scripts/benchmark_debate.py                 # all questions (hours; resumes where it stopped)
    uv run python scripts/benchmark_debate.py --ids monty,py-04
    uv run python scripts/benchmark_debate.py --report        # write the report from results.json only

Needs Quorum running (./start.sh) and Ollama. Results: docs/benchmarks/debate/.
"""

import argparse
import json
import re
import sys
import time
import urllib.request
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from backend import cli  # noqa: E402
from benchmark_review import OLLAMA, largest_local_model  # noqa: E402

QUESTIONS = ROOT / "benchmarks" / "debate" / "questions.json"
OUT = ROOT / "docs" / "benchmarks" / "debate"
ROUNDS = 2
FORMAT = "\n\nState the answer itself first (the exact value or output), then explain briefly."

# ------------------------------------------------------------------ grading

_NUM = re.compile(r"(?<![\w.])(-|−)?\$?(\d[\d,]*(?:\.\d+)?)(?:\s*/\s*(\d+))?\s*(%)?")


def _numbers(text: str) -> List[float]:
    out = []
    for sign, whole, den, pct in _NUM.findall(text):
        v = float(whole.replace(",", ""))
        if den:
            v = v / float(den)
        if pct:
            v = v / 100
        out.append(-v if sign else v)
    return out


def _expected_number(answer: str) -> Optional[float]:
    try:
        return float(Fraction(answer.replace("%", ""))) / (100 if answer.endswith("%") else 1)
    except (ValueError, ZeroDivisionError):
        return None


def graded(q: Dict[str, Any], text: str) -> bool:
    """Whether the answer states the right value: one of the accepted spellings, or (for a number) the first number it
    gives, as a fraction, decimal or percentage."""
    head = _plain_math((text or "").strip())[:240]
    if not head:
        return False
    low = head.lower()
    # a whole token: "2" doesn't match "2.5" or "12", but a sentence's full stop may follow it
    if any(re.search(rf"(?<![\w.]){re.escape(a.lower())}(?!\w|\.\d)", low) for a in q["accept"]):
        return True
    want = _expected_number(q["answer"])
    if want is None:
        return False
    got = _numbers(head)
    if not got:
        return False
    # A probability may be given as a percentage or a fraction; the first number is the answer
    return any(abs(g - w) <= max(0.0015, abs(w) * 0.006) for g in got[:1] for w in (want, want * 100))


def _plain_math(text: str) -> str:
    """LaTeX as a model writes it in plain text: \\( \\frac{2}{3} \\) reads as 2/3."""
    text = re.sub(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"\1/\2", text)
    return re.sub(r"\\[()\[\]]|\$", "", text)


def bottom_line(content: str) -> str:
    """The answer's first line after 'BOTTOM LINE:', else its first line."""
    m = re.search(r"BOTTOM\s*LINE\s*[:：]\s*(.+)", content or "", re.I)
    line = m.group(1) if m else (content or "").strip().split("\n")[0]
    return re.sub(r"[*_`]", "", line).strip()


# ------------------------------------------------------------------ running


def ask_single(q: Dict[str, Any], model: str) -> Dict[str, Any]:
    """The model alone. Like an agent in Quorum, it gets one more try without thinking when it sends no answer (a
    thinking model can use its whole context thinking), so the comparison is fair."""
    started = time.monotonic()
    text, retried = _chat(model, q["question"] + FORMAT, think=None), False
    if not text:
        text, retried = _chat(model, q["question"] + FORMAT, think=False), True
    return {
        "model": model,
        "text": text,
        "correct": graded(q, text),
        "seconds": round(time.monotonic() - started, 1),
        "retried": retried,
    }


def _chat(model: str, prompt: str, think: Optional[bool]) -> str:
    body: Dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"num_ctx": 8192, "temperature": 0.2},
    }
    if think is not None:
        body["think"] = think
    req = urllib.request.Request(f"{OLLAMA}/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        text = json.load(r)["message"]["content"]
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def ask_quorum(q: Dict[str, Any]) -> Dict[str, Any]:
    started = time.monotonic()
    body = {"question": q["question"] + FORMAT, "research_enabled": False, "max_rounds": ROUNDS}
    debate_id = cli.post("/debates", body)["debate"]["id"]
    print(f"    quorum: {cli.APP_URL}/#q/{debate_id}", flush=True)
    fixed = False
    while True:
        snap = cli.get(f"/debates/{debate_id}")
        d = snap["debate"]
        # The chair sizes a debate at intake; hold this one to two rounds so round 1 and round 2 can be compared
        if not fixed and d["status"] == "running":
            _patch(debate_id, {"max_rounds": ROUNDS})
            fixed = True
        if d["status"] in ("clarifying", "confirming"):
            cli.post(f"/debates/{debate_id}/intake/confirm")
        if d["status"] in ("concluded", "failed", "cancelled"):
            break
        time.sleep(3)
    return {"id": debate_id, "seconds": round(time.monotonic() - started, 1), **grade_debate(q, snap)}


def _patch(debate_id: str, fields: Dict[str, Any]) -> None:
    req = urllib.request.Request(
        f"{cli.API_URL}/api/debates/{debate_id}", json.dumps(fields).encode(), {"Content-Type": "application/json"},
        method="PATCH",
    )  # fmt: skip
    urllib.request.urlopen(req, timeout=30).read()


def grade_debate(q: Dict[str, Any], snap: Dict[str, Any]) -> Dict[str, Any]:
    d = snap["debate"]
    seats = {s["id"]: s for s in snap["seats"]}
    first = [m for m in snap["messages"] if m["author_kind"] == "seat" and m["round"] == 1 and m["status"] == "done"]
    agents = [
        {
            "agent": seats[m["seat_id"]]["handle"],
            "model": seats[m["seat_id"]]["model"],
            "position": m.get("position_line") or "",
            "correct": graded(q, m.get("position_line") or "") or graded(q, m.get("body") or m["content"]),
        }
        for m in first
    ]
    drafts = [x for x in snap.get("drafts", []) if x["topic"] == d["topic"]]
    draft1 = next((x for x in drafts if x["round"] == 1), None)
    verdict = next((v for v in snap["verdicts"] if v["topic"] == d["topic"]), None)
    final = next((m for m in snap["messages"] if verdict and m["id"] == verdict["message_id"]), None)
    final_line = bottom_line(final["content"]) if final and final["status"] == "done" else ""
    draft_line = bottom_line(draft1["content"]) if draft1 else ""
    return {
        "status": d["status"],
        "rounds": d["round"],
        "agents": agents,
        "draft_round1": draft_line,
        "draft_round1_correct": graded(q, draft_line) if draft1 else None,
        "final": final_line,
        "final_correct": graded(q, final_line),
    }


# ------------------------------------------------------------------ report


def summarize(results: Dict[str, Any], questions: List[Dict[str, Any]]) -> Dict[str, Any]:
    rows = [(q, results[q["id"]]) for q in questions if q["id"] in results and "quorum" in results[q["id"]]]
    n = len(rows)
    if not n:
        return {"questions": 0}
    agent_calls = [a["correct"] for _, r in rows for a in r["quorum"]["agents"]]
    split = [r for _, r in rows if len({a["correct"] for a in r["quorum"]["agents"]}) > 1]
    direct = [r for _, r in rows if not r["quorum"]["agents"]]
    with_draft = [r for _, r in rows if r["quorum"]["draft_round1_correct"] is not None]
    changed = [r for r in with_draft if r["quorum"]["draft_round1_correct"] != r["quorum"]["final_correct"]]
    fixed = [r for r in changed if r["quorum"]["final_correct"]]
    by_model: Dict[str, List[bool]] = {}
    for _, r in rows:
        for a in r["quorum"]["agents"]:
            by_model.setdefault(a["model"], []).append(a["correct"])
    best_model, best = max(by_model.items(), key=lambda kv: sum(kv[1]) / len(kv[1]))
    return {
        "questions": n,
        "single_model": rows[0][1].get("single", {}).get("model"),
        "single_correct": sum(r.get("single", {}).get("correct", False) for _, r in rows),
        "agent_accuracy": round(sum(agent_calls) / max(1, len(agent_calls)), 3),
        "best_agent_model": best_model,
        "best_agent_accuracy": round(sum(best) / len(best), 3),
        "split_questions": len(split),
        "direct": len(direct),
        "drafts": len(with_draft),
        "draft_correct": sum(bool(r["quorum"]["draft_round1_correct"]) for r in with_draft),
        "final_correct": sum(r["quorum"]["final_correct"] for _, r in rows),
        "changed": len(changed),
        "changed_fixed": len(fixed),
        "changed_broke": len(changed) - len(fixed),
        "failed": sum(r["quorum"]["status"] != "concluded" for _, r in rows),
        "quorum_minutes": round(sum(r["quorum"]["seconds"] for _, r in rows) / n / 60, 1),
        "single_seconds": round(sum(r.get("single", {}).get("seconds", 0) for _, r in rows) / n),
    }


def report(results: Dict[str, Any], questions: List[Dict[str, Any]]) -> str:
    s = summarize(results, questions)
    if not s["questions"]:
        return "# Debate benchmark\n\nNo results yet.\n"
    n = s["questions"]
    pct = lambda k: f"{s[k]} of {n} ({s[k] / n:.0%})"  # noqa: E731
    lines = [
        "# Debate benchmark: does the debate change the answer?",
        "",
        f"{n} questions with one computed answer each (Python output, math, probability): "
        "`benchmarks/debate/questions.json`. Method: `scripts/benchmark_debate.py`. Web research off, two rounds.",
        "",
        "| | Right |",
        "|---|---|",
        f"| {s['single_model']} alone | {pct('single_correct')} |",
        f"| An average council agent, round 1 | {s['agent_accuracy']:.0%} |",
        f"| Best agent in the council ({s['best_agent_model']}), round 1 | {s['best_agent_accuracy']:.0%} |",
        f"| The chair's draft after round 1 | {s['draft_correct']} of {s['drafts']} debated |",
        f"| **Quorum's final answer** | **{pct('final_correct')}** |",
        "",
        f"- The agents disagreed (some right, some wrong) on **{pct('split_questions')}** of the questions.",
        f"- Round 2 changed the answer on {s['changed']} questions: it **fixed {s['changed_fixed']}** and broke "
        f"{s['changed_broke']}.",
        f"- The chair answered {s['direct']} of {n} directly, without a debate. Those count toward Quorum's final "
        "answer but not toward the agent or disagreement numbers.",
        f"- Time: {s['quorum_minutes']} min per question for Quorum, {s['single_seconds']} s for the single model.",
        f"- Debates that ended without an answer: {s['failed']}.",
        "",
        "## Per question",
        "",
        "| Question | Answer | Alone | Agents right (round 1) | Draft | Final |",
        "|---|---|---|---|---|---|",
    ]
    mark = lambda ok: "✓" if ok else ("·" if ok is None else "✗")  # noqa: E731
    for q in questions:
        r = results.get(q["id"])
        if not r or "quorum" not in r:
            continue
        a = r["quorum"]["agents"]
        lines.append(
            f"| {q['id']} | `{q['answer']}` | {mark(r.get('single', {}).get('correct'))} | "
            f"{sum(x['correct'] for x in a)} of {len(a)} | {mark(r['quorum']['draft_round1_correct'])} | "
            f"{mark(r['quorum']['final_correct'])} |"
        )
    notes = OUT / "notes.md"
    if notes.exists():
        lines += ["", notes.read_text().strip()]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ids", default="", help="comma-separated question ids (default: all)")
    ap.add_argument("--report", action="store_true", help="only write the report from results.json")
    ap.add_argument("--no-single", action="store_true", help="skip the single-model baseline")
    ap.add_argument("--out", default=str(OUT), help="folder for results.json and the report")
    ap.add_argument("--redo-empty", action="store_true", help="ask the single model again where it sent no answer")
    args = ap.parse_args()
    questions = json.loads(QUESTIONS.read_text())["questions"]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "results.json"
    results = json.loads(path.read_text()) if path.exists() else {}
    if not args.report:
        wanted = {i for i in args.ids.split(",") if i}
        model = None if args.no_single else largest_local_model()
        for q in questions:
            if wanted and q["id"] not in wanted:
                continue
            r = results.setdefault(q["id"], {})
            if args.redo_empty and not r.get("single", {}).get("text", "x").strip():
                r.pop("single")
            print(q["id"], flush=True)
            if model and "single" not in r:
                r["single"] = ask_single(q, model)
                print(
                    f"    alone: {'right' if r['single']['correct'] else 'wrong'} ({r['single']['seconds']}s)",
                    flush=True,
                )
            if "quorum" not in r:
                r["quorum"] = ask_quorum(q)
                qr = r["quorum"]
                agents = sum(a["correct"] for a in qr["agents"])
                print(f"    quorum: agents {agents}/{len(qr['agents'])}, draft {qr['draft_round1_correct']}, "
                      f"final {qr['final_correct']} ({qr['seconds']}s)", flush=True)  # fmt: skip
            path.write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n")
    (out / "README.md").write_text(report(results, questions))
    print(f"wrote {out / 'README.md'}")


if __name__ == "__main__":
    main()
