import asyncio
import json
import re

import pytest

from backend import db
from backend import engine as engine_mod
from backend import inventory, pyrun
from backend.config import HANDLES, SEAT_COLORS
from backend.engine import DebateEngine
from backend.model_queue import Caller, ModelQueue, server_key
from backend.providers import ChatClient, Chunk, ProviderError


AGENT_RE = re.compile(r"You are (Otter|Panda|Koala|Penguin|Hedgehog),")


UNSOURCED = (
    "BOTTOM LINE: Send **reminder texts**.\n\n## Key points\n"
    "- **Reminders work:** Reminders usually help; a rough estimate is a cut of about a fifth.\n"
    "- **Cheap:** texts cost little."
)


class FakeClient(ChatClient):
    """Scripted model server. turn_fn(handle, round, messages) -> (content, thinking) or raises."""

    def __init__(self, turn_fn):
        super().__init__()
        self.turn_fn = turn_fn
        self.calls = []
        self.gates = {}
        self.plan_reply = lambda prompt: "planned query"
        self.intake_replies = []
        self.pick_reply = (
            '{"chair": "Panda", "researcher": "Koala", "reason": "clear, careful writer", "title": "Test title"}'
        )
        # The claim ledger: which claims to check, how each checks out, and the audit of the final answer
        self.claims_reply = '{"claims": [{"claim": "Go compiles fast", "query": "go compile speed"}]}'
        self.verify_reply = lambda messages: (
            '{"status": "supported", "source": 1, "quote": "content for go compile speed", "caveat": ""}'
        )
        self.audit_reply = '{"problems": []}'
        self.revise_reply = "BOTTOM LINE: revised answer"
        self.verdict_reply = "VERDICT TEXT"
        self.review_gap_reply = '{"missing": []}'
        self.draft_reply = None  # None: the verdict reply
        self.review_add_reply = "BOTTOM LINE: add back"
        self.unsourced_reply = None  # None: the answer with its named sources taken out
        self.critique_reply = '{"problems": []}'
        self.critique_revise_reply = None  # None: the verdict reply, corrected
        self.recheck_reply = '{"problems": []}'

    def calls_with(self, marker):
        return [m for _, m, _ in self.calls if marker in m[0]["content"]]

    def turn_calls(self, handle=None):
        out = []
        for model, messages, kw in self.calls:
            m = AGENT_RE.search(messages[0]["content"])
            if m and (handle is None or m.group(1) == handle):
                out.append((messages, kw))
        return out

    async def stream(self, endpoint, model, messages, **kw):
        self.calls.append((model, messages, kw))
        sys = messages[0]["content"]
        agent = AGENT_RE.search(sys)
        if agent:
            handle = agent.group(1)
            round_no = int(re.search(r"\(round (\d+)", messages[1]["content"]).group(1))
            gate = self.gates.get(handle)
            if gate is not None:
                await gate.wait()
            content, thinking = self.turn_fn(handle, round_no, messages)
        elif sys.startswith("You list the factual claims"):
            content, thinking = self.claims_reply, ""
        elif sys.startswith("You are Beagle, a careful fact-checker"):
            content, thinking = self.verify_reply(messages), ""
        elif sys.startswith("You audit an AI council"):
            content, thinking = self.audit_reply, ""
        elif "keep a short draft" in sys and self.draft_reply is not None:
            content, thinking = self.draft_reply, ""
        elif sys.startswith("You check an AI council's code review"):
            content, thinking = self.review_gap_reply, ""
        elif (
            sys.startswith("You check an AI council's final answer")
            and "An answer was corrected" in messages[1]["content"]
        ):
            content, thinking = self.recheck_reply, ""
        elif sys.startswith("You check an AI council's final answer"):
            content, thinking = self.critique_reply, ""
        elif "You correct your final answer before the user sees it" in sys:
            content, thinking = self.critique_revise_reply or self.verdict_reply.replace("rebuild", "pay off"), ""
        elif "doesn't cite sources nobody checked" in sys:
            content, thinking = self.unsourced_reply or UNSOURCED, ""
        elif sys.startswith("You finish an AI council's code review"):
            content, thinking = self.review_add_reply, ""
        elif "You correct your final answer" in sys:
            content, thinking = self.revise_reply, ""
        elif sys.startswith("You organize an AI council"):
            content, thinking = self.pick_reply, ""
        elif "the moderator of an AI council" in sys:
            content, thinking = (self.intake_replies.pop(0) if self.intake_replies else '{"action": "clear"}'), ""
        elif sys.startswith("You rewrite an AI council"):
            content, thinking = "BOTTOM LINE: simpler words", ""
        elif sys.startswith("You plan web searches"):
            content, thinking = self.plan_reply(messages[1]["content"]), ""
        elif sys.startswith("You are Beagle"):
            content, thinking = "BRIEF: fact [1]", ""
        elif "neutral chair" in sys:
            content, thinking = "SUMMARY TEXT", ""
        else:
            content, thinking = self.verdict_reply, ""
        if thinking:
            yield Chunk("thinking", thinking)
        for i in range(0, len(content), 7):
            yield Chunk("content", content[i : i + 7])
            await asyncio.sleep(0)
        yield Chunk("done", stats={"tokens": 10, "tok_per_s": 5.0})


def reply(stance, text="My view.", position="Use Go."):
    return f"{text}\n\nSTANCE: {stance}\nPOSITION: {position}", ""


async def fake_meta(endpoint_id, model, num_ctx):
    return {"thinking": True}


async def fake_plan(selection, num_ctx):
    return {"mode": "parallel"}


@pytest.fixture(autouse=True)
def memory_db(monkeypatch):
    db.connect(":memory:")
    monkeypatch.setattr(engine_mod, "QUEUE", ModelQueue())
    yield


class FakeSearch:
    def __init__(self, fail=None):
        self.queries = []
        self.fail = fail

    async def __call__(self, query, limit, focus=""):
        self.queries.append(query)
        if self.fail:
            raise self.fail
        return [
            {
                "url": f"https://example.com/{len(self.queries)}-{i}",
                "title": f"Page {i}",
                "description": "",
                "content": f"content for {query}",
            }
            for i in range(limit)
        ]


def make_debate(
    client,
    *,
    seats=3,
    max_rounds=5,
    autopilot=True,
    num_ctx=8192,
    models=None,
    research=False,
    search=None,
    auto_chair=False,
):
    debate_id = "d1"
    db.execute(
        "INSERT INTO debates (id, title, created_at, chair_endpoint_id, chair_model, max_rounds, autopilot, "
        "criteria_json, custom_rubric, num_ctx, research_enabled, researcher_endpoint_id, researcher_model, chair_mode) "
        "VALUES (?, ?, ?, 1, 'chair-model', ?, ?, ?, '', ?, ?, ?, ?, ?)",
        [
            debate_id,
            "" if auto_chair else "t",
            db.now(),
            max_rounds,
            int(autopilot),
            json.dumps(["Accuracy"]),
            num_ctx,
            int(research),
            None if auto_chair else 1,
            None if auto_chair else "research-model",
            "auto" if auto_chair else "manual",
        ],
    )
    for i in range(seats):
        model = (models or [f"model-{i}" for i in range(seats)])[i]
        db.execute(
            "INSERT INTO seats (debate_id, handle, endpoint_id, model, color, thinking_enabled, position) "
            "VALUES (?, ?, 1, ?, ?, 1, ?)",
            [debate_id, HANDLES[i], model, SEAT_COLORS[i], i],
        )
    return DebateEngine(
        debate_id, client=client, meta_lookup=fake_meta, plan_lookup=fake_plan, search_fn=search or FakeSearch()
    )


async def wait_for(pred, timeout=2.0):
    for _ in range(int(timeout / 0.01)):
        if pred():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met")


async def test_consensus_ends_after_min_rounds_with_verdict():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client)
    await eng.post_user_message("Rust or Go?")
    await eng.task
    d = eng.debate()
    assert d["status"] == "concluded" and d["round"] == 2  # consensus can't end round 1
    verdict = db.query_one("SELECT * FROM verdicts")
    assert verdict["reason"] == "consensus"
    chair = db.query_one("SELECT * FROM messages WHERE author_kind = 'chair'")
    assert chair["content"] == "VERDICT TEXT" and chair["status"] == "done"


async def test_max_rounds_without_consensus():
    client = FakeClient(lambda h, r, m: reply("DISAGREE" if h == "Otter" else "AGREE"))
    eng = make_debate(client, max_rounds=3)
    await eng.post_user_message("Q")
    await eng.task
    assert eng.debate()["round"] == 3
    assert db.query_one("SELECT reason FROM verdicts")["reason"] == "max_rounds"
    assert len(client.turn_calls()) == 9


async def test_manual_mode_pauses_each_round_then_continues():
    client = FakeClient(lambda h, r, m: reply("REFINE"))
    eng = make_debate(client, autopilot=False, max_rounds=2)
    await eng.post_user_message("Q")
    await eng.task
    assert eng.debate()["status"] == "paused" and eng.debate()["round"] == 1
    await eng.continue_()
    await eng.task
    assert eng.debate()["status"] == "concluded" and eng.debate()["round"] == 2


async def test_user_message_while_paused_resumes_and_is_seen():
    client = FakeClient(lambda h, r, m: reply("REFINE"))
    eng = make_debate(client, autopilot=False, max_rounds=3)
    await eng.post_user_message("Q")
    await eng.task
    await eng.post_user_message("Assume the team only knows Python")
    await eng.task
    assert eng.debate()["round"] == 2
    prompt = client.turn_calls("Otter")[-1][0][1]["content"]
    assert "User: Assume the team only knows Python" in prompt


async def test_interjection_mid_round_reaches_next_speaker():
    client = FakeClient(lambda h, r, m: reply("REFINE"))
    eng = make_debate(client, autopilot=False)
    client.gates["Otter"] = asyncio.Event()
    await eng.post_user_message("Q")
    await wait_for(lambda: len(client.turn_calls("Otter")) == 1)
    await eng.post_user_message("PREFER RUST")
    client.gates["Otter"].set()
    await eng.task
    a_prompt = client.turn_calls("Otter")[0][0][1]["content"]
    b_prompt = client.turn_calls("Panda")[0][0][1]["content"]
    assert "PREFER RUST" not in a_prompt
    assert "User: PREFER RUST" in b_prompt


