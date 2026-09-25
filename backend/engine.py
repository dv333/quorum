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
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from . import db, firecrawl, inventory, prompts
from .config import (
    MAX_ROUNDS_LIMIT,
    CONTEXT_BUDGET_FRACTION,
    INTAKE_MAX_QUESTIONS,
    MIN_ROUNDS_FOR_CONSENSUS,
    MIN_SEATS,
    RESEARCH_MAX_QUERIES,
    RESEARCH_MAX_SOURCES,
    RESEARCH_REQUESTS_PER_ROUND,
    RESEARCH_RESULTS_PER_QUERY,
    RESEARCHER_NAME,
)
from .parsing import (
    MENTION_RE,
    ThinkSplitter,
    clean_fact_check,
    parse_json_loose,
    parse_research_requests,
    parse_stance,
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


def _parse_queries(text: str, limit: int) -> List[str]:
    queries = []
    for line in strip_thinking(text).splitlines():
        q = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip().strip("\"'`").strip()
        if q and q.upper() != "NONE" and len(q) > 2 and q not in queries:
            queries.append(q[:200])
    return queries[:limit]


def _interleave(result_lists: List[List[Dict[str, str]]], limit: int) -> List[Dict[str, str]]:
    """Take results round-robin across queries so every query contributes, deduplicated by URL."""
    seen, out = set(), []
    for i in range(max((len(r) for r in result_lists), default=0)):
        for results in result_lists:
            if i < len(results) and results[i]["url"] not in seen:
                seen.add(results[i]["url"])
                out.append(results[i])
    return out[:limit]


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

    def _system_message(self, text: str) -> None:
        d = self.debate()
        self._insert_message(topic=d["topic"], round_no=d["round"], author_kind="system", content=text)

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
            self._set(max_rounds=max(1, min(MAX_ROUNDS_LIMIT, rounds)))
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
            ep, model, messages, think=think, num_ctx=self.debate()["num_ctx"], keep_alive=keep_alive
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
            self._endpoint(endpoint_id), model, messages, think=think, num_ctx=self.debate()["num_ctx"]
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

    async def _pick_roles(self) -> None:
        """The largest council model reads the question and picks the chair, the researcher model and a title."""
        d = self.debate()
        seats = self.seats()
        metas = {}
        for s in seats:
            try:
                metas[s["id"]] = await self.meta_lookup(s["endpoint_id"], s["model"], d["num_ctx"]) or {}
            except Exception:
                metas[s["id"]] = {}
        picker = max(seats, key=lambda s: metas[s["id"]].get("est_bytes") or 0)
        members = []
        for s in seats:
            m = metas[s["id"]]
            bits = [b for b in (m.get("family"), m.get("params")) if b]
            if m.get("thinking"):
                bits.append("reasoning model")
            members.append(
                {"handle": s["handle"], "description": f"{s['model']}" + (f" ({', '.join(bits)})" if bits else "")}
            )
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
            topic=d["topic"], round_no=round_no, author_kind="seat", seat_id=seat["id"], status="streaming"
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

    async def _research(
        self, item: Dict[str, Any], round_no: int, positions: Optional[List[Dict[str, Any]]] = None
    ) -> Optional[str]:
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
            if kind == "factcheck":
                plan = prompts.factcheck_plan_messages(question, positions or [], RESEARCH_MAX_QUERIES)
            else:
                plan = prompts.research_plan_messages(item["request"], question, RESEARCH_MAX_QUERIES)
            self._log(msg_id, "Planning searches…")
            queries = _parse_queries(
                await self._complete(RESEARCHER_NAME, "research", ep_id, model, plan, think), RESEARCH_MAX_QUERIES
            )
            if not queries and kind != "factcheck":
                queries = [item["request"][:200]]
            if not queries:
                self._finish_message(
                    msg_id,
                    content="Nothing in the final positions needed a web check.",
                    thinking=self.partials[msg_id]["thinking"],
                    status="done",
                )
                return None

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
            sources = _interleave(ok, RESEARCH_MAX_SOURCES)
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
            if kind == "factcheck":
                request = "Verify these claims: " + "; ".join(queries)
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
            content = strip_thinking(partial["content"]).strip()
            if kind == "factcheck":
                content = clean_fact_check(content)
            content = content or "The sources didn't answer this."
            stored_sources = [{"url": s["url"], "title": s["title"]} for s in sources]
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
            )

        messages = build(msgs)
        # Hard cap: if still over budget (summary pending or failed), drop the oldest messages
        keep_min = len(handles)
        while len(msgs) > keep_min and sum(estimate_tokens(m["content"]) for m in messages) > self._budget():
            msgs = msgs[1:]
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

            # 1. Any pending lookups, then a web fact-check of the claims the answer will rely on
            await self._drain_research(d["round"])
            fact_check = None
            if d["research_enabled"] and positions:
                fact_check = await self._research(
                    {"request": "Fact-check the final positions", "requested_by": None, "kind": "factcheck"},
                    d["round"],
                    positions=positions,
                )

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
                positions=positions,
                fact_check=fact_check,
                reason=reason,
                criteria=d["criteria"],
                custom_rubric=d["custom_rubric"],
                guidance=(d["pack"] or {}).get("guidance", ""),
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

    def snapshot(self) -> Dict[str, Any]:
        d = self.debate()
        endpoint_names = {e.id: e.name for e in inventory.endpoints()}
        seats = [
            {
                **s,
                "thinking_enabled": bool(s["thinking_enabled"]),
                "endpoint_name": endpoint_names.get(s["endpoint_id"], "?"),
            }
            for s in self.seats()
        ]
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
