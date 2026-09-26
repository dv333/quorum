"""Debate engine: round-robin turns, interjections, stance-based consensus, rolling
summaries, a web Researcher, and the chair's synthesized final answer.

One DebateEngine per debate. All state lives in SQLite; the engine only keeps the
text of in-flight streams in memory so late subscribers can catch up.
"""

import asyncio
import json
import logging
import re
import time
from datetime import date
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from . import db, firecrawl, inventory, prompts
from .config import (
    MAX_NUM_CTX,
    MAX_ROUNDS_LIMIT,
    REPLY_RESERVE_TOKENS,
    CONTEXT_BUDGET_FRACTION,
    INTAKE_MAX_QUESTIONS,
    MIN_ROUNDS_FOR_CONSENSUS,
    MIN_SEATS,
    RESEARCH_MAX_CLAIMS,
    RESEARCH_MAX_QUERIES,
    RESEARCH_MAX_SOURCES,
    RESEARCH_SOURCES_PER_CLAIM,
    RESEARCH_REQUESTS_PER_ROUND,
    RESEARCH_RESULTS_PER_QUERY,
    RESEARCHER_NAME,
)
from .parsing import (
    MENTION_RE,
    ThinkSplitter,
    parse_json_loose,
    parse_research_requests,
    parse_stance,
    plain_answer,
    strip_thinking,
)
from .providers import ChatClient, Endpoint

log = logging.getLogger(__name__)

MetaLookup = Callable[[int, str, int], Awaitable[Optional[Dict[str, Any]]]]
PlanLookup = Callable[[List[Dict[str, Any]], int], Awaitable[Dict[str, Any]]]
SearchFn = Callable[[str, int], Awaitable[List[Dict[str, str]]]]

PRIOR_TOPICS_IN_CONTEXT = 3
ACTIVE_STATUSES = ("running", "concluding", "researching", "intake")
# Waiting on the user during the chair's clarifying interview
INTAKE_WAITING = ("clarifying", "confirming")


def estimate_tokens(text: str) -> int:
    return len(text) // 3 + 1