async def test_thinking_is_never_forwarded():
    def turn(h, r, m):
        if h == "Otter":
            return "<think>INLINE_SECRET</think>Visible answer\nSTANCE: REFINE\nPOSITION: x", "NATIVE_SECRET"
        return reply("REFINE")

    client = FakeClient(turn)
    eng = make_debate(client, max_rounds=2)
    await eng.post_user_message("Q")
    await eng.task
    a_msg = db.query_one(
        "SELECT * FROM messages WHERE seat_id IS NOT NULL AND author_kind = 'seat' ORDER BY id LIMIT 1"
    )
    assert "NATIVE_SECRET" in a_msg["thinking"] and "INLINE_SECRET" in a_msg["thinking"]
    assert "SECRET" not in a_msg["content"]
    for _, messages, _ in client.calls[1:]:
        assert "SECRET" not in json.dumps(messages)
    # thinking requested for seats (meta says the model supports it)
    assert client.turn_calls()[0][1]["think"] is True


async def test_failed_seat_is_skipped_and_debate_continues():
    def turn(h, r, m):
        if h == "Koala":
            raise RuntimeError("model not found")
        return reply("AGREE")

    client = FakeClient(turn)
    eng = make_debate(client)
    await eng.post_user_message("Q")
    await eng.task
    errors = db.query("SELECT * FROM messages WHERE status = 'error'")
    assert errors and "Koala (model-2) failed: model not found" in errors[0]["content"]
    assert eng.debate()["status"] == "concluded"
    verdict_prompt = client.calls_with("You turn the council")[-1][1]["content"]
    assert "Otter:" in verdict_prompt and "- Koala:" not in verdict_prompt  # C has no final position


async def test_too_few_responders_fails():
    client = FakeClient(lambda h, r, m: (_ for _ in ()).throw(RuntimeError("down")) if h != "Otter" else reply("AGREE"))
    eng = make_debate(client)
    await eng.post_user_message("Q")
    await eng.task
    assert eng.debate()["status"] == "failed"
    assert (
        "Fewer than two agents"
        in db.query("SELECT content FROM messages WHERE author_kind = 'system' ORDER BY id DESC")[0]["content"]
    )


async def test_stop_mid_stream_then_resume_same_round():
    client = FakeClient(lambda h, r, m: reply("REFINE"))
    eng = make_debate(client, autopilot=False)
    client.gates["Panda"] = asyncio.Event()
    await eng.post_user_message("Q")
    await wait_for(lambda: len(client.turn_calls("Panda")) == 1)
    await eng.stop()
    assert eng.debate()["status"] == "paused"
    b = db.query_one("SELECT * FROM messages WHERE seat_id = (SELECT id FROM seats WHERE handle = 'Panda')")
    assert b["status"] == "stopped"
    client.gates["Panda"].set()
    await eng.continue_()
    await eng.task
    # resumed round 1 with only Koala, then paused
    assert eng.debate()["round"] == 1
    assert len(client.turn_calls("Otter")) == 1 and len(client.turn_calls("Koala")) == 1


async def test_rolling_summary_kicks_in_for_small_context():
    long_text = "word " * 700  # ~3500 chars per message
    client = FakeClient(lambda h, r, m: reply("REFINE", text=long_text))
    eng = make_debate(client, num_ctx=4096, max_rounds=3)
    await eng.post_user_message("Q")
    await eng.task
    summary = db.query_one("SELECT * FROM summaries ORDER BY id LIMIT 1")
    assert summary and summary["upto_round"] == 1
    round3_prompt = client.turn_calls("Otter")[2][0][1]["content"]
    assert "SUMMARY OF ROUNDS 1-1" in round3_prompt and "SUMMARY TEXT" in round3_prompt
    assert "--- Round 1 ---" not in round3_prompt


async def test_conclude_manually_while_paused():
    client = FakeClient(lambda h, r, m: reply("DISAGREE"))
    eng = make_debate(client, autopilot=False)
    await eng.post_user_message("Q")
    await eng.task
    await eng.conclude()
    await eng.task
    assert eng.debate()["status"] == "concluded"
    assert db.query_one("SELECT reason FROM verdicts")["reason"] == "manual"


async def test_follow_up_starts_new_topic_with_history():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client)
    await eng.post_user_message("First question")
    await eng.task
    await eng.post_user_message("Follow-up question")
    await eng.task
    d = eng.debate()
    assert d["topic"] == 2 and d["status"] == "concluded"
    prompt = client.turn_calls("Otter")[-1][0][1]["content"]
    assert "Q: First question" in prompt and "Council verdict: VERDICT TEXT" in prompt
    assert "THE QUESTION:\nFollow-up question" in prompt


async def test_sequential_mode_unloads_between_different_models():
    async def seq_plan(selection, num_ctx):
        return {"mode": "sequential"}

    client = FakeClient(lambda h, r, m: reply("REFINE"))
    eng = make_debate(client, autopilot=False, models=["m1", "m1", "m2"])
    eng.plan_lookup = seq_plan
    await eng.post_user_message("Q")
    await eng.task
    keep = [kw["keep_alive"] for _, kw in client.turn_calls()]
    assert keep == ["5m", 0, 0]  # A->B same model keeps it loaded; B->C and C->A unload


async def test_snapshot_includes_partial_stream():
    client = FakeClient(lambda h, r, m: reply("REFINE"))
    eng = make_debate(client, autopilot=False)
    client.gates["Otter"] = asyncio.Event()
    await eng.post_user_message("Q")
    await wait_for(lambda: len(client.turn_calls("Otter")) == 1)
    snap = eng.snapshot()
    streaming = [m for m in snap["messages"] if m["status"] == "streaming"]
    assert len(streaming) == 1 and streaming[0]["seat_id"] is not None
    client.gates["Otter"].set()
    await eng.task


async def test_conclude_mid_turn_keeps_earlier_position_of_interrupted_seat():
    client = FakeClient(lambda h, r, m: reply("REFINE", position=f"{h} position r{r}"))
    eng = make_debate(client, autopilot=False)
    await eng.post_user_message("Q")
    await eng.task
    client.gates["Panda"] = asyncio.Event()  # B hangs in round 2
    await eng.continue_()
    await wait_for(lambda: len(client.turn_calls("Panda")) == 2)
    await eng.conclude()
    await eng.task
    verdict_prompt = client.calls_with("You turn the council")[-1][1]["content"]
    assert "Panda position r1" in verdict_prompt and "Otter position r2" in verdict_prompt


# ---------------------------------------------------------------- research


def researcher_messages():
    return db.query("SELECT * FROM messages WHERE author_kind = 'researcher' ORDER BY id")


async def test_opening_brief_runs_before_round_one_and_reaches_agents():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    search = FakeSearch()
    eng = make_debate(client, research=True, search=search)
    await eng.post_user_message("Latest Postgres?")
    await eng.task
    briefs = researcher_messages()
    assert briefs[0]["research_kind"] == "brief" and briefs[0]["round"] == 0 and briefs[0]["status"] == "done"
    first_seat = db.query_one("SELECT id FROM messages WHERE author_kind = 'seat' ORDER BY id LIMIT 1")["id"]
    assert briefs[0]["id"] < first_seat
    assert json.loads(briefs[0]["sources_json"])[0]["url"].startswith("https://example.com/")
    prompt = client.turn_calls("Otter")[0][0][1]["content"]
    assert "Beagle (web research): BRIEF: fact [1]" in prompt and "Sources: [1] Page 0" in prompt
    assert "@Beagle" in client.turn_calls("Otter")[0][0][0]["content"]  # agents are told how to ask
    assert search.queries[0] == "planned query"
    # research model, not the chair, does the research
    assert [model for model, m, _ in client.calls if m[0]["content"].startswith("You are Beagle")][
        0
    ] == "research-model"


async def test_agent_request_is_answered_before_next_speaker():
    def turn(h, r, m):
        if h == "Otter" and r == 2:
            return reply("REFINE", text="Not sure.\n@Beagle: what is the newest Go release?")
        return reply("REFINE")

    client = FakeClient(turn)
    eng = make_debate(client, research=True, max_rounds=2)
    await eng.post_user_message("Q")
    await eng.task
    kinds = [(m["research_kind"], m["requested_by"], m["research_request"]) for m in researcher_messages()]
    assert ("request", "Otter", "what is the newest Go release?") in kinds
    b_prompt = client.turn_calls("Panda")[1][0][1]["content"]  # Panda's round-2 turn, right after Otter's
    assert "Beagle (web research, asked by Otter): BRIEF" in b_prompt
    a_prompt = client.turn_calls("Otter")[1][0][1]["content"]
    assert "asked by Otter" not in a_prompt


async def test_round_one_is_blind_then_everyone_sees_everything():
    def turn(h, r, m):
        if h == "Otter" and r == 1:
            return reply("REFINE", text="OTTER-ROUND-ONE\n@Beagle: newest Go release?")
        return reply("REFINE")

    client = FakeClient(turn)
    eng = make_debate(client, research=True, max_rounds=2)
    await eng.post_user_message("Q")
    await eng.task
    panda_r1 = client.turn_calls("Panda")[0][0][1]["content"]
    assert "OTTER-ROUND-ONE" not in panda_r1 and "asked by Otter" not in panda_r1
    assert "This is round 1" in panda_r1 and "strongest objection" in panda_r1
    assert "Beagle (web research): BRIEF" in panda_r1  # the opening brief is shared
    panda_r2 = client.turn_calls("Panda")[1][0][1]["content"]
    assert "OTTER-ROUND-ONE" in panda_r2


async def test_agent_requests_capped_per_round(monkeypatch):
    monkeypatch.setattr("backend.engine.RESEARCH_REQUESTS_PER_ROUND", 1)
    client = FakeClient(lambda h, r, m: reply("REFINE", text=f"@Researcher: question from {h} here"))
    eng = make_debate(client, research=True, autopilot=False)
    await eng.post_user_message("Q")
    await eng.task
    requests = [m for m in researcher_messages() if m["research_kind"] == "request"]
    assert len(requests) == 1
    skipped = db.query("SELECT content FROM messages WHERE author_kind = 'system' AND content LIKE 'Research limit%'")
    assert len(skipped) == 2


async def test_no_research_when_disabled():
    client = FakeClient(lambda h, r, m: reply("AGREE", text="@Researcher: should be ignored please"))
    search = FakeSearch()
    eng = make_debate(client, research=False, search=search)
    await eng.post_user_message("Q")
    await eng.task
    assert researcher_messages() == [] and search.queries == []
    assert "@Beagle" not in client.turn_calls("Otter")[0][0][0]["content"]


