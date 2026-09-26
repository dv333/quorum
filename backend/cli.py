"""quorum: ask the council from your terminal, scripts or CI. Talks to a running Quorum (./start.sh).

    quorum ask "Should we use Postgres or SQLite for this?"
    git diff | quorum ask --pack code-review
    quorum ask "..." --json --no-questions > answer.json
    quorum show <id> --debate > debate.md
    quorum packs
    quorum list
    quorum doctor [--ask]

Uses only the Python standard library.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

API_URL = os.getenv("QUORUM_URL", "http://127.0.0.1:8002").rstrip("/")
APP_URL = os.getenv("QUORUM_APP_URL", "http://localhost:5173").rstrip("/")
EMOJI = {
    "Otter": "🦦",
    "Panda": "🐼",
    "Koala": "🐨",
    "Penguin": "🐧",
    "Hedgehog": "🦔",
    "Bunny": "🐰",
    "Turtle": "🐢",
    "Dolphin": "🐬",
    "Beagle": "🐶",
}
POLL_SECONDS = 1.5


class QuorumError(Exception):
    pass


# ------------------------------------------------------------------ HTTP


def _request(method: str, path: str, body: Optional[Dict[str, Any]] = None, raw: bool = False) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API_URL}/api{path}", data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=300) as res:
            payload = res.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8")).get("detail")
        except Exception:
            detail = None
        raise QuorumError(detail if isinstance(detail, str) else f"{e.code} {e.reason}")
    except urllib.error.URLError:
        raise QuorumError(f"Can't reach Quorum at {API_URL}. Start it with ./start.sh (or set QUORUM_URL).")
    return payload if raw else json.loads(payload)


def get(path: str, raw: bool = False) -> Any:
    return _request("GET", path, raw=raw)


def post(path: str, body: Optional[Dict[str, Any]] = None) -> Any:
    return _request("POST", path, body or {})


# ------------------------------------------------------------------ output


class Progress:
    """One line per finished message, on stderr so stdout stays clean for the answer."""

    def __init__(self, quiet: bool) -> None:
        self.quiet = quiet
        self.seen: set = set()
        self.status: Optional[str] = None

    def say(self, text: str) -> None:
        if not self.quiet:
            print(text, file=sys.stderr, flush=True)

    def update(self, snap: Dict[str, Any]) -> None:
        d = snap["debate"]
        handles = {s["id"]: s["handle"] for s in snap["seats"]}
        if d["status"] != self.status:
            self.status = d["status"]
            if self.status == "running" and d.get("chair_handle"):
                self.say(f"Chair: {d['chair_handle']} · up to {d['max_rounds']} rounds · {len(handles)} agents")
            elif self.status == "concluding":
                self.say("The chair is writing the answer…")
        for m in snap["messages"]:
            if m["id"] in self.seen or m["status"] == "streaming":
                continue
            self.seen.add(m["id"])
            kind = m["author_kind"]
            if kind == "seat":
                h = handles.get(m["seat_id"], "?")
                stance = f" {m['stance']}" if m.get("stance") else ""
                line = (m.get("position_line") or "").strip()
                self.say(f"  {EMOJI.get(h, '·')} {h} · round {m['round']}{stance}" + (f" — {line}" if line else ""))
            elif kind == "researcher" and m["status"] == "done":
                what = {"brief": "opening brief", "factcheck": "fact-check"}.get(m.get("research_kind"), "lookup")
                n = len(m.get("sources") or [])
                self.say(f"  🐶 Beagle · {what} · {n} source{'' if n == 1 else 's'}")
            elif kind == "system" and m["status"] == "error":
                self.say(f"  ! {m['content']}")


def _ask_tty(prompt: str) -> str:
    print(prompt, end="", file=sys.stderr, flush=True)
    line = sys.stdin.readline()
    if not line:
        raise EOFError
    return line.strip()


def _answer_intake(debate_id: str, snap: Dict[str, Any], interactive: bool, progress: Progress) -> None:
    """The chair asked a question or summarized its assumptions: answer it, or skip straight to the debate."""
    d = snap["debate"]
    intake = [m for m in snap["messages"] if m["topic"] == d["topic"] and m["author_kind"] == "moderator"]
    last = intake[-1] if intake else None
    if not interactive or not last:
        progress.say("  (skipping the chair's questions)")
        post(f"/debates/{debate_id}/intake/confirm")
        return
    meta = last.get("meta") or {}
    if d["status"] == "clarifying":
        print(f"\n{d.get('chair_handle') or 'Chair'} asks: {last['content']}", file=sys.stderr)
        options: List[str] = meta.get("options") or []
        for i, o in enumerate(options, 1):
            print(f"  {i}. {o}", file=sys.stderr)
        reply = _ask_tty("Your answer (a number, your own words, or Enter to skip the questions): ")
        if not reply:
            post(f"/debates/{debate_id}/intake/confirm")
        else:
            if reply.isdigit() and 1 <= int(reply) <= len(options):
                reply = options[int(reply) - 1]
            post(f"/debates/{debate_id}/messages", {"content": reply})
    else:  # confirming
        print(f"\nThe chair's brief: {last['content']}", file=sys.stderr)
        for a in meta.get("assumptions") or []:
            print(f"  - {a}", file=sys.stderr)
        reply = _ask_tty("Start the debate? (Enter to start, or type details to add): ")
        if reply and reply.lower() not in ("y", "yes"):
            post(f"/debates/{debate_id}/messages", {"content": reply})
        else:
            post(f"/debates/{debate_id}/intake/confirm")
    print(file=sys.stderr)


def _verdict(snap: Dict[str, Any], topic: int) -> Optional[Dict[str, Any]]:
    v = next((v for v in snap["verdicts"] if v["topic"] == topic), None)
    if not v:
        return None
    msg = next((m for m in snap["messages"] if m["id"] == v["message_id"]), None)
    return v if msg and msg["status"] in ("done", "error", "stopped") else None


def wait_for_answer(debate_id: str, interactive: bool, progress: Progress) -> Dict[str, Any]:
    handled_intake: Optional[int] = None
    while True:
        snap = get(f"/debates/{debate_id}")
        progress.update(snap)
        d = snap["debate"]
        if d["status"] == "concluded" and _verdict(snap, d["topic"]):
            return snap
        if d["status"] in ("clarifying", "confirming"):
            last_id = max((m["id"] for m in snap["messages"] if m["author_kind"] == "moderator"), default=0)
            if handled_intake != last_id:  # answer each of the chair's messages once
                handled_intake = last_id
                _answer_intake(debate_id, snap, interactive, progress)
        elif d["status"] == "paused":
            post(f"/debates/{debate_id}/continue")
        time.sleep(POLL_SECONDS)


# ------------------------------------------------------------------ commands


def _result(debate_id: str, level: str, include_debate: bool, as_json: bool) -> str:
    snap = get(f"/debates/{debate_id}")
    topic = snap["debate"]["topic"]
    v = _verdict(snap, topic)
    if v and level != "standard" and v["reason"] != "direct":
        try:
            post(f"/debates/{debate_id}/verdicts/{v['id']}/level", {"level": level})
        except QuorumError as e:
            print(f"(couldn't write the {level} version: {e}; showing the standard answer)", file=sys.stderr)
    query = urllib.parse.urlencode({"level": level, "debate": str(include_debate).lower()})
    markdown = get(f"/debates/{debate_id}/export?{query}", raw=True)
    if not as_json:
        return markdown
    answer_msg = next((m for m in snap["messages"] if v and m["id"] == v["message_id"]), None)
    totals = (snap.get("metrics") or {}).get(str(topic), {}).get("totals", {})
    return json.dumps(
        {
            "id": debate_id,
            "title": snap["debate"].get("title"),
            "url": f"{APP_URL}/#q/{debate_id}",
            "reason": v and v["reason"],
            "rounds": v and v["rounds"],
            "answer": answer_msg and answer_msg["content"],
            "markdown": markdown,
            "metrics": totals,
        },
        indent=2,
        ensure_ascii=False,
    )


def cmd_ask(args: argparse.Namespace) -> int:
    typed = " ".join(args.question).strip()
    piped = not sys.stdin.isatty()
    extra = sys.stdin.read().strip() if piped else ""
    question = f"{typed}\n\n{extra}".strip()
    if args.pack:
        pack = next((p for p in get("/packs") if p["id"] == args.pack), None)
        if not pack:
            raise QuorumError(f"Unknown topic pack '{args.pack}'. See: quorum packs")
        if not typed and extra:  # only piped content: the pack's question starter introduces it
            question = f"{pack.get('prompt') or ''}{extra}".strip()
    if not question:
        raise QuorumError('Ask something, for example: quorum ask "Is Rust worth learning in 2026?"')

    body: Dict[str, Any] = {"question": question, "pack": args.pack}
    if args.no_research:
        body["research_enabled"] = False
    snap = post("/debates", body)
    debate_id = snap["debate"]["id"]
    progress = Progress(args.quiet)
    progress.say(f"Conundrum {debate_id} · watch it live at {APP_URL}/#q/{debate_id}")
    interactive = not args.no_questions and not piped and sys.stdin.isatty()
    try:
        wait_for_answer(debate_id, interactive, progress)
    except KeyboardInterrupt:
        post(f"/debates/{debate_id}/stop")
        print(f"\nPaused. Pick it up in the app: {APP_URL}/#q/{debate_id}", file=sys.stderr)
        return 130
    print(_result(debate_id, args.level, args.debate, args.json))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    print(_result(args.id, args.level, args.debate, args.json))
    return 0


DOCTOR_QUESTION = """Here is Quorum's health report, measured from my last {n} conundrums on this machine (Quorum is the app you are running in: a council of local models that debate a question in rounds, then a chair writes the answer).