class EventBus:
    def __init__(self) -> None:
        self._subscribers: List[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    def publish(self, event: Dict[str, Any]) -> None:
        for q in list(self._subscribers):
            q.put_nowait(event)


# ---------------------------------------------------------------------------
# Serialization helpers (shared with the API layer)
# ---------------------------------------------------------------------------


def serialize_message(row: Dict[str, Any], partial: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    msg = dict(row)
    if partial is not None:
        msg["content"], msg["thinking"] = partial["content"], partial["thinking"]
    msg["body"] = parse_stance(msg["content"]).body if msg["author_kind"] == "seat" else msg["content"]
    msg["stance_parsed"] = bool(msg["stance_parsed"])
    msg["sources"] = db.loads(msg.pop("sources_json", None), [])
    msg["meta"] = db.loads(msg.pop("meta_json", None), None)
    return msg


# Roles every council gets (fewer for tiny councils), and the defaults used to fill any the chair leaves out
DEFAULT_ROLES = [
    ("Domain expert", "Brings the specialist knowledge this question needs."),
    ("Skeptic", "Finds the strongest reasons the emerging answer is wrong."),
    ("Pragmatist", "Weighs cost, effort and what is realistic to do."),
    ("User advocate", "Keeps the answer grounded in the person's situation and constraints."),
    ("Risk analyst", "Looks for what could go wrong and how to limit it."),
    ("Evidence checker", "Separates facts from assumptions and asks for sources."),
    ("Optimist", "Makes the best case for the most promising option."),
    ("Contrarian", "Tests the options nobody else is considering."),
]
_ROLE_MATCH = {
    "Skeptic": re.compile(r"skeptic|sceptic|critic|devil|red team", re.I),
    "Pragmatist": re.compile(r"pragmat|practical|feasib|cost|operator", re.I),
    "User advocate": re.compile(r"user|customer|client|advocate for", re.I),
}


def required_roles(n: int) -> List[str]:
    return (
        ["Skeptic"] if n <= 2 else ["Skeptic", "Pragmatist"] if n == 3 else ["Skeptic", "Pragmatist", "User advocate"]
    )


def settle_roles(
    handles: List[str], proposed: Dict[str, Dict[str, str]], required: List[str]
) -> Dict[str, Dict[str, str]]:
    """Every agent gets a distinct role. The chair's proposal is kept where usable; agents it left out get the missing
    required roles first, then defaults. If the chair skipped a required role for everyone, it replaces a default role
    before any of the chair's own choices. Returned in seat order."""
    roles: Dict[str, Dict[str, str]] = {}
    by_chair = set()
    used = set()
    for h in handles:
        r = proposed.get(h.lower())
        if r and r["role"].lower() not in used:
            roles[h] = r
            by_chair.add(h)
            used.add(r["role"].lower())
    defaults = dict(DEFAULT_ROLES)

    def covered(need: str) -> bool:
        return any(_ROLE_MATCH[need].search(r["role"]) for r in roles.values())

    # With nothing from the chair, the default order already puts the required roles early
    missing_required = [(n, defaults[n]) for n in required if not covered(n)] if by_chair else []
    spare = missing_required + [
        (n, f) for n, f in DEFAULT_ROLES if n.lower() not in used and (n, f) not in missing_required
    ]
    for h in handles:
        if h not in roles:
            name, focus = spare.pop(0) if spare else (f"Perspective {len(used) + 1}", "")
            roles[h] = {"role": name, "focus": focus}
            used.add(name.lower())
    for need in required:
        if covered(need):
            continue
        essential = lambda h: any(_ROLE_MATCH[x].search(roles[h]["role"]) for x in required)  # noqa: E731
        candidates = [h for h in reversed(handles) if not essential(h)]
        candidates.sort(key=lambda h: h in by_chair)  # defaults go before the chair's own choices
        if candidates:
            roles[candidates[0]] = {"role": need, "focus": defaults[need]}
    return {h: roles[h] for h in handles}


_WORD = re.compile(r"[a-z]{4,}|\d[\d.,]*%?", re.IGNORECASE)


def _most_related(passage: str, messages: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """The messages sharing the most words (numbers count triple) with a passage, kept in debate order."""
    want = {w.lower().rstrip(".,") for w in _WORD.findall(passage)}

    def score(m: Dict[str, Any]) -> float:
        have = {w.lower().rstrip(".,") for w in _WORD.findall(m["content"])}
        shared = want & have
        return sum(3 if w[0].isdigit() else 1 for w in shared) / (1 + len(have)) ** 0.25

    ranked = sorted(messages, key=score, reverse=True)[:limit]
    return sorted(ranked, key=lambda m: m["id"])


def _parse_queries(text: str, limit: int) -> List[str]:
    queries = []
    for line in strip_thinking(text).splitlines():
        q = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip().strip("\"'`").strip()
        if q and q.upper() != "NONE" and len(q) > 2 and q not in queries:
            queries.append(q[:200])
    return queries[:limit]


_TERM = re.compile(r"[a-z0-9]{4,}")


def _related_pages(
    pages: List[Dict[str, Any]], claim: str, exclude: List[Dict[str, Any]], limit: int = 2
) -> List[Dict[str, Any]]:
    """The pages from earlier research that best match a claim (strongest evidence first among close matches), so the
    check can use the reviews the research already found even when the claim's own search misses them."""
    terms = set(_TERM.findall(claim.lower()))
    taken = {s["url"] for s in exclude}
    scored = []
    for p in pages:
        if p["url"] in taken:
            continue
        taken.add(p["url"])
        words = set(_TERM.findall(f"{p.get('title', '')} {p.get('content', '')}".lower()))
        hits = len(terms & words)
        if hits >= max(3, len(terms) // 3):
            scored.append((hits + 2 * p.get("evidence", 0), p))
    return [p for _, p in sorted(scored, key=lambda x: -x[0])[:limit]]


_NUM = re.compile(r"\d+(?:\.\d+)?")
STUDY_PAGE_CHARS = 4500  # characters of each page the key-studies step reads
# Models describe what's missing instead of leaving it out
_MISSING = re.compile(r"not (?:stated|specified|provided|given|reported)|unspecified|snippet|excerpt|provided text", re.I)


def _numbers_in(text: str, source: str) -> bool:
    """Whether every number in `text` also appears in the source (thousands separators ignored)."""
    plain = source.replace(",", "")
    return all(n in plain for n in _NUM.findall(text.replace(",", "")))


def _clip(text: str, limit: int) -> str:
    """Shorten to the limit at a sentence end when possible, so a finding never stops mid-number."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("; "))
    return cut[: end + 1] if end > limit // 2 else cut.rsplit(" ", 1)[0] + "…"


def check_studies(raw: List[Any], pages: List[Dict[str, Any]], limit: int = 5) -> List[Dict[str, str]]:
    """Keep the studies whose quote is really on their page and whose finding's numbers are too; drop any year or size
    the page doesn't state."""
    out, seen = [], set()
    for s in raw:
        if not isinstance(s, dict):
            continue
        try:
            page = pages[int(s.get("source")) - 1]
        except (TypeError, ValueError, IndexError):
            continue
        field = {
            k: _clip(" ".join(str(s.get(k) or "").split()), 600 if k in ("finding", "quote") else 200)
            for k in ("name", "year", "design", "participants", "finding", "quote")
        }
        text = f"{page.get('title', '')} {page['url']} {page.get('content', '')}"
        if not field["name"] or not field["finding"] or page["url"] in seen:
            continue
        if not quote_in_source(field["quote"], page.get("content", "")) or not _numbers_in(field["finding"], text):
            continue
        if _MISSING.search(field["finding"]):
            continue
        if not re.fullmatch(r"(19|20)\d\d", field["year"]) or field["year"] not in text:
            field["year"] = ""
        for k in ("design", "participants"):
            if not _numbers_in(field[k], text) or _MISSING.search(field[k]):
                field[k] = ""
        if not re.search(r"\d", field["participants"]):
            field["participants"] = ""
        seen.add(page["url"])
        out.append({**field, "url": page["url"], "title": page.get("title", "")})
    return out[:limit]


def _interleave(result_lists: List[List[Dict[str, str]]], limit: int) -> List[Dict[str, str]]:
    """Take results round-robin across queries so every query contributes, deduplicated by URL, with the strongest
    evidence (systematic reviews, then randomized trials) and official documentation moved to the front."""
    # Social posts, videos and shop listings only when nothing else was found
    useful = [[r for r in results if not firecrawl.is_low_value(r["url"])] for results in result_lists]
    if any(useful):
        result_lists = useful
    seen, out = set(), []
    for i in range(max((len(r) for r in result_lists), default=0)):
        for results in result_lists:
            if i < len(results) and results[i]["url"] not in seen:
                r = results[i]
                seen.add(r["url"])
                out.append(
                    {
                        **r,
                        "primary": firecrawl.is_primary(r["url"]),
                        "evidence": firecrawl.evidence_level(r.get("title", ""), r.get("content", ""), r["url"]),
                    }
                )
    # Strongest evidence first (systematic reviews, then trials), then official documentation
    out.sort(key=lambda s: -(2 * s["evidence"] + s["primary"]))
    return out[:limit]


_LEDGER_LABEL = {
    "supported": "Supported",
    "partly": "Partly supported",
    "contradicted": "Contradicted",
    "unknown": "Unverified",
}


def _ledger_markdown(claims: List[Dict[str, Any]], sources: List[Dict[str, str]]) -> str:
    lines = []
    for i, c in enumerate(claims, 1):
        n = next((j for j, s in enumerate(sources, 1) if s["url"] == c.get("source_url")), None)
        line = f"{i}. **{_LEDGER_LABEL.get(c['status'], 'Unverified')}**: {c['claim']}"
        if c.get("caveat"):
            line += f" — {c['caveat']}"
        if c.get("quote"):
            line += f" “{c['quote']}”" + (f" [{n}]" if n else "")
        lines.append(line)
    return "\n".join(lines)


_EVIDENCE_Q = re.compile(r"\b(evidence|studies|study|research|trials?|meta-?analys\w*|scientific|science)\b", re.I)
_FILLER = set(
    "what does do did the a an of for to in on and or vs versus is are be say says show shows about how which better "
    "best evidence studies study research science scientific really actually current latest way run running locally "
    "should which that with this it its we our my i".split()
)


def _topic(question: str, limit: int = 8) -> str:
    """The question's subject in a few search words (the question itself, without the criteria after it)."""
    main = question.split("?")[0]
    return " ".join([w for w in re.findall(r"[a-z0-9][a-z0-9-]*", main.lower()) if w not in _FILLER][:limit])


_REQ_TAIL = re.compile(r"\?\s*([^?]+?)[.!]?\s*$")


def stated_requirements(question: str) -> List[str]:
    """The criteria listed after the question itself, like "…? Speed, cost, quantization." """
    m = _REQ_TAIL.search(question.strip())
    if not m:
        return []
    parts = [p.strip(" .;:") for p in re.split(r",|;|\band\b", m.group(1))]
    return [p for p in parts if p and len(p.split()) <= 4][:6]


def requirement_covered(requirement: str, text: str) -> bool:
    """Whether the answer talks about a criterion at all (each word's stem appears somewhere)."""
    words = [w for w in re.findall(r"[a-z0-9]+", requirement.lower()) if len(w) > 2]
    low = text.lower()
    return all(w[:5] in low for w in words)


def criteria_queries(question: str) -> List[str]:
    """One search per stated criterion, so each gets its own sources (prices, benchmarks, reliability data)."""
    topic = _topic(question, 9)
    return [f"{topic} {r.lower()}" for r in stated_requirements(question)][:3] if topic else []


_CALC = re.compile(
    r"((?:\$?\d[\d,]*(?:\.\d+)?\s*[A-Za-z%]{0,6}\s*[×x*÷/+−-]\s*)+\$?\d[\d,]*(?:\.\d+)?)\s*[A-Za-z%]{0,6}\s*"
    r"(?:=|≈|~|≃)\s*~?\s*\$?(\d[\d,]*(?:\.\d+)?)"
)


def check_arithmetic(text: str) -> List[Dict[str, str]]:
    """Calculations written out in the answer ("30 × 4.5 ÷ 8 ≈ 17") whose result is off by more than 15%."""
    problems = []
    for m in _CALC.finditer(text):
        expr = re.sub(r"[A-Za-z%$,\s]", "", m.group(1).replace("×", "*").replace("÷", "/").replace("−", "-"))
        expr = re.sub(r"(?<=\d)x(?=\d)", "*", expr)
        if not re.fullmatch(r"[\d.*/+-]+", expr) or not re.search(r"[*/+-]", expr):
            continue
        try:
            value = eval(expr, {"__builtins__": {}})  # digits and operators only, checked above
            stated = float(m.group(2).replace(",", ""))
        except Exception:
            continue
        if stated and abs(value - stated) / abs(stated) > 0.15:
            problems.append(
                {"text": m.group(0).strip(), "issue": f"the arithmetic is wrong: it comes to about {value:.3g}, not {m.group(2)}"}
            )
    return problems


def evidence_queries(question: str) -> List[str]:
    """Extra searches for evidence questions: the newest meta-analysis and any Cochrane review on the topic."""
    if not _EVIDENCE_Q.search(question):
        return []
    topic = _topic(question)
    if not topic:
        return []
    return [
        f"{topic} meta-analysis {date.today().year}",
        f"{topic} Cochrane review",
        f"{topic} network meta-analysis randomized trials",
    ]


_NORM = re.compile(r"[^a-z0-9]+")


def quote_in_source(quote: str, text: str) -> bool:
    """Whether a quoted passage really appears in the source, ignoring case, spacing and punctuation. Models tidy
    quotes (merged sentences, "…", a dropped word), so a long quote counts when most of its four-word runs are in the
    page; a short one has to match exactly."""
    q = _NORM.sub(" ", quote.lower()).strip()
    if len(q) < 12:
        return False
    source = _NORM.sub(" ", text.lower())
    if q in source:
        return True
    words = q.split()
    if len(words) < 8:
        return False
    runs = [" ".join(words[i : i + 4]) for i in range(len(words) - 3)]
    return sum(run in source for run in runs) / len(runs) >= 0.6


CLAIM_STATUSES = ("supported", "partly", "contradicted", "unknown")


class DebateEngine:
    def __init__(
        self,
        debate_id: str,
        client: Optional[ChatClient] = None,
        meta_lookup: Optional[MetaLookup] = None,
        plan_lookup: Optional[PlanLookup] = None,
        search_fn: Optional[SearchFn] = None,
    ) -> None:
        self.id = debate_id
        self.client = client or ChatClient()
        self.meta_lookup = meta_lookup or inventory.model_meta
        self.plan_lookup = plan_lookup or inventory.plan
        self.search_fn = search_fn or firecrawl.search
        self.bus = EventBus()
        self.task: Optional[asyncio.Task] = None
        self.partials: Dict[int, Dict[str, str]] = {}
        self.mode = "parallel"
        self.research_queue: List[Dict[str, Any]] = []
        self._requests_per_round: Dict[Tuple[int, int], int] = {}
        self._search_down: Optional[str] = None
        self._research_pages: Dict[int, List[Dict[str, Any]]] = {}  # pages Beagle read, per topic, for the claim check
        self._concluding = False
        self._cmd_lock = asyncio.Lock()

    # ------------------------------------------------------------------ state

    def debate(self) -> Dict[str, Any]:
        d = db.query_one("SELECT * FROM debates WHERE id = ?", [self.id])
        if d is None:
            raise KeyError(self.id)
        d["criteria"] = db.loads(d.pop("criteria_json"), [])
        d["pack"] = db.loads(d.pop("pack_json", None), None)
        d["autopilot"] = bool(d["autopilot"])
        d["research_enabled"] = bool(d["research_enabled"])
        return d

    def seats(self) -> List[Dict[str, Any]]:
        return db.query("SELECT * FROM seats WHERE debate_id = ? ORDER BY position", [self.id])

    def _endpoint(self, endpoint_id: int) -> Endpoint:
        ep = inventory.endpoint(endpoint_id)
        if ep is None:
            raise RuntimeError(f"Endpoint {endpoint_id} no longer exists")
        return ep

    def _researcher_model(self, d: Dict[str, Any]) -> Tuple[int, str]:
        if d.get("researcher_endpoint_id") and d.get("researcher_model"):
            return d["researcher_endpoint_id"], d["researcher_model"]
        return d["chair_endpoint_id"], d["chair_model"]

    def _set(self, **fields: Any) -> None:
        db.update("debates", self.id, **fields)
        self.bus.publish({"type": "debate_updated", "debate": self.debate()})

    def _insert_message(
        self,
        *,
        topic: int,
        round_no: int,
        author_kind: str,
        seat_id: Optional[int] = None,
        content: str = "",
        status: str = "done",
        **extra: Any,
    ) -> Dict[str, Any]:
        fields = {
            "debate_id": self.id,
            "topic": topic,
            "round": round_no,
            "author_kind": author_kind,
            "seat_id": seat_id,
            "content": content,
            "status": status,
            "created_at": db.now(),
            **extra,
        }
        msg_id = db.execute(
            f"INSERT INTO messages ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})",
            list(fields.values()),
        )
        row = db.query_one("SELECT * FROM messages WHERE id = ?", [msg_id])
        self.bus.publish({"type": "message_created", "message": serialize_message(row)})
        return row

    def _system_message(self, text: str, meta: Optional[Dict[str, Any]] = None) -> None:
        d = self.debate()
        self._insert_message(
            topic=d["topic"],
            round_no=d["round"],
            author_kind="system",
            content=text,
            **({"meta_json": json.dumps(meta)} if meta else {}),
        )

    def _finish_message(self, msg_id: int, **fields: Any) -> Dict[str, Any]:
        db.update("messages", msg_id, **fields)
        row = db.query_one("SELECT * FROM messages WHERE id = ?", [msg_id])
        msg = serialize_message(row)
        self.bus.publish({"type": "message_updated", "message": msg})
        return msg

    def is_running(self) -> bool:
        return self.task is not None and not self.task.done()

    # --------------------------------------------------------------- commands

    async def post_user_message(self, content: str) -> None:
        content = content.strip()
        if not content:
            return
        requests = parse_research_requests(content, limit=1)
        if not requests and MENTION_RE.search(content):
            requests = [MENTION_RE.sub("", content).strip()]
        async with self._cmd_lock:
            d = self.debate()
            status = d["status"]
            if status in ("idle", "concluded"):
                if requests and MENTION_RE.match(content) and d["topic"] > 0:
                    # A lookup between questions: research only, no new debate round
                    self._insert_message(topic=d["topic"], round_no=d["round"], author_kind="user", content=content)
                    self._queue_research(requests[0], "You", "request")
                    self._set(status="researching")
                    self._start(self._research_only(status))
                    return
                topic = d["topic"] + 1
                self._set(topic=topic, round=0, status="intake")
                self._insert_message(topic=topic, round_no=0, author_kind="user", content=content)
                self._start(self._intake())
                return

            if status in INTAKE_WAITING or status == "intake":
                # An answer to the chair's question, or extra details for its summary
                self._insert_message(topic=d["topic"], round_no=0, author_kind="user", content=content)
                if status in INTAKE_WAITING:
                    self._set(status="intake")
                    self._start(self._intake())
                return

            self._insert_message(topic=d["topic"], round_no=d["round"], author_kind="user", content=content)
            if requests:
                self._queue_research(requests[0], "You", "request")
            if status == "paused":
                self._set(status="running")
                self._start(self._run_rounds())
            # running / concluding / researching: the interjection and any request are
            # picked up before the next speaker

    async def confirm_intake(self) -> None:
        """The user accepted the chair's summary (or skipped the interview): start the debate."""
        async with self._cmd_lock:
            d = self.debate()
            if d["status"] not in (*INTAKE_WAITING, "intake"):
                return
            await self._cancel_task()
            self._start(self._begin_debate())

    async def continue_(self) -> None:
        async with self._cmd_lock:
            if self.debate()["status"] == "paused" and not self.is_running():
                self._set(status="running")
                self._start(self._run_rounds())

    async def stop(self) -> None:
        async with self._cmd_lock:
            await self._cancel_task()
            self.research_queue.clear()
            if self.debate()["status"] in ("running", "concluding"):
                self._set(status="paused")

    async def conclude(self) -> None:
        async with self._cmd_lock:
            d = self.debate()
            if d["status"] not in ("running", "paused") or d["topic"] == 0:
                return
            await self._cancel_task()
            self._start(self._conclude("manual"))

    async def set_autopilot(self, enabled: bool) -> None:
        self._set(autopilot=int(enabled))
        if enabled:
            await self.continue_()

    async def shutdown(self) -> None:
        await self._cancel_task()

    def _start(self, coro: Awaitable[None]) -> None:
        self._search_down = None  # retry web search on every new run
        self.task = asyncio.create_task(self._guard(coro))

    async def _guard(self, coro: Awaitable[None]) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as e:  # never leave a debate stuck in "running"
            log.exception("debate %s crashed", self.id)
            self._system_message(f"The debate engine hit an error: {e}")
            self._set(status="paused")

    async def _cancel_task(self) -> None:
        if self.is_running():
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
        self.task = None

    # ---------------------------------------------------------------- intake

    def _intake_messages(self, topic: int) -> List[Dict[str, Any]]:
        rows = db.query(
            "SELECT * FROM messages WHERE debate_id = ? AND topic = ? AND round = 0 "
            "AND author_kind IN ('user', 'moderator') ORDER BY id",
            [self.id, topic],
        )
        return [serialize_message(r) for r in rows]

    async def _intake(self) -> None:
        """The chair decides whether to ask a clarifying question, summarize its assumptions, or just start."""
        d = self.debate()
        if d["chair_mode"] == "auto" and not d["chair_handle"]:
            await self._pick_roles()
            d = self.debate()
        msgs = self._intake_messages(d["topic"])
        question = msgs[0]["content"] if msgs else ""
        history, asked, summarized = [], 0, False
        for m in msgs[1:]:
            if m["author_kind"] == "moderator":
                kind = (m["meta"] or {}).get("kind")
                asked += kind == "question"
                summarized |= kind == "summary"
                text = m["content"] if kind == "question" else "Summary: " + m["content"]
                history.append({"who": "chair", "text": text})
            else:
                history.append({"who": "user", "text": m["content"]})
        must_summarize = summarized or asked >= INTAKE_MAX_QUESTIONS
        label = self._chair_label(d)
        choice: Dict[str, Any] = {}
        for _attempt in range(2):  # one retry when the reply can't be used at all
            try:
                text = await self._complete(
                    label,
                    "intake",
                    d["chair_endpoint_id"],
                    d["chair_model"],
                    prompts.intake_messages(label, question, history, asked, INTAKE_MAX_QUESTIONS, must_summarize),
                    think=await self._thinking_flag(d["chair_endpoint_id"], d["chair_model"], False),
                )
                choice = parse_json_loose(text)
            except Exception as e:
                log.warning("intake failed: %s", e)
            if choice.get("action") in ("ask", "summarize", "clear", "direct"):
                break
        action = choice.get("action")
        # The chair sizes the debate: 1 round for simple questions, up to MAX_ROUNDS_LIMIT for hard ones
        try:
            rounds = int(choice.get("rounds"))
            # Questions about what the evidence says need at least one round of rebuttal
            floor = 2 if _EVIDENCE_Q.search(self._question(d["topic"])) else 1
            self._set(max_rounds=max(floor, min(MAX_ROUNDS_LIMIT, rounds)))
        except (TypeError, ValueError):
            pass
        if action == "direct" and not asked and not summarized:
            await self._answer_directly()
            return
        if action == "ask" and not must_summarize and str(choice.get("question") or "").strip():
            options = [str(o).strip()[:80] for o in (choice.get("options") or []) if str(o).strip()][:4]
            self._insert_message(
                topic=d["topic"],
                round_no=0,
                author_kind="moderator",
                content=str(choice["question"]).strip()[:400],
                meta_json=json.dumps(
                    {
                        "kind": "question",
                        "options": options,
                        "n": asked + 1,
                        "max": INTAKE_MAX_QUESTIONS,
                        "chair": label,
                    }
                ),
            )
            self._set(status="clarifying")
            self.bus.publish({"type": "attention", "reason": "question"})
            return
        # Summarize when asked to, when forced (question budget spent or details added after a summary),
        # or when the chair considers it clear after already asking something
        if action == "summarize" or must_summarize or (asked and action != "ask"):
            brief = str(choice.get("brief") or question).strip()[:600]
            raw = choice.get("assumptions") or []
            if isinstance(raw, str):
                raw = [raw]
            # Some models pack several assumptions into one string, one per line
            assumptions = [
                part.strip(" -•*\t")[:200]
                for a in raw
                for part in re.split(r"\n+|;\s+", str(a))
                if part.strip(" -•*\t")
            ][:6]
            if not assumptions:
                assumptions = [f"{h['text']}" for h in history if h["who"] == "user"][:5]
            self._insert_message(
                topic=d["topic"],
                round_no=0,
                author_kind="moderator",
                content=brief,
                meta_json=json.dumps({"kind": "summary", "assumptions": assumptions, "chair": label}),
            )
            self._set(status="confirming")
            self.bus.publish({"type": "attention", "reason": "summary"})
            return
        # Clear enough (or the chair's reply was unusable): start right away
        await self._begin_debate()

    async def _answer_directly(self) -> None:
        """Trivial conundrums (greetings, simple facts) get a short answer from the chair, no council."""
        d = self.debate()
        label = self._chair_label(d)
        self._set(status="concluding")
        row = self._insert_message(topic=d["topic"], round_no=0, author_kind="chair", status="streaming")
        try:
            messages = prompts.direct_answer_messages(label, self._question(d["topic"]), self._prior_topics(d["topic"]))
            think = await self._thinking_flag(d["chair_endpoint_id"], d["chair_model"], False)
            stats = await self._stream_into(
                row["id"], d["chair_endpoint_id"], d["chair_model"], messages, think, None, label, "answer"
            )
            partial = self.partials[row["id"]]
            self._finish_message(
                row["id"],
                content=strip_thinking(partial["content"]).strip() or "…",
                thinking=partial["thinking"],
                tokens=stats.get("tokens"),
                tok_per_s=stats.get("tok_per_s"),
                prompt_tokens=stats.get("prompt_tokens"),
                duration_ms=stats.get("duration_ms"),
                status="done",
            )
        except Exception as e:
            self._finish_message(row["id"], status="error", content=f"The chair ({d['chair_model']}) failed: {e}")
        finally:
            self.partials.pop(row["id"], None)
        vid = db.execute(
            "INSERT INTO verdicts (debate_id, topic, reason, rounds, message_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            [self.id, d["topic"], "direct", 0, row["id"], db.now()],
        )
        self.bus.publish(
            {"type": "verdict_created", "verdict": db.query_one("SELECT * FROM verdicts WHERE id = ?", [vid])}
        )
        self._set(status="concluded")

    async def _begin_debate(self) -> None:
        d = self.debate()
        self._set(status="running")
        await self._assign_roles()
        if d["research_enabled"]:
            self._queue_research(self._question(d["topic"]), None, "brief")
        await self._run_rounds()

    # ------------------------------------------------------------ run rounds

    async def _refresh_mode(self) -> None:
        d = self.debate()
        selection = [{"endpoint_id": s["endpoint_id"], "model": s["model"]} for s in self.seats()]
        selection.append({"endpoint_id": d["chair_endpoint_id"], "model": d["chair_model"]})
        if d["research_enabled"]:
            ep_id, model = self._researcher_model(d)
            selection.append({"endpoint_id": ep_id, "model": model})
        try:
            self.mode = (await self.plan_lookup(selection, d["num_ctx"]))["mode"]
        except Exception:
            self.mode = "parallel"

    def _keep_alive(self, current: Dict[str, Any], upcoming: Optional[Dict[str, Any]]) -> Any:
        if self.mode == "parallel":
            return "15m"
        # Sequential: unload unless the next speaker reuses this exact model
        same = (
            upcoming is not None
            and upcoming["endpoint_id"] == current["endpoint_id"]
            and upcoming["model"] == current["model"]
        )
        return "5m" if same else 0

    def _round_messages(self, topic: int, round_no: int) -> List[Dict[str, Any]]:
        return db.query(
            "SELECT * FROM messages WHERE debate_id = ? AND topic = ? AND round = ? AND seat_id IS NOT NULL ORDER BY id",
            [self.id, topic, round_no],
        )

    async def _run_rounds(self) -> None:
        d = self.debate()
        if d["chair_mode"] == "auto" and not d["chair_handle"]:
            await self._pick_roles()
        await self._refresh_mode()
        seats = self.seats()
        try:
            while True:
                d = self.debate()
                await self._drain_research(d["round"])  # opening brief, or requests made while paused
                round_no = d["round"]
                spoken = {m["seat_id"] for m in self._round_messages(d["topic"], round_no)}
                if round_no == 0 or all(s["id"] in spoken for s in seats):
                    round_no += 1
                    self._set(round=round_no)
                    pending = seats
                else:  # resume a round that was stopped midway
                    pending = [s for s in seats if s["id"] not in spoken]

                for i, seat in enumerate(pending):
                    await self._drain_research(round_no)
                    upcoming = pending[i + 1] if i + 1 < len(pending) else seats[0]
                    await self._seat_turn(seat, round_no, upcoming)
                await self._drain_research(round_no)

                d = self.debate()
                done = [
                    m
                    for m in self._round_messages(d["topic"], round_no)
                    if m["author_kind"] == "seat" and m["status"] in ("done", "stopped")
                ]
                if len(done) < MIN_SEATS:
                    self._system_message(
                        "Fewer than two agents responded this round, so the debate is paused. "
                        "Check that the models are running, then continue."
                    )
                    self._set(status="paused")
                    return

                finished = [m for m in done if m["status"] == "done"]
                if round_no >= MIN_ROUNDS_FOR_CONSENSUS and finished and all(m["stance"] == "AGREE" for m in finished):
                    reason = "consensus"
                    break
                if round_no >= d["max_rounds"]:
                    reason = "max_rounds"
                    break

                await self._maybe_summarize(round_no)
                await self._write_draft(round_no)
                if not self.debate()["autopilot"]:
                    self._set(status="paused")
                    return
        except asyncio.CancelledError:
            if not self._concluding:
                db.update("debates", self.id, status="paused")
                self.bus.publish({"type": "debate_updated", "debate": self.debate()})
            raise
        await self._conclude(reason)

    async def _thinking_flag(self, endpoint_id: int, model: str, wanted: Optional[bool]) -> Optional[bool]:
        """Only send 'think' to Ollama models that advertise the capability."""
        ep = self._endpoint(endpoint_id)
        if ep.kind != "ollama":
            return None
        try:
            meta = await self.meta_lookup(endpoint_id, model, self.debate()["num_ctx"])
        except Exception:
            meta = None
        if not meta or not meta.get("thinking"):
            return None
        return wanted

    async def _stream_into(
        self,
        msg_id: int,
        endpoint_id: int,
        model: str,
        messages: List[Dict[str, str]],
        think: Optional[bool],
        keep_alive: Any,
        actor: str,
        kind: str,
    ) -> Dict[str, Any]:
        """Stream a reply into an existing message row, publishing deltas. Records usage; returns stats."""
        started = time.monotonic()
        partial = self.partials.setdefault(msg_id, {"content": "", "thinking": ""})
        stats: Dict[str, Any] = {}
        ep = self._endpoint(endpoint_id)
        splitter = ThinkSplitter()  # defensive: route any inline <think> text to the thinking channel

        def emit(content: str, thinking: str) -> None:
            if thinking:
                partial["thinking"] += thinking
                self.bus.publish({"type": "message_delta", "id": msg_id, "thinking": thinking})
            if content:
                partial["content"] += content
                self.bus.publish({"type": "message_delta", "id": msg_id, "content": content})

        async for chunk in self.client.stream(
            ep, model, messages, think=think, num_ctx=self._num_ctx(messages), keep_alive=keep_alive
        ):
            if chunk.kind == "content":
                emit(*splitter.feed(chunk.text))
            elif chunk.kind == "thinking":
                emit("", chunk.text)
            elif chunk.kind == "done":
                stats = chunk.stats
        emit(*splitter.flush())
        stats = {**stats, "duration_ms": int((time.monotonic() - started) * 1000)}
        stats["prompt_tokens"] = self._record(actor, model, kind, stats, messages)
        return stats

    async def _complete(
        self,
        actor: str,
        kind: str,
        endpoint_id: int,
        model: str,
        messages: List[Dict[str, str]],
        think: Optional[bool],
        topic: Optional[int] = None,
    ) -> str:
        """Non-streamed model call (plans, summaries, picks, rewrites) with usage recorded."""
        started = time.monotonic()
        parts, stats = [], {}
        async for chunk in self.client.stream(
            self._endpoint(endpoint_id), model, messages, think=think, num_ctx=self._num_ctx(messages)
        ):
            if chunk.kind == "content":
                parts.append(chunk.text)
            elif chunk.kind == "done":
                stats = chunk.stats
        stats = {**stats, "duration_ms": int((time.monotonic() - started) * 1000)}
        self._record(actor, model, kind, stats, messages, topic=topic)
        return "".join(parts)

    # ---------------------------------------------------------------- metrics

    def _record(
        self,
        actor: str,
        model: str,
        kind: str,
        stats: Dict[str, Any],
        messages: Optional[List[Dict[str, str]]] = None,
        searches: int = 0,
        pages: int = 0,
        topic: Optional[int] = None,
    ) -> int:
        """Log one model call or search batch. Prompt tokens are estimated when the server doesn't report them."""
        topic = self.debate()["topic"] if topic is None else topic
        prompt_tokens = stats.get("prompt_tokens")
        if prompt_tokens is None and messages:
            prompt_tokens = estimate_tokens("".join(m["content"] for m in messages))
        db.execute(
            "INSERT INTO usage (debate_id, topic, actor, model, kind, prompt_tokens, output_tokens, duration_ms, "
            "searches, pages, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                self.id,
                topic,
                actor,
                model,
                kind,
                prompt_tokens or 0,
                stats.get("tokens") or 0,
                stats.get("duration_ms") or 0,
                searches,
                pages,
                db.now(),
            ],
        )
        self.bus.publish({"type": "metrics_updated", "topic": topic, "metrics": self.metrics(topic)})
        return prompt_tokens or 0

    def metrics(self, topic: int) -> Dict[str, Any]:
        rows = db.query("SELECT * FROM usage WHERE debate_id = ? AND topic = ? ORDER BY id", [self.id, topic])
        actors: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            a = actors.setdefault(
                r["actor"],
                {
                    "actor": r["actor"],
                    "models": [],
                    "duration_ms": 0,
                    "prompt_tokens": 0,
                    "output_tokens": 0,
                    "calls": 0,
                    "searches": 0,
                    "pages": 0,
                },
            )
            if r["model"] not in a["models"]:
                a["models"].append(r["model"])
            for k in ("duration_ms", "prompt_tokens", "output_tokens", "searches", "pages"):
                a[k] += r[k]
            a["calls"] += 0 if r["kind"] == "search" else 1
        for a in actors.values():
            gen_s = a["duration_ms"] / 1000
            a["tok_per_s"] = round(a["output_tokens"] / gen_s, 1) if gen_s and a["output_tokens"] else None
        totals = {
            k: sum(a[k] for a in actors.values())
            for k in ("duration_ms", "prompt_tokens", "output_tokens", "searches", "pages", "calls")
        }
        return {"actors": list(actors.values()), "totals": totals}

    def _chair_label(self, d: Dict[str, Any]) -> str:
        if d.get("chair_handle"):
            return d["chair_handle"]
        for s in self.seats():
            if s["endpoint_id"] == d["chair_endpoint_id"] and s["model"] == d["chair_model"]:
                return s["handle"]
        return "Chair"

    # ------------------------------------------------------------ chair pick

    async def _describe_members(self, seats: List[Dict[str, Any]]) -> Tuple[List[Dict[str, str]], Dict[int, Any]]:
        """Each member's model, family, size and whether it reasons (for choosing the chair and assigning roles)."""
        d = self.debate()
        metas = {}
        for s in seats:
            try:
                metas[s["id"]] = await self.meta_lookup(s["endpoint_id"], s["model"], d["num_ctx"]) or {}
            except Exception:
                metas[s["id"]] = {}
        members = []
        for s in seats:
            m = metas[s["id"]]
            bits = [b for b in (m.get("family"), m.get("params")) if b]
            if m.get("thinking"):
                bits.append("reasoning model")
            members.append(
                {"handle": s["handle"], "description": f"{s['model']}" + (f" ({', '.join(bits)})" if bits else "")}
            )
        return members, metas

    async def _pick_roles(self) -> None:
        """The largest council model reads the question and picks the chair, the researcher model and a title."""
        d = self.debate()
        seats = self.seats()
        members, metas = await self._describe_members(seats)
        picker = max(seats, key=lambda s: metas[s["id"]].get("est_bytes") or 0)
        by_name = {s["handle"].lower(): s for s in seats}
        choice: Dict[str, Any] = {}
        try:
            text = await self._complete(
                picker["handle"],
                "pick",
                picker["endpoint_id"],
                picker["model"],
                prompts.pick_roles_messages(self._question(d["topic"]), members),
                think=await self._thinking_flag(picker["endpoint_id"], picker["model"], False),
            )
            choice = parse_json_loose(text)
        except Exception as e:
            log.warning("chair pick failed: %s", e)
        chair = by_name.get(str(choice.get("chair", "")).strip().lower()) or picker
        researcher = by_name.get(str(choice.get("researcher", "")).strip().lower()) or chair
        reason = str(choice.get("reason") or "").strip().strip(".")[:80]
        fields: Dict[str, Any] = {
            "chair_endpoint_id": chair["endpoint_id"],
            "chair_model": chair["model"],
            "chair_handle": chair["handle"],
            "chair_reason": reason,
            "chair_picked_by": picker["handle"],
        }
        if not d.get("researcher_model"):
            fields.update(
                researcher_endpoint_id=researcher["endpoint_id"],
                researcher_model=researcher["model"],
                researcher_handle=researcher["handle"],
            )
        title = str(choice.get("title") or "").strip().strip('".')[:60]
        if title and not d["title"]:
            fields["title"] = title
        self._set(**fields)
        why = f" — “{reason}”" if reason else ""
        if chair["id"] == picker["id"]:
            self._system_message(f"{chair['handle']} will chair{why}.")
        else:
            self._system_message(f"{picker['handle']} chose {chair['handle']} to chair{why}.")

    # ----------------------------------------------------------------- roles

    async def _assign_roles(self) -> None:
        """The chair gives every agent a distinct role for this conundrum. A Skeptic, a Pragmatist and a User advocate
        are always included (fewer for tiny councils); anything the chair leaves out is filled in from a default set."""
        d = self.debate()
        seats = self.seats()
        if not seats:
            return
        required = required_roles(len(seats))
        members, _ = await self._describe_members(seats)
        proposed: Dict[str, Dict[str, str]] = {}
        try:
            text = await self._complete(
                self._chair_label(d),
                "roles",
                d["chair_endpoint_id"],
                d["chair_model"],
                prompts.assign_roles_messages(
                    self._question(d["topic"]), members, required, (d["pack"] or {}).get("guidance", "")
                ),
                await self._thinking_flag(d["chair_endpoint_id"], d["chair_model"], False),
            )
            for item in parse_json_loose(text).get("roles") or []:
                if isinstance(item, dict) and str(item.get("role") or "").strip():
                    proposed[str(item.get("agent") or "").strip().lower()] = {
                        "role": str(item["role"]).strip().strip(".")[:28],
                        "focus": str(item.get("focus") or "").strip()[:120],
                    }
        except Exception as e:
            log.warning("role assignment failed: %s", e)
        roles = settle_roles([s["handle"] for s in seats], proposed, required)
        for s in seats:
            r = roles[s["handle"]]
            db.update("seats", s["id"], role=r["role"], role_focus=r["focus"])
        self.bus.publish({"type": "seats_updated", "seats": self._public_seats()})
        self._system_message(
            "Roles: " + " · ".join(f"{s['handle']}, {roles[s['handle']]['role']}" for s in seats),
            meta={"kind": "roles", "roles": [{"handle": s["handle"], **roles[s["handle"]]} for s in seats]},
        )

    # --------------------------------------------------------- reading level

    async def rewrite_level(self, verdict_id: int, level: str) -> str:
        """Rewrite a final answer for another reading level; cached per verdict."""
        cached = db.query_one(
            "SELECT content FROM verdict_levels WHERE verdict_id = ? AND level = ?", [verdict_id, level]
        )
        if cached:
            return cached["content"]
        v = db.query_one("SELECT * FROM verdicts WHERE id = ? AND debate_id = ?", [verdict_id, self.id])
        if not v:
            raise KeyError(verdict_id)
        answer = db.query_one("SELECT content FROM messages WHERE id = ?", [v["message_id"]])["content"]
        d = self.debate()
        text = strip_thinking(
            await self._complete(
                self._chair_label(d),
                "rewrite",
                d["chair_endpoint_id"],
                d["chair_model"],
                prompts.level_messages(self._question(v["topic"]), answer, level),
                think=await self._thinking_flag(d["chair_endpoint_id"], d["chair_model"], False),
                topic=v["topic"],
            )
        ).strip()
        if not text:
            raise RuntimeError("The chair returned an empty rewrite")
        db.execute(
            "INSERT OR REPLACE INTO verdict_levels (verdict_id, level, content) VALUES (?, ?, ?)",
            [verdict_id, level, text],
        )
        return text

    async def _seat_turn(self, seat: Dict[str, Any], round_no: int, upcoming: Optional[Dict[str, Any]]) -> None:
        d = self.debate()
        row = self._insert_message(
            topic=d["topic"],
            round_no=round_no,
            author_kind="seat",
            seat_id=seat["id"],
            status="streaming",
            meta_json=json.dumps({"role": seat["role"]}) if seat.get("role") else None,
        )
        msg_id = row["id"]
        self.partials[msg_id] = {"content": "", "thinking": ""}
        try:
            messages = self._turn_context(seat, round_no, exclude_id=msg_id)
            think = await self._thinking_flag(seat["endpoint_id"], seat["model"], bool(seat["thinking_enabled"]))
            stats = await self._stream_into(
                msg_id,
                seat["endpoint_id"],
                seat["model"],
                messages,
                think,
                self._keep_alive(seat, upcoming),
                seat["handle"],
                "turn",
            )
            partial = self.partials[msg_id]
            content = strip_thinking(partial["content"])
            if not content.strip():
                raise RuntimeError("returned an empty reply")
            st = parse_stance(content)
            self._finish_message(
                msg_id,
                content=content,
                thinking=partial["thinking"],
                stance=st.stance,
                position_line=st.position,
                stance_parsed=int(st.parsed),
                tokens=stats.get("tokens"),
                tok_per_s=stats.get("tok_per_s"),
                prompt_tokens=stats.get("prompt_tokens"),
                duration_ms=stats.get("duration_ms"),
                status="done",
            )
            if d["research_enabled"]:
                self._queue_agent_requests(seat, st.body, d["topic"], round_no)
        except asyncio.CancelledError:
            partial = self.partials.get(msg_id, {"content": "", "thinking": ""})
            content = strip_thinking(partial["content"])
            st = parse_stance(content)
            self._finish_message(
                msg_id,
                content=content,
                thinking=partial["thinking"],
                stance=st.stance if st.parsed else None,
                position_line=st.position,
                stance_parsed=int(st.parsed),
                status="stopped",
            )
            raise
        except Exception as e:
            log.warning("seat %s (%s) failed: %s", seat["handle"], seat["model"], e)
            self._finish_message(
                msg_id, author_kind="system", status="error", content=f"{seat['handle']} ({seat['model']}) failed: {e}"
            )
        finally:
            self.partials.pop(msg_id, None)

    # --------------------------------------------------------------- research

    def _queue_research(self, request: str, requested_by: Optional[str], kind: str) -> None:
        self.research_queue.append({"request": request, "requested_by": requested_by, "kind": kind})

    def _queue_agent_requests(self, seat: Dict[str, Any], body: str, topic: int, round_no: int) -> None:
        key = (topic, round_no)
        for request in parse_research_requests(body, limit=1):
            if self._requests_per_round.get(key, 0) >= RESEARCH_REQUESTS_PER_ROUND:
                self._system_message(
                    f"Research limit for round {round_no} reached; skipped {seat['handle']}'s request: {request}"
                )
                continue
            self._requests_per_round[key] = self._requests_per_round.get(key, 0) + 1
            self._queue_research(request, seat["handle"], "request")

    async def _drain_research(self, round_no: int) -> None:
        while self.research_queue:
            item = self.research_queue.pop(0)
            await self._research(item, round_no)

    async def _research_only(self, previous_status: str) -> None:
        try:
            await self._drain_research(self.debate()["round"])
        finally:
            db.update("debates", self.id, status=previous_status)
            self.bus.publish({"type": "debate_updated", "debate": self.debate()})

    def _log(self, msg_id: int, line: str) -> None:
        text = line + "\n"
        self.partials[msg_id]["thinking"] += text
        self.bus.publish({"type": "message_delta", "id": msg_id, "thinking": text})

    async def _research(self, item: Dict[str, Any], round_no: int) -> Optional[str]:
        """Plan searches, run them through Firecrawl, and stream a cited brief. Returns the brief."""
        d = self.debate()
        kind = item["kind"]
        row = self._insert_message(
            topic=d["topic"],
            round_no=round_no,
            author_kind="researcher",
            status="streaming",
            research_kind=kind,
            research_request=item["request"],
            requested_by=item["requested_by"],
        )
        msg_id = row["id"]
        self.partials[msg_id] = {"content": "", "thinking": ""}
        ep_id, model = self._researcher_model(d)
        try:
            if self._search_down:
                raise firecrawl.SearchError(self._search_down)
            think = await self._thinking_flag(ep_id, model, False)
            question = self._question(d["topic"])
            plan = prompts.research_plan_messages(item["request"], question, RESEARCH_MAX_QUERIES)
            self._log(msg_id, "Planning searches…")
            queries = _parse_queries(
                await self._complete(RESEARCHER_NAME, "research", ep_id, model, plan, think), RESEARCH_MAX_QUERIES
            )
            if not queries:
                queries = [item["request"][:200]]
            limit = RESEARCH_MAX_SOURCES
            if kind == "brief":
                # For "what does the evidence say" questions, always look for the newest syntheses too, and give each
                # criterion the question lists ("speed, cost…") its own search
                extra = criteria_queries(question)
                queries += [q for q in evidence_queries(question) + extra if q not in queries]
                limit += len(extra)

            for q in queries:
                self._log(msg_id, f"Searching: {q}")
            search_started = time.monotonic()
            results = await asyncio.gather(
                *[self.search_fn(q, RESEARCH_RESULTS_PER_QUERY) for q in queries], return_exceptions=True
            )
            search_ms = int((time.monotonic() - search_started) * 1000)
            errors = [r for r in results if isinstance(r, Exception)]
            ok = [r for r in results if not isinstance(r, Exception)]
            if not ok:
                raise errors[0]
            sources = _interleave(ok, limit)
            self._research_pages.setdefault(d["topic"], []).extend(_interleave(ok, 100))
            self._record(
                RESEARCHER_NAME,
                "firecrawl",
                "search",
                {"duration_ms": search_ms},
                searches=len(queries),
                pages=len(sources),
            )
            if not sources:
                self._finish_message(
                    msg_id,
                    content="I couldn't find any sources for this.",
                    thinking=self.partials[msg_id]["thinking"],
                    status="done",
                )
                return None
            self._log(msg_id, "Reading: " + ", ".join(urlparse(s["url"]).netloc for s in sources))

            request = item["request"]
            stats = await self._stream_into(
                msg_id,
                ep_id,
                model,
                prompts.research_brief_messages(request, item["requested_by"], sources, kind),
                think,
                None,
                RESEARCHER_NAME,
                "research",
            )
            partial = self.partials[msg_id]
            content = strip_thinking(partial["content"]).strip() or "The sources didn't answer this."
            stored_sources = [
                {"url": s["url"], "title": s["title"], "evidence": s["evidence"], "primary": s["primary"]} for s in sources
            ]
            self._finish_message(
                msg_id,
                content=content,
                thinking=partial["thinking"],
                sources_json=json.dumps(stored_sources),
                tokens=stats.get("tokens"),
                tok_per_s=stats.get("tok_per_s"),
                prompt_tokens=stats.get("prompt_tokens"),
                duration_ms=stats.get("duration_ms"),
                status="done",
            )
            return content + prompts.sources_block(stored_sources)
        except asyncio.CancelledError:
            partial = self.partials.get(msg_id, {"content": "", "thinking": ""})
            self._finish_message(
                msg_id, content=strip_thinking(partial["content"]), thinking=partial["thinking"], status="stopped"
            )
            raise
        except firecrawl.SearchError as e:
            if "reach" in str(e) or "API key" in str(e):
                self._search_down = str(e)  # don't retry every request in this run
            self._finish_message(
                msg_id, status="error", content=f"Web search failed: {e}", thinking=self.partials[msg_id]["thinking"]
            )
        except Exception as e:
            log.warning("researcher failed: %s", e)
            self._finish_message(
                msg_id,
                status="error",
                content=f"The Researcher ({model}) failed: {e}",
                thinking=self.partials[msg_id]["thinking"],
            )
        finally:
            self.partials.pop(msg_id, None)
        return None

    # ---------------------------------------------------------------- context

    def _question(self, topic: int) -> str:
        """The user's conundrum, plus anything the chair's clarifying interview established."""
        msgs = self._intake_messages(topic)
        if not msgs:
            return ""
        question = msgs[0]["content"]
        summary = next((m for m in reversed(msgs) if (m["meta"] or {}).get("kind") == "summary"), None)
        if summary:
            points = "\n".join(f"- {a}" for a in summary["meta"].get("assumptions", []))
            return f"{question}\n\nClarified with the user: {summary['content']}\nAssumptions:\n{points}"
        qa, pending = [], None
        for m in msgs[1:]:
            if m["author_kind"] == "moderator":
                pending = m["content"]
            elif pending:
                qa.append(f"- {pending} {m['content']}")
                pending = None
        return f"{question}\n\nClarified with the user:\n" + "\n".join(qa) if qa else question

    def _prior_topics(self, topic: int) -> List[Dict[str, str]]:
        rows = db.query(
            "SELECT v.topic, m.content FROM verdicts v LEFT JOIN messages m ON m.id = v.message_id "
            "WHERE v.debate_id = ? AND v.topic < ? ORDER BY v.topic DESC LIMIT ?",
            [self.id, topic, PRIOR_TOPICS_IN_CONTEXT],
        )
        return [
            {"question": self._question(r["topic"]), "verdict": strip_thinking(r["content"] or "(no verdict)")}
            for r in reversed(rows)
        ]

    def _latest_summary(self, topic: int) -> Optional[Dict[str, Any]]:
        return db.query_one(
            "SELECT * FROM summaries WHERE debate_id = ? AND topic = ? ORDER BY upto_round DESC LIMIT 1",
            [self.id, topic],
        )

    def _verbatim(self, topic: int, after_round: int, exclude_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Messages not yet folded into the summary. Round-0 items (question, opening brief) always stay."""
        rows = db.query(
            "SELECT * FROM messages WHERE debate_id = ? AND topic = ? AND (round > ? OR round = 0) "
            "AND author_kind IN ('user', 'seat', 'researcher') AND status IN ('done', 'stopped') ORDER BY id",
            [self.id, topic, after_round],
        )
        return [{**r, "sources": db.loads(r["sources_json"], [])} for r in rows if r["id"] != exclude_id]

    def _handles(self) -> Dict[int, str]:
        return {s["id"]: s["handle"] for s in self.seats()}

    def _num_ctx(self, messages: List[Dict[str, str]]) -> int:
        """The debate's context size, grown in 4K steps (up to MAX_NUM_CTX) when the prompt would leave too little
        room for the reply; otherwise a long answer prompt silently cuts the answer short."""
        base = self.debate()["num_ctx"]
        need = sum(estimate_tokens(m["content"]) for m in messages) + REPLY_RESERVE_TOKENS
        if need <= base:
            return base
        return max(base, min(MAX_NUM_CTX, -(-need // 4096) * 4096))

    def _budget(self) -> int:
        return int(self.debate()["num_ctx"] * CONTEXT_BUDGET_FRACTION)

    def _turn_context(self, seat: Dict[str, Any], round_no: int, exclude_id: int) -> List[Dict[str, str]]:
        d = self.debate()
        handles = self._handles()
        summary = self._latest_summary(d["topic"])
        upto = summary["upto_round"] if summary else 0
        msgs = self._verbatim(d["topic"], upto, exclude_id)
        # Round-0 user messages (the question) are passed separately
        msgs = [m for m in msgs if not (m["round"] == 0 and m["author_kind"] == "user")]
        if round_no == 1:
            # Independent first positions: no other agent's round-1 turn, nor the lookups they asked for
            msgs = [
                m
                for m in msgs
                if not (
                    m["round"] == 1
                    and (m["author_kind"] == "seat" or (m["author_kind"] == "researcher" and m["requested_by"]))
                )
            ]

        def build(ms: List[Dict[str, Any]]) -> List[Dict[str, str]]:
            return prompts.turn_messages(
                handle=seat["handle"],
                others=[h for sid, h in handles.items() if sid != seat["id"]],
                criteria=d["criteria"],
                custom_rubric=d["custom_rubric"],
                question=self._question(d["topic"]),
                prior_topics=self._prior_topics(d["topic"]),
                summary=summary["content"] if summary else None,
                summary_upto=upto,
                transcript=prompts.render_transcript(ms, handles, me=seat["id"]),
                round_no=round_no,
                max_rounds=d["max_rounds"],
                research=d["research_enabled"],
                guidance=(d["pack"] or {}).get("guidance", ""),
                role={"role": seat["role"], "focus": seat.get("role_focus") or ""} if seat.get("role") else None,
                roster=roster,
            )

        others = [s for s in self.seats() if s["id"] != seat["id"]]
        roster = [f"{s['handle']} ({s['role']})" if s.get("role") else s["handle"] for s in others]
        messages = build(msgs)
        # Hard cap: if still over budget (summary pending or failed), drop the oldest debate turns, keeping research
        keep_min = len(handles)
        while len(msgs) > keep_min and sum(estimate_tokens(m["content"]) for m in messages) > self._budget():
            drop = next((i for i, m in enumerate(msgs) if m["author_kind"] != "researcher"), 0)
            msgs = msgs[:drop] + msgs[drop + 1 :]
            messages = build(msgs)
        return messages

    async def _maybe_summarize(self, round_no: int) -> None:
        """Fold every round except the latest into the chair's rolling summary when the transcript grows too big."""
        d = self.debate()
        summary = self._latest_summary(d["topic"])
        upto = summary["upto_round"] if summary else 0
        if round_no - 1 <= upto:
            return
        verbatim = [m for m in self._verbatim(d["topic"], upto) if m["round"] > 0]
        if sum(estimate_tokens(m["content"]) for m in verbatim) < self._budget() * 0.5:
            return
        older = [m for m in verbatim if m["round"] < round_no]
        if not older:
            return
        text = prompts.render_transcript(older, self._handles())
        try:
            think = await self._thinking_flag(d["chair_endpoint_id"], d["chair_model"], False)
            content = await self._complete(
                self._chair_label(d),
                "summary",
                d["chair_endpoint_id"],
                d["chair_model"],
                prompts.summary_messages(self._question(d["topic"]), summary["content"] if summary else None, text),
                think,
            )
            content = strip_thinking(content)
        except Exception as e:
            self._system_message(
                f"The chair couldn't summarize older rounds ({e}); oldest messages will be trimmed instead."
            )
            return
        sid = db.execute(
            "INSERT INTO summaries (debate_id, topic, upto_round, content, source_messages, source_tokens, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [self.id, d["topic"], round_no - 1, content, len(older), estimate_tokens(text), db.now()],
        )
        self.bus.publish(
            {"type": "summary_created", "summary": db.query_one("SELECT * FROM summaries WHERE id = ?", [sid])}
        )

    # ---------------------------------------------------------- living answer

    def _drafts(self, topic: int) -> List[Dict[str, Any]]:
        return db.query("SELECT * FROM drafts WHERE debate_id = ? AND topic = ? ORDER BY id", [self.id, topic])

    async def _write_draft(self, round_no: int) -> None:
        """After each round that isn't the last, the chair updates its draft answer so the user can watch it improve."""
        d = self.debate()
        previous = self._drafts(d["topic"])
        if previous and previous[-1]["round"] >= round_no:
            return
        turns = [m for m in self._round_messages(d["topic"], round_no) if m["author_kind"] == "seat" and m["content"]]
        if not turns:
            return
        try:
            think = await self._thinking_flag(d["chair_endpoint_id"], d["chair_model"], False)
            text = await self._complete(
                self._chair_label(d),
                "draft",
                d["chair_endpoint_id"],
                d["chair_model"],
                prompts.draft_messages(
                    question=self._question(d["topic"]),
                    previous_draft=previous[-1]["content"] if previous else None,
                    transcript=prompts.render_transcript(turns, self._handles()),
                    positions=self._final_positions(d["topic"]),
                    round_no=round_no,
                ),
                think,
            )
        except Exception as e:
            log.warning("draft after round %s failed: %s", round_no, e)
            return
        text = strip_thinking(text).strip()
        changed = ""
        m = re.search(r"^\s*[*_]*CHANGED[*_]*\s*:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
        if m:
            changed = m.group(1).strip()
            text = text[: m.start()].strip()
        if not text:
            return
        did = db.execute(
            "INSERT INTO drafts (debate_id, topic, round, content, changed, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            [self.id, d["topic"], round_no, text, changed[:300], db.now()],
        )
        self.bus.publish({"type": "draft_created", "draft": db.query_one("SELECT * FROM drafts WHERE id = ?", [did])})

    # ----------------------------------------------------------- claim ledger

    async def _check_claims(self, positions: List[Dict[str, Any]], round_no: int) -> List[Dict[str, Any]]:
        """Before the answer: the chair lists the material claims the answer will rely on, Beagle searches for each,
        and each gets a status backed by an exact quote from a source. Quotes that aren't really in the source don't
        count. Returns the ledger (also stored, and posted in the thread as Beagle's fact-check)."""
        d = self.debate()
        topic = d["topic"]
        row = self._insert_message(
            topic=topic,
            round_no=round_no,
            author_kind="researcher",
            status="streaming",
            research_kind="factcheck",
            research_request="Check the claims the answer relies on",
        )
        msg_id = row["id"]
        self.partials[msg_id] = {"content": "", "thinking": ""}
        ep_id, model = self._researcher_model(d)
        try:
            if self._search_down:
                raise firecrawl.SearchError(self._search_down)
            summary = self._latest_summary(topic)
            self._log(msg_id, "Listing the claims the answer relies on…")
            text = await self._complete(
                self._chair_label(d),
                "claims",
                d["chair_endpoint_id"],
                d["chair_model"],
                prompts.claims_messages(
                    self._question(topic), positions, summary["content"] if summary else None, RESEARCH_MAX_CLAIMS
                ),
                await self._thinking_flag(d["chair_endpoint_id"], d["chair_model"], False),
            )
            items, seen = [], set()
            for c in parse_json_loose(text).get("claims") or []:
                claim = str((c or {}).get("claim") or "").strip() if isinstance(c, dict) else ""
                if claim and claim.lower() not in seen:
                    seen.add(claim.lower())
                    site = re.sub(r"^https?://", "", str(c.get("docs_site") or "").strip().lower()).split("/")[0]
                    items.append(
                        {
                            "claim": claim[:300],
                            "query": str(c.get("query") or claim).strip()[:200],
                            "site": site if re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", site) else "",
                        }
                    )
            items = items[:RESEARCH_MAX_CLAIMS]
            if not items:
                self._finish_message(msg_id, content="Nothing in the final positions needed checking.", status="done")
                return []
            for it in items:
                self._log(msg_id, f"Checking: {it['claim']}")
            started = time.monotonic()
            # Each claim is searched as asked and, when it names one, on the vendor's documentation site
            site_q = {id(it): f"site:{it['site']} {it['query']}" for it in items if it["site"]}
            queries = [it["query"] for it in items] + list(site_q.values())
            claim_of = {it["query"]: it["claim"] for it in items} | {
                site_q[id(it)]: it["claim"] for it in items if id(it) in site_q
            }
            # Pages are trimmed to the passages about the claim itself, so a caveat like "uses middleware" survives
            results = await asyncio.gather(
                *[self.search_fn(q, RESEARCH_SOURCES_PER_CLAIM, focus=claim_of[q]) for q in queries],
                return_exceptions=True,
            )
            by_query = dict(zip(queries, results))
            found = []
            for it in items:
                parts = [by_query[it["query"]]] + ([by_query[site_q[id(it)]]] if id(it) in site_q else [])
                ok = [r for r in parts if not isinstance(r, Exception)]
                found.append(ok[0] + [s for r in ok[1:] for s in r] if ok else parts[0])
            pages = sum(len(r) for r in results if not isinstance(r, Exception))
            self._record(
                RESEARCHER_NAME,
                "firecrawl",
                "search",
                {"duration_ms": int((time.monotonic() - started) * 1000)},
                searches=len(queries),
                pages=pages,
            )
            outage = next((r for r in found if isinstance(r, Exception)), None)
            if outage and all(isinstance(r, Exception) for r in found):
                self._log(msg_id, f"Web search failed: {outage}")
            think = await self._thinking_flag(ep_id, model, False)
            ledger: List[Dict[str, Any]] = []
            for it, result in zip(items, found):
                fresh = [] if isinstance(result, Exception) else _interleave([result], RESEARCH_SOURCES_PER_CLAIM + 1)
                sources = fresh + _related_pages(self._research_pages.get(topic, []), it["claim"], fresh)
                entry = {"claim": it["claim"], "status": "unknown", "quote": "", "caveat": "", "source": None}
                if not sources:
                    entry["caveat"] = (
                        f"Web search failed ({result}), so this couldn't be checked."
                        if isinstance(result, Exception)
                        else "No sources found."
                    )
                else:
                    try:
                        v = parse_json_loose(
                            await self._complete(
                                RESEARCHER_NAME,
                                "verify",
                                ep_id,
                                model,
                                prompts.verify_claim_messages(it["claim"], sources),
                                think,
                            )
                        )
                    except Exception as e:
                        log.warning("claim check failed: %s", e)
                        v = {}
                    status = str(v.get("status") or "").strip().lower()
                    status = "partly" if status.startswith("part") else status
                    quote = str(v.get("quote") or "").strip().strip('"“”')
                    try:
                        src = sources[int(v.get("source")) - 1]
                    except (TypeError, ValueError, IndexError):
                        src = None
                    caveat = str(v.get("caveat") or "").strip()[:300]
                    if status in CLAIM_STATUSES and status != "unknown":
                        # The passage has to really be in the page, or the verdict doesn't count
                        if src and quote_in_source(quote, src["content"]):
                            entry.update(status=status, quote=quote[:500], source=src, caveat=caveat)
                        else:
                            entry["caveat"] = (
                                f"Judged {status}, but the quote isn't in the source, so it stays unverified."
                            )
                ledger.append(entry)
            stored = []
            for e in ledger:
                src = e.pop("source")
                e["source_url"], e["source_title"] = (src["url"], src["title"]) if src else (None, None)
                db.execute(
                    "INSERT INTO claims (debate_id, topic, claim, status, quote, caveat, source_url, source_title, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        self.id,
                        topic,
                        e["claim"],
                        e["status"],
                        e["quote"],
                        e["caveat"],
                        e["source_url"],
                        e["source_title"],
                        db.now(),
                    ],
                )
                stored.append(e)
            sources_out: List[Dict[str, str]] = []
            for e in stored:
                if e["source_url"] and all(s["url"] != e["source_url"] for s in sources_out):
                    sources_out.append({"url": e["source_url"], "title": e["source_title"] or e["source_url"]})
            note = (
                f"Web search failed, so these claims stay unverified: {outage}\n\n"
                if outage and all(isinstance(r, Exception) for r in found)
                else ""
            )
            self._finish_message(
                msg_id,
                content=note + _ledger_markdown(stored, sources_out),
                sources_json=json.dumps(sources_out),
                status="done",
            )
            self.bus.publish({"type": "claims_created", "topic": topic, "claims": self._claims(topic)})
            return stored
        except asyncio.CancelledError:
            self._finish_message(msg_id, status="stopped")
            raise
        except firecrawl.SearchError as e:
            if "reach" in str(e) or "API key" in str(e):
                self._search_down = str(e)
            self._finish_message(msg_id, status="error", content=f"Web search failed: {e}")
        except Exception as e:
            log.warning("claim check failed: %s", e)
            self._finish_message(msg_id, status="error", content=f"The claim check ({model}) failed: {e}")
        finally:
            self.partials.pop(msg_id, None)
        return []

    async def _key_studies(self, topic: int) -> List[Dict[str, str]]:
        """The strongest studies the research read (reviews and trials first), with details checked against their
        pages, for the chair to lead with and for the answer's "Key studies" section."""
        pages, seen = [], set()
        for p in sorted(self._research_pages.get(topic, []), key=lambda p: -p.get("evidence", 0)):
            if p.get("evidence", 0) >= 1 and p["url"] not in seen:
                seen.add(p["url"])
                pages.append(p)
        question = self._question(topic)
        # Each page re-read around its results, where the numbers are
        pages = [
            {
                **p,
                "content": firecrawl.relevant_excerpt(
                    p.get("raw") or p["content"], f"{question} {firecrawl.RESULTS_TERMS}", STUDY_PAGE_CHARS
                ),
            }
            for p in pages[:5]  # five pages fit an 8K context with room for the reply
        ]
        if not pages:
            return []
        d = self.debate()
        try:
            text = await self._complete(
                self._chair_label(d),
                "studies",
                d["chair_endpoint_id"],
                d["chair_model"],
                prompts.studies_messages(question, pages),
                await self._thinking_flag(d["chair_endpoint_id"], d["chair_model"], False),
            )
            return check_studies(parse_json_loose(text).get("studies") or [], pages)
        except Exception as e:
            log.warning("key studies failed: %s", e)
            return []

    def _research_digest(self, topic: int, limit_words: int = 1200) -> str:
        """Everything Beagle found for this topic (briefs and lookups, newest last), for the chair's answer. The
        transcript window alone can miss the opening brief."""
        rows = [
            serialize_message(r)
            for r in db.query(
                "SELECT * FROM messages WHERE debate_id = ? AND topic = ? AND author_kind = 'researcher' "
                "AND status = 'done' AND research_kind IN ('brief', 'request') ORDER BY id",
                [self.id, topic],
            )
        ]
        parts, words = [], 0
        for r in reversed(rows):  # newest first while trimming, then back in order
            text = r["content"] + prompts.sources_block(r["sources"])
            n = len(text.split())
            if words + n > limit_words and parts:
                break
            parts.append(
                f"{'Opening brief' if r['research_kind'] == 'brief' else 'Lookup: ' + (r['research_request'] or '')[:120]}\n{text}"
            )
            words += n
        return "\n\n".join(reversed(parts))

    def _claims(self, topic: Optional[int] = None) -> List[Dict[str, Any]]:
        if topic is None:
            return db.query("SELECT * FROM claims WHERE debate_id = ? ORDER BY id", [self.id])
        return db.query("SELECT * FROM claims WHERE debate_id = ? AND topic = ? ORDER BY id", [self.id, topic])

    async def _audit_answer(
        self, msg_id: int, claims: List[Dict[str, Any]], studies: Optional[List[Dict[str, str]]] = None
    ) -> None:
        """After the answer: check it against the ledger and, if it breaks the evidence rules, have the chair revise it
        once. The answer records that it was checked, and what was fixed."""
        row = db.query_one("SELECT * FROM messages WHERE id = ?", [msg_id])
        if not claims or not row or row["status"] != "done" or not row["content"]:
            return
        d = self.debate()
        chair = self._chair_label(d)
        think = await self._thinking_flag(d["chair_endpoint_id"], d["chair_model"], False)
        try:
            text = await self._complete(
                chair,
                "audit",
                d["chair_endpoint_id"],
                d["chair_model"],
                prompts.answer_check_messages(
                    row["content"],
                    claims,
                    self._research_digest(row["topic"])
                    + (f"\n\nKey studies:\n{prompts.studies_text(studies)}" if studies else ""),
                    list(self._handles().values()),
                ),
                think,
            )
            raw = parse_json_loose(text).get("problems") or []
        except Exception as e:
            log.warning("answer audit failed: %s", e)
            return
        # Checked in code: every criterion the question lists is covered, and written-out arithmetic adds up
        question = self._question(row["topic"])
        checked = [
            {
                "text": "",
                "issue": f"The question asks about {r.lower()}, but the answer doesn't address it; add what the research "
                "shows about it (with numbers where there are any).",
            }
            for r in stated_requirements(question)
            if not requirement_covered(r, row["content"])
        ] + check_arithmetic(row["content"])
        problems = (
            checked
            + [
                {"text": str(p.get("text") or "").strip()[:300], "issue": str(p.get("issue") or "").strip()[:300]}
                for p in raw
                if isinstance(p, dict) and str(p.get("issue") or "").strip()
            ]
        )[:8]
        meta = {"evidence": {"checked": True, "problems": problems}}
        content = plain_answer(row["content"])
        if problems:
            try:
                revised = strip_thinking(
                    await self._complete(
                        chair,
                        "revise",
                        d["chair_endpoint_id"],
                        d["chair_model"],
                        prompts.answer_revise_messages(
                            row["content"], problems, claims, self._research_digest(row["topic"])
                        ),
                        think,
                    )
                ).strip()
                # Models sometimes echo the problem list after the answer
                revised = re.split(r"\n[ \t*_#]*(?:an |the )?audit (?:found|flagged|identified)\b", revised, flags=re.I)[0].strip()
                # A revision has to keep the answer whole: its bottom line and its sections
                sections = lambda text: set(re.findall(r"^##\s+(.+?)\s*$", text, re.M))  # noqa: E731
                if (
                    re.search(r"BOTTOM\s*LINE", revised, re.I)
                    and sections(row["content"]) <= sections(revised)
                    and len(revised) >= 0.6 * len(row["content"])
                ):
                    content = plain_answer(revised)
                    meta["evidence"].update(revised=True, original=row["content"])
            except Exception as e:
                log.warning("answer revision failed: %s", e)
        self._finish_message(msg_id, content=content, meta_json=json.dumps(meta))

    # ----------------------------------------------------------------- why?

    async def why(self, verdict_id: int, passage: str) -> Dict[str, Any]:
        """Trace a passage of the final answer to the agents who argued for or against it and the sources behind it."""
        passage = " ".join(passage.split())[:600]
        cached = db.query_one(
            "SELECT content FROM provenance WHERE verdict_id = ? AND passage = ?", [verdict_id, passage]
        )
        if cached:
            return json.loads(cached["content"])
        v = db.query_one("SELECT * FROM verdicts WHERE id = ? AND debate_id = ?", [verdict_id, self.id])
        if not v:
            raise KeyError(verdict_id)
        answer = db.query_one("SELECT content FROM messages WHERE id = ?", [v["message_id"]])
        d = self.debate()
        topic = v["topic"]
        handles = self._handles()
        rows = [
            serialize_message(r)
            for r in db.query(
                "SELECT * FROM messages WHERE debate_id = ? AND topic = ? AND author_kind = 'researcher' "
                "AND status = 'done' ORDER BY id",
                [self.id, topic],
            )
        ]
        sources: List[Dict[str, Any]] = []
        for r in rows:
            for s in r["sources"]:
                if s.get("url") and all(s["url"] != x["url"] for x in sources):
                    sources.append(s)
        sources = sources[:20]
        # The passage may come from any round or from a research brief: give the chair the most related messages
        candidates = [
            m
            for m in self._verbatim(topic, 0)
            if m["author_kind"] in ("seat", "researcher") and m["round"] > 0 and m["content"]
        ]
        transcript = prompts.render_transcript(_most_related(passage, candidates, limit=10), handles)
        empty: Dict[str, Any] = {"summary": "", "support": [], "challenges": [], "sources": []}
        result = empty
        for _attempt in range(2):
            try:
                text = await self._complete(
                    self._chair_label(d),
                    "why",
                    d["chair_endpoint_id"],
                    d["chair_model"],
                    prompts.why_messages(
                        question=self._question(topic),
                        answer=answer["content"] if answer else "",
                        passage=passage,
                        transcript=transcript,
                        positions=self._final_positions(topic),
                        sources=sources,
                        handles=list(handles.values()),
                    ),
                    await self._thinking_flag(d["chair_endpoint_id"], d["chair_model"], False),
                    topic=topic,
                )
                result = parse_json_loose(text)
                if result:
                    break
            except Exception as e:
                log.warning("why failed: %s", e)
        known = set(handles.values())

        def people(key: str) -> List[Dict[str, str]]:
            out = []
            for item in result.get(key) or []:
                if isinstance(item, dict) and str(item.get("agent", "")).strip() in known:
                    out.append({"agent": item["agent"].strip(), "point": str(item.get("point") or "").strip()[:200]})
            return out[:8]

        cited = []
        for n in result.get("sources") or []:
            try:
                s = sources[int(n) - 1]
            except (TypeError, ValueError, IndexError):
                continue
            cited.append({"title": s.get("title") or s["url"], "url": s["url"]})
        clean = {
            "summary": str(result.get("summary") or "").strip()[:300],
            "support": people("support"),
            "challenges": people("challenges"),
            "sources": cited[:6],
        }
        if clean != empty:
            db.execute(
                "INSERT OR REPLACE INTO provenance (verdict_id, passage, content) VALUES (?, ?, ?)",
                [verdict_id, passage, json.dumps(clean)],
            )
        return clean

    # -------------------------------------------------------------- conclude

    def _final_positions(self, topic: int) -> List[Dict[str, Any]]:
        out = []
        for seat in self.seats():
            row = db.query_one(
                "SELECT * FROM messages WHERE debate_id = ? AND topic = ? AND seat_id = ? AND author_kind = 'seat' "
                "AND status IN ('done', 'stopped') AND TRIM(content) != '' ORDER BY id DESC LIMIT 1",
                [self.id, topic, seat["id"]],
            )
            if row:
                st = parse_stance(row["content"])
                out.append(
                    {
                        "handle": seat["handle"],
                        "stance": st.stance if st.parsed else "—",
                        "position": st.position or "(no position line)",
                        "body": st.body,
                    }
                )
        return out

    async def _conclude(self, reason: str) -> None:
        self._concluding = True
        try:
            await self._refresh_mode()
            self._set(status="concluding")
            d = self.debate()
            topic = d["topic"]
            question = self._question(topic)
            positions = self._final_positions(topic)

            # 1. Any pending lookups, then each material claim the answer will rely on, checked against sources
            await self._drain_research(d["round"])
            claims: List[Dict[str, Any]] = []
            studies: List[Dict[str, str]] = []
            if d["research_enabled"] and positions:
                claims = await self._check_claims(positions, d["round"])
                studies = await self._key_studies(topic)

            # 2. Chair synthesizes the final answer, streamed like any other message
            summary = self._latest_summary(topic)
            upto = summary["upto_round"] if summary else 0
            msgs = [
                m
                for m in self._verbatim(topic, upto)
                if not (m["round"] == 0 and m["author_kind"] == "user") and m.get("research_kind") != "factcheck"
            ]
            handles = self._handles()
            vmessages = prompts.verdict_messages(
                question=question,
                prior_topics=self._prior_topics(topic),
                summary=summary["content"] if summary else None,
                transcript=prompts.render_transcript(msgs[-len(handles) * 2 :], handles),
                research=self._research_digest(topic),
                positions=positions,
                fact_check=None,
                reason=reason,
                criteria=d["criteria"],
                custom_rubric=d["custom_rubric"],
                guidance=(d["pack"] or {}).get("guidance", ""),
                claims=claims,
                studies=prompts.studies_text(studies),
            )
            row = self._insert_message(topic=topic, round_no=d["round"], author_kind="chair", status="streaming")
            try:
                # The debate already did the reasoning; chair thinking can exhaust the context window
                # before any answer is written, so it's off here.
                think = await self._thinking_flag(d["chair_endpoint_id"], d["chair_model"], False)
                chair_label = self._chair_label(d)
                stats = await self._stream_into(
                    row["id"], d["chair_endpoint_id"], d["chair_model"], vmessages, think, None, chair_label, "answer"
                )
                partial = self.partials[row["id"]]
                if not strip_thinking(partial["content"]).strip():
                    # Some models still spend the whole budget "thinking" in plain text; retry once
                    self._log(row["id"], "(empty answer, retrying)")
                    partial["content"] = ""
                    stats = await self._stream_into(
                        row["id"],
                        d["chair_endpoint_id"],
                        d["chair_model"],
                        vmessages,
                        think,
                        None,
                        chair_label,
                        "answer",
                    )
                content = strip_thinking(partial["content"]).strip()
                if not content:
                    raise RuntimeError("returned an empty answer twice")
                self._finish_message(
                    row["id"],
                    content=content,
                    thinking=partial["thinking"],
                    tokens=stats.get("tokens"),
                    tok_per_s=stats.get("tok_per_s"),
                    prompt_tokens=stats.get("prompt_tokens"),
                    duration_ms=stats.get("duration_ms"),
                    status="done",
                )
            except asyncio.CancelledError:
                partial = self.partials.get(row["id"], {"content": "", "thinking": ""})
                self._finish_message(
                    row["id"],
                    content=strip_thinking(partial["content"]),
                    thinking=partial["thinking"],
                    status="stopped",
                )
                raise
            except Exception as e:
                self._finish_message(row["id"], status="error", content=f"The chair ({d['chair_model']}) failed: {e}")
            finally:
                self.partials.pop(row["id"], None)
            await self._audit_answer(row["id"], claims, studies)
            done = db.query_one("SELECT content, status FROM messages WHERE id = ?", [row["id"]])
            if studies and done and done["status"] == "done" and done["content"]:
                self._finish_message(
                    row["id"], content=done["content"].rstrip() + "\n\n" + prompts.studies_markdown(studies)
                )

            vid = db.execute(
                "INSERT INTO verdicts (debate_id, topic, reason, rounds, message_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                [self.id, topic, reason, d["round"], row["id"], db.now()],
            )
            self.bus.publish(
                {"type": "verdict_created", "verdict": db.query_one("SELECT * FROM verdicts WHERE id = ?", [vid])}
            )
            self._set(status="concluded")
        except asyncio.CancelledError:
            db.update("debates", self.id, status="paused")
            self.bus.publish({"type": "debate_updated", "debate": self.debate()})
            raise
        finally:
            self._concluding = False

    # --------------------------------------------------------------- snapshot

    def _public_seats(self) -> List[Dict[str, Any]]:
        endpoint_names = {e.id: e.name for e in inventory.endpoints()}
        return [
            {
                **s,
                "thinking_enabled": bool(s["thinking_enabled"]),
                "endpoint_name": endpoint_names.get(s["endpoint_id"], "?"),
            }
            for s in self.seats()
        ]

    def snapshot(self) -> Dict[str, Any]:
        d = self.debate()
        endpoint_names = {e.id: e.name for e in inventory.endpoints()}
        seats = self._public_seats()
        rows = db.query("SELECT * FROM messages WHERE debate_id = ? ORDER BY id", [self.id])
        r_ep, r_model = self._researcher_model(d)
        return {
            "debate": {
                **d,
                "chair_endpoint_name": endpoint_names.get(d["chair_endpoint_id"], "?"),
                "researcher_model": r_model,
                "researcher_endpoint_name": endpoint_names.get(r_ep, "?"),
            },
            "seats": seats,
            "messages": [serialize_message(r, self.partials.get(r["id"])) for r in rows],
            "summaries": db.query("SELECT * FROM summaries WHERE debate_id = ? ORDER BY id", [self.id]),
            "drafts": db.query("SELECT * FROM drafts WHERE debate_id = ? ORDER BY id", [self.id]),
            "claims": self._claims(),
            "verdicts": db.query(
                "SELECT id, debate_id, topic, reason, rounds, message_id, created_at "
                "FROM verdicts WHERE debate_id = ? ORDER BY id",
                [self.id],
            ),
            "mode": self.mode,
            "metrics": {
                t["topic"]: self.metrics(t["topic"])
                for t in db.query("SELECT DISTINCT topic FROM usage WHERE debate_id = ?", [self.id])
            },
        }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_engines: Dict[str, DebateEngine] = {}


def get_engine(debate_id: str) -> DebateEngine:
    if debate_id not in _engines:
        _engines[debate_id] = DebateEngine(debate_id)
    return _engines[debate_id]


async def drop_engine(debate_id: str) -> None:
    eng = _engines.pop(debate_id, None)
    if eng:
        await eng.shutdown()


def recover_after_restart() -> None:
    """Debates interrupted by a server restart resume as paused (research-only lookups return to concluded)."""
    db.execute("UPDATE messages SET status = 'stopped' WHERE status = 'streaming'")
    db.execute("UPDATE debates SET status = 'concluded' WHERE status = 'researching'")
    db.execute("UPDATE debates SET status = 'paused' WHERE status IN ('running', 'voting', 'concluding', 'intake')")