async def test_user_lookup_after_verdict_does_not_start_new_topic():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, research=False)
    await eng.post_user_message("Q")
    await eng.task
    turns_before = len(client.turn_calls())
    await eng.post_user_message("@Researcher what changed in Go 1.30?")
    assert eng.debate()["status"] == "researching"
    await eng.task
    d = eng.debate()
    assert d["status"] == "concluded" and d["topic"] == 1
    assert len(client.turn_calls()) == turns_before
    req = researcher_messages()[-1]
    assert req["requested_by"] == "You" and req["research_request"] == "what changed in Go 1.30?"


async def test_user_request_mid_debate_is_queued():
    client = FakeClient(lambda h, r, m: reply("REFINE"))
    eng = make_debate(client, research=True, autopilot=False)
    client.gates["Otter"] = asyncio.Event()
    await eng.post_user_message("Q")
    await wait_for(lambda: len(client.turn_calls("Otter")) == 1)
    await eng.post_user_message("@Researcher: current SQLite version please")
    client.gates["Otter"].set()
    await eng.task
    req = [m for m in researcher_messages() if m["requested_by"] == "You"][0]
    b_first = db.query_one("SELECT id FROM messages WHERE seat_id = (SELECT id FROM seats WHERE handle = 'Panda')")[
        "id"
    ]
    assert req["id"] < b_first


def verdict_prompt(client):
    return next(m for _, m, _ in reversed(client.calls) if "You turn the council's debate" in m[0]["content"])[1][
        "content"
    ]


async def test_checked_claims_feed_the_verdict_as_rules():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, research=True)
    await eng.post_user_message("Q")
    await eng.task
    fc = [m for m in researcher_messages() if m["research_kind"] == "factcheck"]
    assert len(fc) == 1 and fc[0]["status"] == "done"
    assert fc[0]["content"].startswith("1. **Supported**: Go compiles fast") and "[1]" in fc[0]["content"]
    chair_msg = db.query_one("SELECT id FROM messages WHERE author_kind = 'chair'")["id"]
    assert fc[0]["id"] < chair_msg
    prompt = verdict_prompt(client)
    assert "Evidence ledger" in prompt and "[SUPPORTED] Go compiles fast" in prompt
    assert "Never state a CONTRADICTED claim" in prompt


async def test_claim_check_skipped_when_nothing_to_check():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    client.claims_reply = '{"claims": []}'
    eng = make_debate(client, research=True)
    await eng.post_user_message("Q")
    await eng.task
    fc = [m for m in researcher_messages() if m["research_kind"] == "factcheck"][0]
    assert "needed checking" in fc["content"]
    assert "Evidence ledger" not in verdict_prompt(client)


async def test_search_outage_is_reported_once_per_run_and_debate_continues():
    from backend.firecrawl import SearchError

    client = FakeClient(lambda h, r, m: reply("REFINE", text=f"@Researcher: question from {h} here"))
    search = FakeSearch(fail=SearchError("Can't reach Firecrawl at http://localhost:3002"))
    eng = make_debate(client, research=True, autopilot=False, search=search)
    await eng.post_user_message("Q")
    await eng.task
    errors = [m for m in researcher_messages() if m["status"] == "error"]
    assert len(errors) == 1 and "Web search failed" in errors[0]["content"]
    assert len(search.queries) == 1  # later requests skip the dead service
    notes = db.query("SELECT content FROM messages WHERE content LIKE 'Web search is unavailable%'")
    assert len(notes) == 1 and "Settings" in notes[0]["content"]
    assert eng.debate()["status"] == "paused" and len(client.turn_calls()) == 3


async def test_the_next_conundrum_remembers_that_search_is_down():
    from backend.firecrawl import SearchError

    client = FakeClient(lambda h, r, m: reply("REFINE", text=f"@Researcher: question from {h} here"))
    search = FakeSearch(fail=SearchError("Firecrawl error (HTTP 402): Insufficient credits"))
    eng = make_debate(client, research=True, autopilot=False, search=search)
    await eng.post_user_message("Q")
    await eng.task
    assert len(search.queries) == 1
    db.execute("DELETE FROM seats")
    db.execute("DELETE FROM debates")
    later = make_debate(client, research=True, autopilot=False, search=search)
    await later.post_user_message("Another question")
    await later.task
    assert len(search.queries) == 1  # not tried again: out of credits until the settings change


class EmptyChair(FakeClient):
    """Every call that writes the final answer comes back empty, whichever model makes it."""

    async def stream(self, endpoint, model, messages, **kw):
        if "chair of an AI council" in messages[0]["content"]:
            self.calls.append((model, messages, kw))
            yield Chunk("thinking", "drafting...")
            yield Chunk("done", stats={})
            return
        async for c in super().stream(endpoint, model, messages, **kw):
            yield c


async def test_an_answer_nobody_can_write_is_reported_as_no_answer():
    client = EmptyChair(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, max_rounds=1)  # one round: no draft to fall back on
    await eng.post_user_message("Q")
    await eng.task
    chair = db.query_one("SELECT * FROM messages WHERE author_kind = 'chair'")
    assert chair["status"] == "error" and "empty answer" in chair["content"]
    writers = [model for model, m, _ in client.calls if "chair of an AI council" in m[0]["content"]]
    assert writers == ["chair-model", "chair-model", "model-0"]  # the chair twice, then the largest other model
    assert all(kw["think"] is False for _, m, kw in client.calls if "chair of an AI council" in m[0]["content"])
    # No answer is reported as a failure, not as an answered debate
    assert eng.debate()["status"] == "failed"
    assert not db.query("SELECT * FROM verdicts")
    note = db.query("SELECT content FROM messages WHERE author_kind = 'system' ORDER BY id DESC")[0]["content"]
    assert note.startswith("No answer:") and "empty answer" in note and "Resume" in note


async def test_when_no_model_can_write_the_answer_the_latest_draft_stands_in():
    client = EmptyChair(lambda h, r, m: reply("REFINE"))
    client.draft_reply = "BOTTOM LINE: Use Go.\n- It compiles fast.\nCHANGED: First draft."
    eng = make_debate(client, max_rounds=2)
    await eng.post_user_message("Q")
    await eng.task
    assert eng.debate()["status"] == "concluded"
    chair = db.query_one("SELECT * FROM messages WHERE author_kind = 'chair'")
    assert chair["status"] == "done" and chair["content"].startswith("BOTTOM LINE: Use Go.")
    assert "CHANGED" not in chair["content"] and "the chair's draft after round 1" in chair["content"]


async def test_a_chair_whose_server_fails_hands_the_answer_to_another_model():
    class FailingChair(FakeClient):
        async def stream(self, endpoint, model, messages, **kw):
            if model == "chair-model" and "chair of an AI council" in messages[0]["content"]:
                self.calls.append((model, messages, kw))
                raise ProviderError("the model server stopped mid-reply")
                yield  # pragma: no cover
            async for c in super().stream(endpoint, model, messages, **kw):
                yield c

    client = FailingChair(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message("Q")
    await eng.task
    chair = db.query_one("SELECT * FROM messages WHERE author_kind = 'chair'")
    assert chair["status"] == "done" and chair["content"] == "VERDICT TEXT"
    assert "writes it instead" in chair["thinking"] and "stopped mid-reply" in chair["thinking"]
    assert eng.debate()["status"] == "concluded"


async def test_resume_after_a_failed_answer_tries_the_answer_again_not_another_round():
    class FlakyChair(FakeClient):
        failed = 0

        async def stream(self, endpoint, model, messages, **kw):
            if "chair of an AI council" in messages[0]["content"] and self.failed < 3:
                self.failed += 1
                self.calls.append((model, messages, kw))
                yield Chunk("done", stats={})
                return
            async for c in super().stream(endpoint, model, messages, **kw):
                yield c

    client = FlakyChair(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message("Q")
    await eng.task
    assert eng.debate()["status"] == "failed"
    turns = len(client.turn_calls())
    await eng.continue_()
    await eng.task
    assert eng.debate()["status"] == "concluded" and len(db.query("SELECT * FROM verdicts")) == 1
    assert len(client.turn_calls()) == turns  # no extra round


async def test_a_restart_mid_turn_marks_the_debate_and_resume_asks_that_agent_again():
    client = FakeClient(lambda h, r, m: reply("REFINE"))
    client.gates["Koala"] = asyncio.Event()  # Koala is mid-turn, with nothing written yet, when Quorum restarts
    eng = make_debate(client, seats=3, max_rounds=1)
    await eng.post_user_message("Q")
    await wait_for(lambda: len(client.turn_calls("Koala")) == 1)
    eng.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await eng.task
    db.execute("UPDATE debates SET status = 'running'")  # the process died: the status was never updated
    del client.gates["Koala"]
    engine_mod._engines.clear()
    engine_mod.recover_after_restart()
    d = db.query_one("SELECT * FROM debates")
    assert d["status"] == "paused" and d["interrupted"] == 1
    assert (
        "Quorum restarted while this was running" in db.query("SELECT content FROM messages ORDER BY id")[-1]["content"]
    )
    eng = engine_mod.get_engine("d1")
    eng.client, eng.meta_lookup, eng.plan_lookup = client, fake_meta, fake_plan
    await eng.continue_()
    await eng.task
    assert eng.debate()["status"] == "concluded" and eng.debate()["interrupted"] == 0
    assert len(client.turn_calls("Koala")) == 2  # cut off before it said anything, so it speaks again
    assert len(client.turn_calls("Otter")) == 1
    engine_mod._engines.clear()


async def test_streamed_text_is_saved_while_it_arrives(monkeypatch):
    monkeypatch.setattr(engine_mod, "CHECKPOINT_SECONDS", 0)
    client = FakeClient(lambda h, r, m: reply("REFINE", text="A long reply " * 5))
    eng = make_debate(client, seats=2, max_rounds=1)
    saved = []
    real_update = db.update

    def spy(table, row_id, **fields):
        if table == "messages" and set(fields) == {"content", "thinking"}:
            saved.append(fields["content"])
        return real_update(table, row_id, **fields)

    monkeypatch.setattr(engine_mod.db, "update", spy)
    await eng.post_user_message("Q")
    await eng.task
    assert len(saved) > 3 and saved[1].startswith("A long") and len(saved[-1]) > len(saved[1])


async def test_an_answer_that_did_not_finish_is_written_again_after_a_restart():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, max_rounds=2)
    await eng.post_user_message("Q")
    await eng.task
    # As if Quorum restarted while the chair was writing
    db.execute("DELETE FROM verdicts")
    db.execute("UPDATE debates SET status = 'concluding', answer_pending = 'consensus'")
    engine_mod._engines.clear()
    engine_mod.recover_after_restart()
    turns = len(client.turn_calls())
    eng = engine_mod.get_engine("d1")
    eng.client, eng.meta_lookup, eng.plan_lookup = client, fake_meta, fake_plan
    await eng.continue_()
    await eng.task
    d = eng.debate()
    assert d["status"] == "concluded" and d["answer_pending"] is None
    assert len(client.turn_calls()) == turns and len(db.query("SELECT * FROM verdicts")) == 1
    engine_mod._engines.clear()


async def test_the_failure_note_names_what_timed_out(monkeypatch):
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, seats=2, max_rounds=1)
    await eng.post_user_message("Q")
    await eng.task
    for actor, model, kind in [("Koala", "gemma3:12b", "turn-timeout"), ("Panda", "qwen3.8", "summary-timeout")]:
        db.execute(
            "INSERT INTO usage (debate_id, topic, actor, model, kind, prompt_tokens, output_tokens, duration_ms, searches, "
            "pages, created_at) VALUES ('d1', 1, ?, ?, ?, 0, 0, 240000, 0, 0, ?)",
            [actor, model, kind, db.now()],
        )
    note = eng._failure_note(eng.debate(), "took longer than 10 minutes")
    assert "Koala (gemma3:12b): turn" in note and "Panda (qwen3.8): summary" in note and "quick mode" in note