{report}

Diagnose it like an engineer: which problems matter most for answer quality and speed, the likely cause of each, and the concrete fix (a setting, a model change, or a code change), in priority order. Only use the numbers above; say what else we'd need to measure where they aren't enough."""


def cmd_doctor(args: argparse.Namespace) -> int:
    report = get(f"/diagnostics?last={args.last}&text=true", raw=True)
    if args.json:
        print(json.dumps(get(f"/diagnostics?last={args.last}"), indent=2, ensure_ascii=False))
        return 0
    print(report)
    if not args.ask:
        return 0
    print("\nAsking the council for an improvement plan…", file=sys.stderr)
    snap = post("/debates", {"question": DOCTOR_QUESTION.format(n=args.last, report=report), "research_enabled": False})
    debate_id = snap["debate"]["id"]
    progress = Progress(args.quiet)
    progress.say(f"Conundrum {debate_id} · watch it live at {APP_URL}/#q/{debate_id}")
    try:
        wait_for_answer(debate_id, interactive=False, progress=progress)
    except KeyboardInterrupt:
        post(f"/debates/{debate_id}/stop")
        print(f"\nPaused. Pick it up in the app: {APP_URL}/#q/{debate_id}", file=sys.stderr)
        return 130
    print("\n" + _result(debate_id, "standard", False, False))
    return 0


def cmd_packs(args: argparse.Namespace) -> int:
    packs = get("/packs")
    if args.json:
        print(json.dumps(packs, indent=2, ensure_ascii=False))
        return 0
    width = max(len(p["id"]) for p in packs) if packs else 0
    for p in packs:
        mine = "" if p.get("builtin") else "  (yours)"
        print(f"{p['id'].ljust(width)}  {p.get('emoji', ' ')} {p['name']}: {p['description']}{mine}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    debates = get(f"/debates?limit={max(1, args.n)}")
    if args.json:
        print(json.dumps(debates, indent=2, ensure_ascii=False))
        return 0
    for d in debates:
        title = d.get("title") or d["question"].splitlines()[0][:60]
        print(f"{d['id']}  {d['created_at'][:10]}  {d['status']:<10} {title}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="quorum", description="Ask a council of local AI models from your terminal.")
    sub = parser.add_subparsers(dest="command", required=True)

    def answer_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("--level", choices=["simple", "standard", "expert"], default="standard", help="reading level")
        p.add_argument("--debate", action="store_true", help="include the whole debate after the answer")
        p.add_argument("--json", action="store_true", help="print JSON instead of Markdown")

    ask = sub.add_parser("ask", help="ask the council a question (also reads stdin)")
    ask.add_argument("question", nargs="*", help="your question; piped input is appended to it")
    ask.add_argument("--pack", help="a topic pack id (see: quorum packs)")
    ask.add_argument("--no-questions", action="store_true", help="skip the chair's clarifying questions")
    ask.add_argument("--no-research", action="store_true", help="don't search the web")
    ask.add_argument("-q", "--quiet", action="store_true", help="no progress lines on stderr")
    answer_options(ask)
    ask.set_defaults(func=cmd_ask)

    show = sub.add_parser("show", help="print an existing conundrum's answer")
    show.add_argument("id")
    answer_options(show)
    show.set_defaults(func=cmd_show)

    packs = sub.add_parser("packs", help="list topic packs")
    packs.add_argument("--json", action="store_true")
    packs.set_defaults(func=cmd_packs)

    doctor = sub.add_parser("doctor", help="health report from your recent conundrums")
    doctor.add_argument("--last", type=int, default=20, help="how many recent conundrums to measure (default 20)")
    doctor.add_argument("--ask", action="store_true", help="then ask the council for an improvement plan")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("-q", "--quiet", action="store_true", help="no progress lines on stderr")
    doctor.set_defaults(func=cmd_doctor)

    ls = sub.add_parser("list", help="list recent conundrums")
    ls.add_argument("-n", type=int, default=15, help="how many (default 15)")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=cmd_list)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except QuorumError as e:
        print(f"quorum: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
