import asyncio
import json
import re

import pytest

from backend import db
from backend.config import HANDLES, SEAT_COLORS
from backend.engine import DebateEngine
from backend.providers import ChatClient, Chunk


AGENT_RE = re.compile(r"You are (Otter|Panda|Koala|Penguin|Hedgehog),")


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
            round_no = int(re.search(r"\(round (\d+)", messages[-1]["content"]).group(1))
            gate = self.gates.get(handle)
            if gate is not None:
                await gate.wait()
            content, thinking = self.turn_fn(handle, round_no, messages)
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
            content, thinking = "VERDICT TEXT", ""
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
def memory_db():
    db.connect(":memory:")
    yield


class FakeSearch:
    def __init__(self, fail=None):
        self.queries = []
        self.fail = fail

    async def __call__(self, query, limit):
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
    verdict_prompt = client.calls[-1][1][1]["content"]
    assert "Otter:" in verdict_prompt and "- Koala:" not in verdict_prompt  # C has no final position


async def test_too_few_responders_pauses():
    client = FakeClient(lambda h, r, m: (_ for _ in ()).throw(RuntimeError("down")) if h != "Otter" else reply("AGREE"))
    eng = make_debate(client)
    await eng.post_user_message("Q")
    await eng.task
    assert eng.debate()["status"] == "paused"
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
    verdict_prompt = client.calls[-1][1][1]["content"]
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
        if h == "Otter" and r == 1:
            return reply("REFINE", text="Not sure.\n@Beagle: what is the newest Go release?")
        return reply("REFINE")

    client = FakeClient(turn)
    eng = make_debate(client, research=True, autopilot=False)
    await eng.post_user_message("Q")
    await eng.task
    kinds = [(m["research_kind"], m["requested_by"], m["research_request"]) for m in researcher_messages()]
    assert ("request", "Otter", "what is the newest Go release?") in kinds
    b_prompt = client.turn_calls("Panda")[0][0][1]["content"]
    assert "Beagle (web research, asked by Otter): BRIEF" in b_prompt
    a_prompt = client.turn_calls("Otter")[0][0][1]["content"]
    assert "asked by Otter" not in a_prompt


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


async def test_fact_check_feeds_the_verdict():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, research=True)
    await eng.post_user_message("Q")
    await eng.task
    fc = [m for m in researcher_messages() if m["research_kind"] == "factcheck"]
    assert len(fc) == 1 and fc[0]["status"] == "done"
    chair_msg = db.query_one("SELECT id FROM messages WHERE author_kind = 'chair'")["id"]
    assert fc[0]["id"] < chair_msg
    verdict_prompt = client.calls[-1][1][1]["content"]
    assert "Web fact-check of key claims" in verdict_prompt and "BRIEF: fact [1]" in verdict_prompt


async def test_fact_check_skipped_when_nothing_to_check():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    client.plan_reply = lambda prompt: "NONE" if "Final positions" in prompt else "planned query"
    eng = make_debate(client, research=True)
    await eng.post_user_message("Q")
    await eng.task
    fc = [m for m in researcher_messages() if m["research_kind"] == "factcheck"][0]
    assert "needed a web check" in fc["content"]
    assert "Web fact-check" not in client.calls[-1][1][1]["content"]


async def test_search_outage_is_reported_once_per_run_and_debate_continues():
    from backend.firecrawl import SearchError

    client = FakeClient(lambda h, r, m: reply("REFINE", text=f"@Researcher: question from {h} here"))
    search = FakeSearch(fail=SearchError("Can't reach Firecrawl at http://localhost:3002"))
    eng = make_debate(client, research=True, autopilot=False, search=search)
    await eng.post_user_message("Q")
    await eng.task
    errors = [m for m in researcher_messages() if m["status"] == "error"]
    assert errors and all("Web search failed" in m["content"] for m in errors)
    assert len(search.queries) == 1  # later requests skip the dead service
    assert eng.debate()["status"] == "paused" and len(client.turn_calls()) == 3


async def test_empty_chair_answer_is_retried_then_reported():
    class EmptyChair(FakeClient):
        async def stream(self, endpoint, model, messages, **kw):
            if "chair of an AI council" in messages[0]["content"]:
                self.calls.append((model, messages, kw))
                yield Chunk("thinking", "drafting...")
                yield Chunk("done", stats={})
                return
            async for c in super().stream(endpoint, model, messages, **kw):
                yield c

    client = EmptyChair(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client)
    await eng.post_user_message("Q")
    await eng.task
    chair = db.query_one("SELECT * FROM messages WHERE author_kind = 'chair'")
    assert chair["status"] == "error" and "empty answer twice" in chair["content"]
    assert len(client.calls_with("chair of an AI council")) == 2
    assert eng.debate()["status"] == "concluded"
    assert client.calls_with("chair of an AI council")[0] and client.calls[-1][2]["think"] is False


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
    assert client.calls[-1][0] == "model-1"  # the chair writes the final answer
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
    assert actors["Otter"]["calls"] == 2 and actors["Otter"]["output_tokens"] == 20
    assert actors["Otter"]["prompt_tokens"] > 0  # estimated when the server doesn't report it
    # opening brief + fact-check: 2 plans + 2 briefs, 2 search batches
    assert actors["Beagle"]["calls"] == 4 and actors["Beagle"]["searches"] == 2 and actors["Beagle"]["pages"] > 0
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