# ---------------------------------------------------------------- chair pick, metrics, levels


async def sized_meta(endpoint_id, model, num_ctx):
    return {
        "thinking": False,
        "est_bytes": {"model-0": 5, "model-1": 20, "model-2": 10}.get(model, 1),
        "family": model,
        "params": "8B",
    }


async def test_largest_member_picks_chair_researcher_and_title():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, auto_chair=True, research=True)
    eng.meta_lookup = sized_meta
    await eng.post_user_message("Q")
    await eng.task
    d = eng.debate()
    assert d["chair_picked_by"] == "Panda"  # model-1 is the largest
    assert (d["chair_handle"], d["chair_model"]) == ("Panda", "model-1")
    assert (d["researcher_handle"], d["researcher_model"]) == ("Koala", "model-2")
    assert d["chair_reason"] == "clear, careful writer" and d["title"] == "Test title"
    pick_call = [c for c in client.calls if c[1][0]["content"].startswith("You organize")][0]
    assert pick_call[0] == "model-1" and "Otter: model-0" in pick_call[1][1]["content"]
    answer_call = [
        c for c in client.calls if c[1][0]["content"].startswith("You are the chair of an AI council. You turn")
    ]
    assert answer_call[-1][0] == "model-1"  # the chair writes the final answer
    audit_call = [c for c in client.calls if c[1][0]["content"].startswith("You audit")]
    assert audit_call and audit_call[-1][0] != "model-1"  # and a different model checks it
    assert (
        "Panda will chair — “clear, careful writer”"
        in db.query_one("SELECT content FROM messages WHERE author_kind = 'system' ORDER BY id LIMIT 1")["content"]
    )


async def test_chair_pick_falls_back_to_picker_on_bad_reply():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    client.pick_reply = "I think Panda would be great!"
    eng = make_debate(client, auto_chair=True)
    eng.meta_lookup = sized_meta
    await eng.post_user_message("Q")
    await eng.task
    d = eng.debate()
    assert d["chair_handle"] == "Panda" and d["researcher_handle"] == "Panda" and d["title"] == ""


async def test_metrics_cover_every_model_call_and_search():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, research=True)
    await eng.post_user_message("Q")
    await eng.task
    m = eng.metrics(1)
    actors = {a["actor"]: a for a in m["actors"]}
    assert set(actors) == {"Otter", "Panda", "Koala", "Beagle", "Chair"}
    # two turns, plus auditing the chair's answer (the auditor is a council model other than the chair's)
    assert actors["Otter"]["calls"] == 3 and actors["Otter"]["output_tokens"] == 30
    assert actors["Otter"]["prompt_tokens"] > 0  # estimated when the server doesn't report it
    # opening brief (plan + brief) and one claim check; one search batch each
    assert actors["Beagle"]["calls"] == 3 and actors["Beagle"]["searches"] == 2 and actors["Beagle"]["pages"] > 0
    assert m["totals"]["calls"] == len([c for c in client.calls])
    assert eng.snapshot()["metrics"][1]["totals"]["searches"] == 2
    seat_msg = db.query_one("SELECT * FROM messages WHERE author_kind = 'seat' LIMIT 1")
    assert seat_msg["duration_ms"] is not None and seat_msg["prompt_tokens"] > 0


async def test_reading_level_rewrite_is_cached():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client)
    await eng.post_user_message("Q")
    await eng.task
    vid = db.query_one("SELECT id FROM verdicts")["id"]
    assert await eng.rewrite_level(vid, "simple") == "BOTTOM LINE: simpler words"
    n = len(client.calls)
    assert await eng.rewrite_level(vid, "simple") == "BOTTOM LINE: simpler words"
    assert len(client.calls) == n
    rewrite_prompt = client.calls[-1][1][1]["content"]
    assert "VERDICT TEXT" in rewrite_prompt and "12-year-old" in rewrite_prompt


async def test_beagle_mention_from_user_after_answer():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client)
    await eng.post_user_message("Q")
    await eng.task
    await eng.post_user_message("@Beagle what changed in Go 1.30?")
    await eng.task
    req = researcher_messages()[-1]
    assert req["research_request"] == "what changed in Go 1.30?" and eng.debate()["topic"] == 1


# ---------------------------------------------------------------- clarifying interview

ASK = '{"action": "ask", "question": "What is your budget?", "options": ["Under $1k", "Under $2k"]}'
SUMMARY = '{"action": "summarize", "brief": "Pick a laptop under $2k for local LLMs", "assumptions": ["Budget under $2k", "Runs 30B models"]}'


async def test_clear_conundrum_starts_debate_without_questions():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client)
    await eng.post_user_message("Q")
    await eng.task
    assert eng.debate()["status"] == "concluded"
    assert db.query("SELECT * FROM messages WHERE author_kind = 'moderator'") == []


async def test_interview_asks_then_summarizes_then_debates_with_clarified_question():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    client.intake_replies = [ASK, SUMMARY]
    eng = make_debate(client)
    events = eng.bus.subscribe()
    await eng.post_user_message("Which laptop?")
    await eng.task
    assert eng.debate()["status"] == "clarifying"
    q = db.query_one("SELECT * FROM messages WHERE author_kind = 'moderator'")
    assert q["content"] == "What is your budget?" and json.loads(q["meta_json"])["options"] == [
        "Under $1k",
        "Under $2k",
    ]
    assert any(e["type"] == "attention" for e in [events.get_nowait() for _ in range(events.qsize())])

    await eng.post_user_message("Under $2k")
    await eng.task
    assert eng.debate()["status"] == "confirming"
    summary = db.query("SELECT * FROM messages WHERE author_kind = 'moderator' ORDER BY id")[-1]
    assert json.loads(summary["meta_json"])["kind"] == "summary"
    assert client.turn_calls() == []  # nothing debated yet

    await eng.confirm_intake()
    await eng.task
    assert eng.debate()["status"] == "concluded"
    prompt = client.turn_calls("Otter")[0][0][1]["content"]
    assert "Clarified with the user: Pick a laptop under $2k" in prompt and "- Budget under $2k" in prompt
    # the answer to the chair's question isn't treated as a debate interjection
    assert "User: Under $2k" not in prompt


async def test_question_budget_forces_a_summary():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    client.intake_replies = [ASK, ASK, ASK, ASK]
    eng = make_debate(client)
    await eng.post_user_message("Q")
    await eng.task
    for answer in ("a", "b", "c"):
        await eng.post_user_message(answer)
        await eng.task
    assert eng.debate()["status"] == "confirming"
    kinds = [
        json.loads(m["meta_json"])["kind"]
        for m in db.query("SELECT meta_json FROM messages WHERE author_kind = 'moderator' ORDER BY id")
    ]
    assert kinds == ["question", "question", "question", "summary"]


async def test_the_chair_sizes_the_debate_unless_the_caller_set_the_rounds():
    for fixed, expected in [(0, 4), (1, 1)]:
        db.connect(":memory:")
        client = FakeClient(lambda h, r, m: reply("REFINE"))
        client.intake_replies = ['{"action": "clear", "rounds": 4}']
        eng = make_debate(client, max_rounds=1)
        db.update("debates", "d1", rounds_fixed=fixed)
        await eng.post_user_message("Which laptop should I buy?")
        await eng.task
        assert eng.debate()["max_rounds"] == expected, fixed


async def test_skip_interview_uses_answers_so_far():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    client.intake_replies = [ASK]
    eng = make_debate(client)
    await eng.post_user_message("Which laptop?")
    await eng.task
    await eng.confirm_intake()  # "Skip, just start" before answering
    await eng.task
    assert eng.debate()["status"] == "concluded"
    assert "Clarified" not in client.turn_calls("Otter")[0][0][1]["content"]


async def test_details_after_summary_trigger_a_new_summary():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    client.intake_replies = [SUMMARY, SUMMARY.replace("under $2k", "under $1.5k")]
    eng = make_debate(client)
    await eng.post_user_message("Which laptop?")
    await eng.task
    assert eng.debate()["status"] == "confirming"
    await eng.post_user_message("Actually my budget is $1.5k")
    await eng.task
    assert eng.debate()["status"] == "confirming"
    summaries = db.query("SELECT content FROM messages WHERE author_kind = 'moderator' ORDER BY id")
    assert summaries[-1]["content"] == "Pick a laptop under $1.5k for local LLMs"


async def test_unusable_intake_reply_just_starts():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    client.intake_replies = ["Sure! Let me think..."]
    eng = make_debate(client)
    await eng.post_user_message("Q")
    await eng.task
    assert eng.debate()["status"] == "concluded"


async def test_intake_retries_once_on_unparseable_reply():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    client.intake_replies = [ASK, "oops not json", SUMMARY]
    eng = make_debate(client)
    await eng.post_user_message("Which laptop?")
    await eng.task
    await eng.post_user_message("Under $2k")
    await eng.task
    assert eng.debate()["status"] == "confirming"
    last = db.query("SELECT content FROM messages WHERE author_kind = 'moderator' ORDER BY id")[-1]
    assert last["content"] == "Pick a laptop under $2k for local LLMs"


async def test_multiline_assumptions_are_split():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    client.intake_replies = [
        '{"action": "summarize", "brief": "B", "assumptions": ["Stays 7+ years\\nHas $300k saved\\n- Wants good schools"]}'
    ]
    eng = make_debate(client)
    await eng.post_user_message("Q")
    await eng.task
    meta = json.loads(db.query_one("SELECT meta_json FROM messages WHERE author_kind = 'moderator'")["meta_json"])
    assert meta["assumptions"] == ["Stays 7+ years", "Has $300k saved", "Wants good schools"]


async def test_trivial_conundrum_is_answered_directly_by_the_chair():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    client.intake_replies = ['{"action": "direct"}']
    eng = make_debate(client)
    await eng.post_user_message("hi")
    await eng.task
    assert eng.debate()["status"] == "concluded"
    assert client.turn_calls() == []  # no agent spoke
    verdict = db.query_one("SELECT * FROM verdicts")
    assert verdict["reason"] == "direct" and verdict["rounds"] == 0
    chair = db.query_one("SELECT * FROM messages WHERE author_kind = 'chair'")
    assert chair["status"] == "done" and chair["content"]


async def test_the_chair_thinks_before_a_direct_answer():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    client.intake_replies = ['{"action": "direct"}']
    eng = make_debate(client)
    await eng.post_user_message("A bat and a ball cost $1.10 together; the bat costs $1 more. What does the ball cost?")
    await eng.task
    direct = [(m, kw) for _, m, kw in client.calls if "answer it yourself" in m[0]["content"]]
    assert len(direct) == 1 and direct[0][1]["think"] is True
    assert "check it before you write" in direct[0][0][1]["content"]


async def test_a_direct_answer_that_only_thought_is_asked_again_without_thinking():
    class ThinkingOnly(FakeClient):
        async def stream(self, endpoint, model, messages, **kw):
            if "answer it yourself" in messages[0]["content"] and kw.get("think"):
                self.calls.append((model, messages, kw))
                yield Chunk("thinking", "hmm " * 50)
                yield Chunk("done", stats={})
                return
            async for c in super().stream(endpoint, model, messages, **kw):
                yield c

    client = ThinkingOnly(lambda h, r, m: reply("AGREE"))
    client.intake_replies = ['{"action": "direct"}']
    eng = make_debate(client)
    await eng.post_user_message("What is 17 x 23?")
    await eng.task
    direct = [kw for _, m, kw in client.calls if "answer it yourself" in m[0]["content"]]
    assert [kw["think"] for kw in direct] == [True, False]
    chair = db.query_one("SELECT * FROM messages WHERE author_kind = 'chair'")
    assert chair["content"] == "VERDICT TEXT" and "answering without thinking" in chair["thinking"]


async def test_a_code_review_is_never_answered_directly():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    client.intake_replies = ['{"action": "direct"}']
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message("Review this code change.\n\nDiff:\n```diff\n-a = 1\n+a = 2\n```")
    await eng.task
    assert client.turn_calls()  # the council spoke
    assert db.query_one("SELECT reason FROM verdicts")["reason"] != "direct"


DIFF_Q = "Review this code change.\n\nDiff:\n```diff\n-@app.get('/c/<int:id>')\n+@app.get('/c/<id>')\n```"
REVIEW = "BOTTOM LINE: Don't merge.\n\n## Key points\n- SQL injection in db.py:15.\n\n## Details\n**High**\n1. Fix it."


async def test_a_code_review_answer_gets_back_findings_the_summary_dropped():
    client = FakeClient(lambda h, r, m: reply("AGREE", text="The <int:> converter was removed (api.py:14)."))
    client.verdict_reply = REVIEW
    client.review_gap_reply = (
        '{"missing": [{"finding": "The <int:> route converter was removed", "where": "api.py:14", '
        '"severity": "high", "raised_by": "Otter"}]}'
    )
    client.review_add_reply = REVIEW + "\n2. Restore `<int:id>` (api.py:14)."
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message(DIFF_Q)
    await eng.task
    chair = db.query_one("SELECT * FROM messages WHERE author_kind = 'chair'")
    assert "Restore `<int:id>`" in chair["content"]
    meta = json.loads(chair["meta_json"])["review"]
    assert meta["revised"] and meta["missing"][0]["where"] == "api.py:14"
    gap = client.calls_with("You check an AI council's code review")[0]
    assert "converter was removed" in gap[1]["content"]  # the auditor saw the debate


async def test_a_revision_that_loses_the_review_is_not_used():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    client.verdict_reply = REVIEW
    client.review_gap_reply = '{"missing": [{"finding": "No timeout", "severity": "high"}]}'
    client.review_add_reply = "Sure, here it is."  # no bottom line, no sections
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message(DIFF_Q)
    await eng.task
    chair = db.query_one("SELECT * FROM messages WHERE author_kind = 'chair'")
    assert chair["content"].startswith("BOTTOM LINE: Don't merge.")
    assert not json.loads(chair["meta_json"])["review"].get("revised")


async def test_other_questions_are_not_checked_as_reviews():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message("Rent or buy?")
    await eng.task
    assert client.calls_with("You check an AI council's code review") == []


async def test_chair_sets_the_number_of_rounds():
    client = FakeClient(lambda h, r, m: reply("REFINE"))
    client.intake_replies = ['{"action": "clear", "rounds": 2}']
    eng = make_debate(client, max_rounds=5)
    await eng.post_user_message("Q")
    await eng.task
    d = eng.debate()
    assert d["max_rounds"] == 2 and d["round"] == 2 and d["status"] == "concluded"


async def test_rounds_are_clamped_to_1_to_10():
    client = FakeClient(lambda h, r, m: reply("REFINE"))
    client.intake_replies = ['{"action": "clear", "rounds": 99}']
    eng = make_debate(client, autopilot=False)
    await eng.post_user_message("Q")
    await eng.task
    assert eng.debate()["max_rounds"] == 10


async def test_expert_rewrite_asks_for_a_diagram_only_at_expert_level():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client)
    await eng.post_user_message("Q")
    await eng.task
    vid = db.query_one("SELECT id FROM verdicts")["id"]
    await eng.rewrite_level(vid, "simple")
    assert "Mermaid" not in client.calls[-1][1][1]["content"]
    await eng.rewrite_level(vid, "expert")
    assert 'add a "## Diagram" section' in client.calls[-1][1][1]["content"]


# ------------------------------------------------------------ turn limits and speed


async def test_a_turn_that_runs_too_long_is_stopped_and_the_seat_sits_out(monkeypatch):
    monkeypatch.setattr(engine_mod, "TURN_MAX_SECONDS", 0.05)
    client = FakeClient(lambda h, r, m: reply("REFINE"))
    client.gates["Koala"] = asyncio.Event()  # Koala never finishes: a model looping in its thinking
    eng = make_debate(client, seats=3, max_rounds=2)
    await eng.post_user_message("Q")
    await eng.task
    errors = db.query("SELECT content FROM messages WHERE author_kind = 'system' AND status = 'error'")
    assert len(errors) == 1 and "sits out the rest of this debate" in errors[0]["content"]
    assert len(client.turn_calls("Koala")) == 1  # not asked again in round 2
    assert len(client.turn_calls("Otter")) == 2
    failed = db.query("SELECT * FROM usage WHERE kind = 'turn-failed'")
    assert failed and failed[0]["actor"] == "Koala"


async def test_a_server_error_gets_one_more_try_without_thinking():
    tries = {"Koala": 0}

    def turn(h, r, m):
        if h == "Koala" and r == 1:
            tries["Koala"] += 1
            if tries["Koala"] == 1:
                raise RuntimeError("output does not match the expected peg")
        return reply("REFINE")

    client = FakeClient(turn)
    eng = make_debate(client, seats=3, max_rounds=1)
    await eng.post_user_message("Q")
    await eng.task
    assert not db.query("SELECT * FROM messages WHERE status = 'error'")
    koala = client.turn_calls("Koala")
    assert len(koala) == 2 and koala[0][1]["think"] is True and koala[1][1]["think"] is False
    msg = db.query_one("SELECT m.* FROM messages m JOIN seats s ON s.id = m.seat_id WHERE s.handle = 'Koala'")
    assert msg["status"] == "done" and "trying once more without thinking" in msg["thinking"]


async def test_an_empty_reply_gets_one_more_try():
    tries = []

    def turn(h, r, m):
        if h == "Koala":
            tries.append(r)
            if len(tries) == 1:
                return "", ""
        return reply("REFINE")

    client = FakeClient(turn)
    eng = make_debate(client, seats=3, max_rounds=1)
    await eng.post_user_message("Q")
    await eng.task
    assert not db.query("SELECT * FROM messages WHERE status = 'error'")
    assert len(client.turn_calls("Koala")) == 2


async def test_a_seat_that_fails_twice_sits_out():
    def turn(h, r, m):
        if h == "Koala":
            raise RuntimeError("HTTP 500")
        return reply("REFINE")

    client = FakeClient(turn)
    eng = make_debate(client, seats=3, max_rounds=2)
    await eng.post_user_message("Q")
    await eng.task
    errors = db.query("SELECT content FROM messages WHERE author_kind = 'system' AND status = 'error'")
    assert len(errors) == 1 and "sits out the rest of this debate" in errors[0]["content"]
    assert len(client.turn_calls("Koala")) == 2  # one try and one retry in round 1, none in round 2


async def test_a_much_slower_seat_keeps_its_seat_with_brief_replies_after_round_1(monkeypatch):
    monkeypatch.setattr(engine_mod, "SLOW_TURN_SECONDS", 0.0)
    client = FakeClient(lambda h, r, m: reply("REFINE"))
    client.gates["Koala"] = asyncio.Event()
    eng = make_debate(client, seats=4, max_rounds=2)

    async def slow_koala():
        await wait_for(lambda: len(client.turn_calls("Koala")) == 1)
        await asyncio.sleep(0.1)  # far longer than the others' turns
        client.gates["Koala"].set()

    slow = asyncio.create_task(slow_koala())
    await eng.post_user_message("Q")
    await eng.task
    await slow
    koala = client.turn_calls("Koala")
    assert len(koala) == 2  # it still speaks in round 2
    assert koala[0][1]["num_predict"] == engine_mod.TURN_MAX_TOKENS
    assert koala[1][1]["num_predict"] == engine_mod.BRIEF_TURN_TOKENS and koala[1][1]["think"] is False
    assert all(kw["num_predict"] == engine_mod.TURN_MAX_TOKENS for _, kw in client.turn_calls("Otter"))


async def test_a_turn_waiting_behind_another_debate_keeps_its_full_time(monkeypatch):
    monkeypatch.setattr(engine_mod, "TURN_MAX_SECONDS", 0.2)
    client = FakeClient(lambda h, r, m: reply("REFINE"))
    eng = make_debate(client, seats=3, max_rounds=1)
    server = server_key(eng._endpoint(1))
    other, release = Caller("d2", "Hedgehog", "big-model", "Order API review"), asyncio.Event()

    async def other_debate():
        async with engine_mod.QUEUE.turn(server, other):
            await release.wait()

    holder = asyncio.create_task(other_debate())
    await asyncio.sleep(0)
    await eng.post_user_message("Q")
    await wait_for(lambda: eng.waiting)
    assert (
        "is waiting its turn" in eng.waiting and "Hedgehog (big-model) is answering “Order API review”" in eng.waiting
    )
    assert eng.snapshot()["debate"]["waiting"] == eng.waiting
    await asyncio.sleep(0.4)  # longer than a turn may take: waiting must not count against it
    release.set()
    await holder
    await eng.task
    assert eng.waiting is None
    assert not db.query("SELECT * FROM usage WHERE kind = 'turn-failed'")
    assert len(client.turn_calls()) == 3
    assert all(r["duration_ms"] < 200 for r in db.query("SELECT duration_ms FROM usage WHERE kind = 'turn'"))


async def test_a_model_another_debate_wants_next_stays_loaded(monkeypatch):
    async def seq_plan(selection, num_ctx):
        return {"mode": "sequential"}

    client = FakeClient(lambda h, r, m: reply("REFINE"))
    eng = make_debate(client, autopilot=False, models=["m1", "m1", "m2"])
    eng.plan_lookup = seq_plan
    # Another debate is waiting for m2
    monkeypatch.setattr(engine_mod.QUEUE, "wanted_next", lambda server, model: model == "m2")
    await eng.post_user_message("Q")
    await eng.task
    keep = [kw["keep_alive"] for _, kw in client.turn_calls()]
    assert keep == ["5m", 0, "5m"]  # C's model isn't unloaded: the other debate reuses it


async def test_turns_cap_their_output_and_leave_room_for_it(monkeypatch):
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, seats=3, max_rounds=1)
    await eng.post_user_message("Q")
    await eng.task
    _, kw = client.turn_calls("Otter")[0]
    assert kw["num_predict"] == engine_mod.TURN_MAX_TOKENS
    assert kw["num_ctx"] >= engine_mod.TURN_MAX_TOKENS


async def test_agents_think_in_round_one_only():
    client = FakeClient(lambda h, r, m: reply("REFINE"))
    eng = make_debate(client, seats=3, max_rounds=2)
    await eng.post_user_message("Q")
    await eng.task
    thinks = [kw.get("think") for _, kw in client.turn_calls("Otter")]
    assert thinks == [True, False]


async def test_a_turn_spent_thinking_gets_one_answer_without_thinking():
    def turn(h, r, m):
        return ("", "thinking and thinking") if h == "Otter" and not turn.retried.get(h) else reply("AGREE")

    turn.retried = {}
    client = FakeClient(turn)
    orig = client.stream

    async def stream(endpoint, model, messages, **kw):
        agent = AGENT_RE.search(messages[0]["content"])
        if agent and kw.get("think") is False:
            turn.retried[agent.group(1)] = True
        async for chunk in orig(endpoint, model, messages, **kw):
            yield chunk

    client.stream = stream
    eng = make_debate(client, seats=3, max_rounds=1)
    await eng.post_user_message("Q")
    await eng.task
    otter = [m for m in db.query("SELECT * FROM messages WHERE author_kind = 'seat'") if m["seat_id"] == 1]
    assert turn.retried.get("Otter") and otter and otter[0]["status"] == "done" and "My view." in otter[0]["content"]
    assert not db.query("SELECT * FROM messages WHERE author_kind = 'system' AND status = 'error'")


async def test_the_debate_stops_when_a_round_changes_nothing():
    client = FakeClient(lambda h, r, m: reply("REFINE"))
    client.draft_reply = "BOTTOM LINE: x\n- a\nCHANGED: No change."
    eng = make_debate(client, seats=3, max_rounds=5)
    await eng.post_user_message("Q")
    await eng.task
    verdict = db.query_one("SELECT * FROM verdicts")
    assert verdict["reason"] == "converged" and verdict["rounds"] == 2


async def test_a_disagreement_keeps_the_debate_going_even_if_the_draft_held():
    client = FakeClient(lambda h, r, m: reply("DISAGREE" if h == "Otter" else "REFINE"))
    client.draft_reply = "BOTTOM LINE: x\n- a\nCHANGED: No change."
    eng = make_debate(client, seats=3, max_rounds=3)
    await eng.post_user_message("Q")
    await eng.task
    assert db.query_one("SELECT reason FROM verdicts")["reason"] == "max_rounds"


def test_a_models_context_window_never_shrinks_mid_debate():
    eng = make_debate(FakeClient(lambda h, r, m: reply("AGREE")), num_ctx=8192)
    big = [{"role": "user", "content": "x " * 40000}]
    small = [{"role": "user", "content": "hi"}]
    grown = eng._num_ctx(big, 4096, "m")
    assert grown > 8192 and eng._num_ctx(small, 4096, "m") == grown
    assert eng._num_ctx(small, 4096, "other") == 8192


def test_reviews_leave_out_models_far_slower_than_the_rest():
    council = [{"model": m} for m in ("big-slow", "a", "b", "c", "d", "e")]
    speeds = {"big-slow": 160, "a": 60, "b": 26, "c": 24, "d": 35, "e": 46}
    assert [m["model"] for m in inventory.drop_slow(council, 5, speeds)] == ["a", "b", "c", "d", "e"]
    assert len(inventory.drop_slow(council, 6, speeds)) == 6  # not enough others: keep everyone
    assert inventory.drop_slow(council, 5, {"a": 200, "b": 190}) == council  # all slow alike: nothing stands out


async def test_metrics_show_time_lost_to_failed_turns(monkeypatch):
    monkeypatch.setattr(engine_mod, "TURN_MAX_SECONDS", 0.05)
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    client.gates["Koala"] = asyncio.Event()
    eng = make_debate(client, seats=3, max_rounds=1)
    await eng.post_user_message("Q")
    await eng.task
    m = eng.metrics(1)
    koala = next(a for a in m["actors"] if a["actor"] == "Koala")
    assert koala["lost_ms"] > 0 and m["totals"]["lost_ms"] == koala["lost_ms"]


def test_finding_lines_keep_the_points_and_drop_the_chatter():
    text = """I agree with Otter.

**High**
1. SQL injection: `customer_id` is %-formatted into the query.
| File | Line | Issue |
|---|---|---|
| `db.py` | 25 | The search term is f-stringed into LIKE |
The route converter was dropped at api.py:14, so ids arrive as strings.
Short.

STANCE: AGREE
POSITION: Fix the injection first, it is the worst of them."""
    lines = engine_mod.finding_lines(text)
    assert lines == [
        "1. SQL injection: `customer_id` is %-formatted into the query.",
        "| `db.py` | 25 | The search term is f-stringed into LIKE |",
        "The route converter was dropped at api.py:14, so ids arrive as strings.",
    ]


async def test_gpt_oss_gets_low_effort_where_thinking_is_off():
    eng = make_debate(FakeClient(lambda h, r, m: reply("AGREE")))
    assert await eng._thinking_flag(1, "gpt-oss:20b", False) == "low"
    assert await eng._thinking_flag(1, "gpt-oss:20b", True) is True
    assert await eng._thinking_flag(1, "qwen3:14b", False) is False


async def test_the_review_check_is_one_call_and_says_what_it_is_doing():
    client = FakeClient(lambda h, r, m: reply("AGREE", text="- The <int:> converter was removed (api.py:14)."))
    client.verdict_reply = REVIEW
    eng = make_debate(client, max_rounds=2)
    phases = []
    publish = eng.bus.publish

    def spy(event):
        if event.get("type") == "debate_updated" and "phase" in event.get("debate", {}):
            phases.append(event["debate"]["phase"])
        publish(event)

    eng.bus.publish = spy
    await eng.post_user_message(DIFF_Q)
    await eng.task
    assert len(client.calls_with("You check an AI council's code review")) == 1
    assert phases and "checking the review" in phases[0] and phases[-1] is None


# ------------------------------------------------------------ cancel, fail and resume


async def test_cancel_stops_for_good_keeps_what_was_said_and_can_resume():
    client = FakeClient(lambda h, r, m: reply("REFINE"))
    client.gates["Koala"] = asyncio.Event()  # Koala is mid-turn when the user cancels
    eng = make_debate(client, seats=3, max_rounds=1)
    await eng.post_user_message("Q")
    await wait_for(lambda: len(client.turn_calls("Koala")) == 1)
    await eng.cancel()
    assert eng.debate()["status"] == "cancelled" and not eng.is_running()
    said = db.query("SELECT author_kind, status, content FROM messages WHERE author_kind IN ('seat', 'system')")
    assert [m["status"] for m in said if m["author_kind"] == "seat"] == ["done", "done", "stopped"]
    assert "Cancelled by you" in said[-1]["content"]
    await eng.continue_()  # Resume
    client.gates["Koala"].set()
    await eng.task
    assert eng.debate()["status"] == "concluded"


async def test_an_engine_error_marks_the_debate_failed_and_it_can_resume(monkeypatch):
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, seats=3, max_rounds=1)
    original = eng._run_rounds
    calls = []

    async def crash_once():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("disk full")
        await original()

    monkeypatch.setattr(eng, "_run_rounds", crash_once)
    await eng.post_user_message("Q")
    await eng.task
    assert eng.debate()["status"] == "failed"
    await eng.continue_()
    await eng.task
    assert eng.debate()["status"] == "concluded"


async def test_metrics_total_the_time_spent_writing_for_the_speed():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, seats=2, max_rounds=1)
    await eng.post_user_message("Q")
    await eng.task
    db.execute(
        "INSERT INTO usage (debate_id, topic, actor, model, kind, prompt_tokens, output_tokens, duration_ms, searches, "
        "pages, created_at) VALUES ('d1', 1, 'Beagle', 'm', 'search', 0, 0, 90000, 3, 3, ?)",
        [db.now()],
    )
    totals = eng.metrics(1)["totals"]
    writing = db.query_one("SELECT SUM(duration_ms) AS ms FROM usage WHERE output_tokens > 0")["ms"] or 0
    assert totals["gen_ms"] == writing and totals["duration_ms"] >= writing + 90000


# ------------------------------------------------------------ memory


def test_a_watcher_that_stops_reading_is_dropped_and_told_to_reconnect(monkeypatch):
    monkeypatch.setattr(engine_mod.EventBus, "QUEUE_LIMIT", 3)
    bus = engine_mod.EventBus()
    stalled, reading = bus.subscribe(), bus.subscribe()
    for i in range(5):
        bus.publish({"i": i})
        reading.get_nowait()
    assert bus._subscribers == [reading]
    assert stalled.get_nowait() is None and stalled.empty()  # only the "end the stream" marker is left


async def test_answered_debates_nobody_watches_are_let_go(monkeypatch):
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client)
    engine_mod._engines["d1"] = eng
    await eng.post_user_message("Q")
    await eng.task
    for debate_id, status in [("d2", "paused"), ("d3", "concluded")]:
        db.execute(
            "INSERT INTO debates (id, title, created_at, chair_endpoint_id, chair_model, max_rounds, num_ctx, status) "
            "VALUES (?, 't', ?, 1, 'm', 2, 8192, ?)",
            [debate_id, db.now(), status],
        )
        engine_mod.get_engine(debate_id)
    watched = engine_mod.get_engine("d3").bus.subscribe()
    monkeypatch.setattr(engine_mod, "ENGINE_IDLE_SECONDS", -1)
    engine_mod.get_engine("d4")
    assert set(engine_mod._engines) == {"d2", "d3", "d4"}  # d1 is answered and unwatched; d2 can still resume
    engine_mod.get_engine("d3").bus.unsubscribe(watched)
    engine_mod._engines.clear()


async def test_the_sidebar_hears_when_a_conundrum_changes_status_but_not_every_token():
    q = engine_mod.APP_BUS.subscribe()
    try:
        client = FakeClient(lambda h, r, m: reply("AGREE"))
        eng = make_debate(client)
        await eng.post_user_message("Q")
        await eng.task
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        assert events and all(e == {"type": "debate_changed", "id": "d1"} for e in events)
        assert len(events) < 20  # status and round changes, not streamed text
    finally:
        engine_mod.APP_BUS.unsubscribe(q)


# ---------------------------------------------------------------- Python checks


@pytest.fixture
def fake_python(monkeypatch):
    ran = []

    async def run(code):
        ran.append(code)
        return pyrun.Result(True, "42", 0.01)

    monkeypatch.setattr(pyrun, "sandbox", lambda: "fake")
    monkeypatch.setattr(pyrun, "run", run)
    return ran


CHECK = "Let me compute it.\n@Python:\n```python\nprint(6 * 7)\n```\nSo it's 40.\n\nSTANCE: AGREE\nPOSITION: 40"


async def test_an_agent_finishes_its_turn_with_what_its_python_check_printed(fake_python):
    def turn(handle, round_no, messages):
        if "Now finish your message" in messages[-1]["content"]:
            return reply("AGREE", "The program says 42.", "42")
        return (CHECK, "") if handle == "Panda" else reply("AGREE", position="42")

    client = FakeClient(turn)
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message("What is 6 times 7?")
    await eng.task
    assert fake_python == ["print(6 * 7)"]
    msg = db.query_one("SELECT * FROM messages m JOIN seats s ON s.id = m.seat_id WHERE s.handle = 'Panda'")
    # what it guessed before the output is dropped; the output and its finished reply are kept
    assert "print(6 * 7)" in msg["content"] and "42" in msg["content"] and "So it's 40" not in msg["content"]
    assert msg["position_line"] == "42"
    follow = [m for m, _ in client.turn_calls("Panda") if "Now finish your message" in m[-1]["content"]]
    assert len(follow) == 1 and "42" in follow[0][-1]["content"] and "So it's 40" not in follow[0][-2]["content"]


async def test_agents_are_told_about_python_checks_only_when_a_sandbox_exists(fake_python, monkeypatch):
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message("How many primes are below 1000?")
    await eng.task
    assert all("@Python:" in m[0]["content"] for m, _ in client.turn_calls())

    db.connect(":memory:")
    monkeypatch.setattr(pyrun, "sandbox", lambda: None)
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message("How many primes are below 1000?")
    await eng.task
    assert not any("@Python:" in m[0]["content"] for m, _ in client.turn_calls())


async def test_a_question_that_quotes_code_gets_python_checks(fake_python):
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message("What does this print?\n\n```python\nprint(-7 // 2)\n```")
    await eng.task
    assert all("@Python:" in m[0]["content"] for m, _ in client.turn_calls())


async def test_a_code_review_gets_no_python_checks(fake_python):
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message(DIFF_Q)
    await eng.task
    assert not any("@Python:" in m[0]["content"] for m, _ in client.turn_calls())


async def test_a_direct_answer_can_use_a_python_check(fake_python):
    class Chair(FakeClient):
        async def stream(self, endpoint, model, messages, **kw):
            if "answer it yourself" in messages[0]["content"]:
                self.calls.append((model, messages, kw))
                done = "Now finish your message" in messages[-1]["content"]
                yield Chunk("content", "It's **42**." if done else CHECK)
                yield Chunk("done", stats={"tokens": 5})
                return
            async for c in super().stream(endpoint, model, messages, **kw):
                yield c

    client = Chair(lambda h, r, m: reply("AGREE"))
    client.intake_replies = ['{"action": "direct"}']
    eng = make_debate(client)
    await eng.post_user_message("What is 6 times 7?")
    await eng.task
    chair = db.query_one("SELECT * FROM messages WHERE author_kind = 'chair'")
    assert fake_python == ["print(6 * 7)"]
    assert chair["content"].endswith("It's **42**.") and "So it's 40" not in chair["content"]
    assert chair["tokens"] == 10  # both calls counted


async def test_an_agent_that_calls_its_own_python_tool_goes_on_without_checks(fake_python):
    """gpt-oss has a Python tool of its own: told it can run Python, it calls that, and Ollama can't parse it."""

    class ToolCaller(FakeClient):
        async def stream(self, endpoint, model, messages, **kw):
            if "You are Koala" in messages[0]["content"] and "@Python:" in messages[0]["content"]:
                self.calls.append((model, messages, kw))
                raise RuntimeError("error parsing tool call: raw='print(1)'")
                yield  # pragma: no cover
            async for c in super().stream(endpoint, model, messages, **kw):
                yield c

    client = ToolCaller(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, max_rounds=2)
    await eng.post_user_message("How many primes are below 1000?")
    await eng.task
    koala = client.turn_calls("Koala")
    assert ["@Python:" in m[0]["content"] for m, _ in koala] == [True, False, False]  # once, then never again
    assert [kw["think"] for _, kw in koala] == [True, True, False]  # its retry still thinks in round 1
    msgs = db.query("SELECT m.* FROM messages m JOIN seats s ON s.id = m.seat_id WHERE s.handle = 'Koala'")
    assert [m["status"] for m in msgs] == ["done", "done"]


async def test_a_chair_that_calls_its_own_python_tool_answers_without_checks(fake_python):
    class ToolCaller(FakeClient):
        async def stream(self, endpoint, model, messages, **kw):
            if "answer it yourself" in messages[0]["content"] and "@Python:" in messages[1]["content"]:
                self.calls.append((model, messages, kw))
                raise RuntimeError("error parsing tool call: raw='print(1)'")
                yield  # pragma: no cover
            async for c in super().stream(endpoint, model, messages, **kw):
                yield c

    client = ToolCaller(lambda h, r, m: reply("AGREE"))
    client.intake_replies = ['{"action": "direct"}']
    eng = make_debate(client)
    await eng.post_user_message("What is 6 times 7?")
    await eng.task
    chair = db.query_one("SELECT * FROM messages WHERE author_kind = 'chair'")
    assert chair["status"] == "done" and chair["content"] == "VERDICT TEXT"


# ---------------------------------------------------------------- sources nobody checked

CITED = (
    "BOTTOM LINE: Send **reminder texts**.\n\n## Key points\n"
    "- **Reminders work:** Cochrane (2019) meta-analyses show reminders cut no-shows by 18-23%.\n"
    "- **Cheap:** texts cost little."
)


async def test_without_research_an_answer_that_names_a_study_is_rewritten_without_it():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    client.verdict_reply = CITED
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message("How do I cut no-shows at my clinic?")
    await eng.task
    rewrite = client.calls_with("doesn't cite sources nobody checked")
    assert len(rewrite) == 1 and "Cochrane (2019)" in rewrite[0][1]["content"]
    chair = db.query_one("SELECT * FROM messages WHERE author_kind = 'chair'")
    assert "Cochrane" not in chair["content"] and "Reminders usually help" in chair["content"]
    meta = json.loads(chair["meta_json"])["unchecked_sources"]
    assert meta["revised"] and meta["original"] == CITED and meta["left"] == []


async def test_a_rewrite_that_drops_part_of_the_answer_is_not_used():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    client.verdict_reply = CITED
    client.unsourced_reply = "Reminders help."  # no bottom line, no sections
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message("How do I cut no-shows at my clinic?")
    await eng.task
    chair = db.query_one("SELECT * FROM messages WHERE author_kind = 'chair'")
    assert chair["content"] == CITED
    assert json.loads(chair["meta_json"])["unchecked_sources"]["revised"] is False


async def test_agents_and_the_chair_are_told_not_to_cite_when_nothing_can_be_looked_up():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message("How do I cut no-shows at my clinic?")
    await eng.task
    assert all("Don't name studies" in m[0]["content"] for m, _ in client.turn_calls())
    verdict = client.calls_with("You are the chair of an AI council. You turn")
    assert "Web research was off" in verdict[0][1]["content"] and "BMJ meta-analysis" not in verdict[0][1]["content"]


async def test_with_research_the_answer_keeps_its_checked_studies():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    client.verdict_reply = CITED
    eng = make_debate(client, max_rounds=1, research=True, search=FakeSearch())
    await eng.post_user_message("How do I cut no-shows at my clinic?")
    await eng.task
    assert not client.calls_with("doesn't cite sources nobody checked")
    assert not any("Don't name studies" in m[0]["content"] for m, _ in client.turn_calls())


async def test_a_question_that_quotes_code_gets_the_usual_roles_not_reviewers():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message("What does this print?\n\n```python\nprint(-7 // 2)\n```")
    await eng.task
    roles = {s["role"] for s in eng.seats()}
    assert "Security expert" not in roles and "Skeptic" in roles
    assert db.query_one("SELECT reason FROM verdicts")["reason"] != "direct"  # still debated


async def test_quoted_code_the_user_wants_checked_for_bugs_gets_reviewers():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message("Any bugs in this?\n\n```python\ndef div(a, b):\n    return a / b\n```")
    await eng.task
    assert "Security expert" in {s["role"] for s in eng.seats()}


async def test_advice_always_gets_the_council_even_when_the_chair_would_answer_directly():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    client.intake_replies = ['{"action": "direct"}']
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message("How should I teach my 8-year-old to handle money?")
    await eng.task
    assert client.turn_calls()  # the council spoke
    assert db.query_one("SELECT reason FROM verdicts")["reason"] != "direct"


def test_advice_questions_are_told_apart_from_simple_ones():
    from backend.engine import is_advice

    assert is_advice("Should I repair my car or buy another?")
    assert is_advice("We're planning 7 days in Japan with two kids")
    assert is_advice("Is it worth learning Rust?")
    assert not is_advice("What is the capital of France?")
    assert not is_advice("Hi there!")


# ---------------------------------------------------------------- tracing


async def test_every_model_call_is_traced_with_its_timings_and_outcome():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message("Q")
    await eng.task
    rows = db.query("SELECT * FROM usage WHERE debate_id = 'd1' ORDER BY id")
    turns = [r for r in rows if r["kind"] == "turn"]
    assert len(turns) == 3 and all(r["outcome"] == "ok" for r in turns)
    assert all(r["started_at"] and r["queued_ms"] >= 0 and r["first_token_ms"] is not None for r in turns)
    assert any(r["kind"] == "answer" and r["outcome"] == "ok" for r in rows)


async def test_a_failed_call_is_traced_with_its_error():
    class FailingChair(FakeClient):
        async def stream(self, endpoint, model, messages, **kw):
            if model == "chair-model" and "chair of an AI council" in messages[0]["content"]:
                raise ProviderError("the model server stopped mid-reply")
                yield  # pragma: no cover
            async for c in super().stream(endpoint, model, messages, **kw):
                yield c

    client = FailingChair(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message("Q")
    await eng.task
    failed = db.query("SELECT * FROM usage WHERE outcome = 'error'")
    assert len(failed) == 2 and all(r["model"] == "chair-model" and r["kind"] == "answer" for r in failed)
    assert failed[0]["error"] == "the model server stopped mid-reply" and failed[0]["first_token_ms"] is None


async def test_an_empty_reply_and_a_stopped_turn_are_traced():
    client = EmptyChair(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message("Q")
    await eng.task
    answers = db.query("SELECT * FROM usage WHERE kind = 'answer' ORDER BY id")
    assert [r["outcome"] for r in answers] == ["empty"] * 3  # it only thought: the chair twice, then the backup
    assert all(r["first_token_ms"] is not None for r in answers)
    db.connect(":memory:")

    client = FakeClient(lambda h, r, m: reply("AGREE"))
    client.gates["Koala"] = asyncio.Event()
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message("Q")
    await wait_for(lambda: len(client.turn_calls("Koala")) == 1)
    await eng.stop()
    stopped = db.query_one("SELECT * FROM usage WHERE outcome = 'stopped'")
    assert stopped["actor"] == "Koala" and stopped["error"] is None


# ---------------------------------------------------------------- the critic (research off)


def critique_calls(client):
    """The critic's first look at an answer (not its re-check of a fix)."""
    return [
        (model, m, kw)
        for model, m, kw in client.calls
        if m[0]["content"].startswith("You check an AI council's final")
        and "An answer was corrected" not in m[1]["content"]
    ]


SAVINGS = (
    "BOTTOM LINE: Pay $4,000 toward the card and **rebuild your emergency fund first**.\n\n## Key points\n"
    "- **Keep a cushion:** hold $4,000 in savings.\n- **Then rebuild savings** at $1,000 a month before paying more."
)
FLAG = (
    '{"problems": [{"check": "fit", "text": "rebuild your emergency fund first", '
    '"issue": "Saving at ~4% while carrying $8,000 at 24% costs the user money; pay the card first."}]}'
)


async def test_the_critic_flags_advice_that_doesnt_fit_the_numbers_and_the_chair_fixes_it():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    client.verdict_reply = SAVINGS
    client.critique_reply = FLAG
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message("I have $12,000 of card debt at 24% and $8,000 saved. Pay it off?")
    await eng.task
    critic = [(model, m) for model, m, _ in critique_calls(client)]
    assert len(critic) == 1 and critic[0][0] != "chair-model"  # a different model checks the chair's answer
    assert "24%" in critic[0][1][1]["content"] and "rebuild your emergency fund first" in critic[0][1][1]["content"]
    fix = client.calls_with("You correct your final answer before the user sees it")
    assert len(fix) == 1 and "Saving at ~4%" in fix[0][1]["content"]
    chair = db.query_one("SELECT * FROM messages WHERE author_kind = 'chair'")
    assert "pay off your emergency fund" in chair["content"] and "rebuild" not in chair["content"]
    meta = json.loads(chair["meta_json"])["critique"]
    assert meta["revised"] and meta["original"] == SAVINGS
    checks = {p["check"] for p in meta["problems"]}
    assert "fit" in checks and "missing" in checks  # the critic's, and code's: the answer skips the user's $12,000


async def test_an_answer_the_critic_passes_is_left_alone():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    client.verdict_reply = SAVINGS.replace("rebuild", "pay off")
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message("I have card debt. Pay it off?")
    await eng.task
    assert not client.calls_with("You correct your final answer before the user sees it")
    chair = db.query_one("SELECT * FROM messages WHERE author_kind = 'chair'")
    assert chair["content"] == SAVINGS.replace("rebuild", "pay off")
    assert json.loads(chair["meta_json"])["critique"] == {
        "checked": True,
        "critic": "Otter",
        "problems": [],
        "revised": False,
    }


async def test_a_fix_that_drops_part_of_the_answer_is_not_used():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    client.verdict_reply = SAVINGS
    client.critique_reply = FLAG
    client.critique_revise_reply = "Pay the card."
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message("I have card debt. Pay it off?")
    await eng.task
    chair = db.query_one("SELECT * FROM messages WHERE author_kind = 'chair'")
    assert chair["content"] == SAVINGS and json.loads(chair["meta_json"])["critique"]["revised"] is False


async def test_the_critic_runs_only_without_research_and_never_on_a_review():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, max_rounds=1, research=True, search=FakeSearch())
    await eng.post_user_message("How do I cut no-shows at my clinic?")
    await eng.task
    assert not client.calls_with("You check an AI council's final answer")

    db.connect(":memory:")
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message(DIFF_Q)
    await eng.task
    assert not client.calls_with("You check an AI council's final answer")


async def test_the_critic_is_the_largest_other_model_by_its_real_size():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, max_rounds=1, models=["small:latest", "big:latest", "gpt-oss:20b"])
    sizes = {"small:latest": "4.3B", "big:latest": "27.8B", "gpt-oss:20b": "20.9B"}

    async def meta(endpoint_id, model, num_ctx):
        return {"thinking": True, "params": sizes.get(model)}

    eng.meta_lookup = meta
    await eng.post_user_message("I have card debt. Pay it off?")
    await eng.task
    critic = [model for model, _, _ in critique_calls(client)]
    assert critic == ["big:latest"]  # judged by name, "big:latest" would count as 0 and lose to gpt-oss:20b


async def test_the_critic_doesnt_think_and_tries_again_after_an_empty_reply():
    class QuietCritic(FakeClient):
        quiet = 1

        async def stream(self, endpoint, model, messages, **kw):
            first_look = messages[0]["content"].startswith("You check an AI council's final") and (
                "An answer was corrected" not in messages[1]["content"]
            )
            if first_look and self.quiet:
                self.quiet -= 1
                self.calls.append((model, messages, kw))
                yield Chunk("done", stats={})
                return
            async for c in super().stream(endpoint, model, messages, **kw):
                yield c

    client = QuietCritic(lambda h, r, m: reply("AGREE"))
    client.verdict_reply = SAVINGS
    client.critique_reply = FLAG
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message("I have $12,000 of card debt at 24% and $8,000 saved. Pay it off?")
    await eng.task
    assert [kw["think"] for _, _, kw in critique_calls(client)] == [False, False]
    chair = db.query_one("SELECT * FROM messages WHERE author_kind = 'chair'")
    assert json.loads(chair["meta_json"])["critique"]["revised"]  # the second try's flag was acted on


async def test_a_fix_is_rechecked_and_what_is_still_wrong_is_fixed_once_more():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    client.verdict_reply = SAVINGS
    client.critique_reply = FLAG
    client.recheck_reply = '{"problems": [{"check": "trace", "text": "pay off your emergency fund", "issue": "Reads oddly; say pay the card first."}]}'
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message("I have $12,000 of card debt at 24% and $8,000 saved. Pay it off?")
    await eng.task
    recheck = [m for _, m, _ in client.calls if "An answer was corrected" in m[1]["content"]]
    assert len(recheck) == 1 and "Saving at ~4%" in recheck[0][1]["content"]  # it checks the flagged problems
    assert len(client.calls_with("You correct your final answer before the user sees it")) == 2
    meta = json.loads(db.query_one("SELECT meta_json FROM messages WHERE author_kind = 'chair'")["meta_json"])
    assert meta["critique"]["refixed"] and meta["critique"]["rechecked"][0]["check"] == "trace"


async def test_a_fix_that_holds_is_not_fixed_again():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    client.verdict_reply = SAVINGS
    client.critique_reply = FLAG
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message("I have $12,000 of card debt at 24% and $8,000 saved. Pay it off?")
    await eng.task
    assert len(client.calls_with("You correct your final answer before the user sees it")) == 1
    meta = json.loads(db.query_one("SELECT meta_json FROM messages WHERE author_kind = 'chair'")["meta_json"])
    assert meta["critique"]["rechecked"] == [] and "refixed" not in meta["critique"]


async def test_a_plan_question_asks_for_a_schedule_in_the_answer():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message("I want to run my first half marathon in 16 weeks. How should I train?")
    await eng.task
    verdict = client.calls_with("You turn the council")[-1][1]["content"]
    assert "## Plan" in verdict and "add up" in verdict

    db.connect(":memory:")
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message("Should I replace my gas furnace with a heat pump?")
    await eng.task
    assert "## Plan" not in client.calls_with("You turn the council")[-1][1]["content"]


def test_plan_questions_are_told_apart():
    from backend.engine import is_plan

    assert is_plan("How should I train for a half marathon?")
    assert is_plan("What should I do in the next 90 days?")
    assert is_plan("We're planning 7 days in Japan in April")
    assert not is_plan("Is it worth learning Rust?")
    assert not is_plan("How should I teach my 8-year-old to handle money?")


async def test_the_recheck_doesnt_think():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    client.verdict_reply = SAVINGS
    client.critique_reply = FLAG
    eng = make_debate(client, max_rounds=1)
    await eng.post_user_message("I have $12,000 of card debt at 24% and $8,000 saved. Pay it off?")
    await eng.task
    recheck = [kw for _, m, kw in client.calls if "An answer was corrected" in m[1]["content"]]
    assert len(recheck) == 1 and recheck[0]["think"] is False
