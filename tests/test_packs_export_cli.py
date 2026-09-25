import json

import pytest

from backend import cli, db, export, packs
from tests.test_engine import FakeClient, FakeSearch, make_debate, reply


@pytest.fixture(autouse=True)
def memory_db():
    db.connect(":memory:")
    yield


# ------------------------------------------------------------------ topic packs


def test_builtin_packs_load_in_order():
    ids = [p["id"] for p in packs.load_all(user_dir="/nonexistent")]
    assert ids == packs.ORDER
    assert all(p["builtin"] and p["guidance"] and p["description"] for p in packs.load_all(user_dir="/nonexistent"))


def test_user_packs_override_builtins_and_bad_files_are_skipped(tmp_path):
    (tmp_path / "code-review.json").write_text(json.dumps({"name": "My review", "description": "d", "guidance": "g"}))
    (tmp_path / "team-retro.json").write_text(json.dumps({"name": "Retro", "description": "d", "guidance": "g"}))
    (tmp_path / "broken.json").write_text("{not json")
    (tmp_path / "no-guidance.json").write_text(json.dumps({"name": "x", "description": "d"}))
    (tmp_path / "Bad Name.json").write_text(json.dumps({"name": "x", "description": "d", "guidance": "g"}))
    by_id = {p["id"]: p for p in packs.load_all(user_dir=str(tmp_path))}
    assert by_id["code-review"]["name"] == "My review" and not by_id["code-review"]["builtin"]
    assert "team-retro" in by_id
    assert not {"broken", "no-guidance", "Bad Name"} & set(by_id)
    assert list(by_id)[-1] == "team-retro"  # your own packs come after the built-in ones


@pytest.mark.parametrize(
    "raw, problem",
    [
        ([], "JSON object"),
        ({"name": "x", "description": "d"}, '"guidance" is required'),
        ({"name": "x" * 41, "description": "d", "guidance": "g"}, "longer than 40"),
        ({"name": "x", "description": "d", "guidance": 3}, "must be text"),
    ],
)
def test_pack_validation_explains_the_problem(raw, problem):
    with pytest.raises(packs.PackError, match=problem):
        packs.validate("ok-id", raw)


def test_pack_prompt_keeps_its_trailing_space():
    pack = packs.validate("p", {"name": "n", "description": "d", "guidance": "g", "prompt": "Which is better for "})
    assert pack["prompt"] == "Which is better for "


async def test_pack_guidance_reaches_every_agent_and_the_chair():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client)
    pack = {"id": "decision", "name": "Stress-test", "guidance": "ARGUE AGAINST IT"}
    db.execute("UPDATE debates SET pack_json = ? WHERE id = 'd1'", [json.dumps(pack)])
    await eng.post_user_message("Should I quit my job?")
    await eng.task
    systems = [messages[0]["content"] for messages, _ in client.turn_calls()]
    assert systems and all("How to approach this conundrum: ARGUE AGAINST IT" in s for s in systems)
    chair = [m for _, m, _ in client.calls if m[0]["content"].startswith("You are the chair of an AI council")]
    assert "How to approach this conundrum: ARGUE AGAINST IT" in chair[-1][1]["content"]
    assert eng.snapshot()["debate"]["pack"]["name"] == "Stress-test"


async def test_no_pack_means_no_guidance_line():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client)
    await eng.post_user_message("Rust or Go?")
    await eng.task
    assert all("How to approach" not in messages[0]["content"] for messages, _ in client.turn_calls())


# ------------------------------------------------------------------ export


async def finished_debate(research=True):
    client = FakeClient(lambda h, r, m: reply("AGREE", text=f"{h} says hi in round {r}."))
    client.plan_reply = lambda prompt: "one query"
    eng = make_debate(client, research=research, search=FakeSearch())
    await eng.post_user_message("Rust or Go?")
    await eng.task
    return eng


async def test_export_has_answer_sources_and_optional_debate():
    eng = await finished_debate()
    short = export.to_markdown(eng.snapshot())
    assert short.startswith("# t\n") and "> Rust or Go?" in short
    assert "VERDICT TEXT" in short and "all agreed in round 2" in short
    assert "**Sources (Beagle's fact-check)**" in short and "(https://example.com/" in short
    assert "How the council got here" not in short
    full = export.to_markdown(eng.snapshot(), include_debate=True)
    assert "### Round 1" in full and "### Round 2" in full
    assert "**🦦 Otter** (model-0) · AGREE" in full and "Otter says hi in round 1." in full
    assert "STANCE:" not in full  # the stance footer is shown in the heading, not repeated
    assert "opening brief" in full


async def test_export_reading_levels_fall_back_to_standard():
    eng = await finished_debate(research=False)
    assert "simple version" not in export.to_markdown(eng.snapshot(), level="simple")
    verdict = db.query_one("SELECT * FROM verdicts")
    await eng.rewrite_level(verdict["id"], "simple")
    md = export.to_markdown(eng.snapshot(), level="simple")
    assert "simple version" in md and "**Bottom line: simpler words**" in md
    with pytest.raises(ValueError):
        export.to_markdown(eng.snapshot(), level="shouty")


async def test_export_includes_follow_ups():
    eng = await finished_debate(research=False)
    await eng.post_user_message("And for a CLI?")
    await eng.task
    md = export.to_markdown(eng.snapshot())
    assert md.count("VERDICT TEXT") == 2 and "## Follow-up" in md and "> And for a CLI?" in md


def test_export_filename_is_a_slug():
    assert export.filename({"debate": {"title": "Rent vs Buy: Cupertino 2026!"}}) == "rent-vs-buy-cupertino-2026.md"
    assert export.filename({"debate": {"title": ""}}) == "conundrum.md"


# ------------------------------------------------------------------ CLI


class FakeAPI:
    """Stands in for the HTTP API: serves scripted snapshots and records what the CLI posts."""

    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.posts = []

    def get(self, path, raw=False):
        if path.startswith("/debates/d1/export"):
            return "# exported\n"
        if path == "/debates/d1":
            return self.snapshots.pop(0) if len(self.snapshots) > 1 else self.snapshots[0]
        raise AssertionError(path)

    def post(self, path, body=None):
        self.posts.append((path, body))
        return {"debate": {"id": "d1"}}


def snap(status, messages=(), verdicts=(), topic=1):
    return {
        "debate": {
            "id": "d1",
            "status": status,
            "topic": topic,
            "max_rounds": 3,
            "chair_handle": "Otter",
            "title": "T",
        },
        "seats": [{"id": 1, "handle": "Otter", "model": "m"}],
        "messages": list(messages),
        "verdicts": list(verdicts),
        "metrics": {"1": {"totals": {"duration_ms": 5}}},
    }


QUESTION = {
    "id": 2,
    "topic": 1,
    "author_kind": "moderator",
    "status": "done",
    "content": "Budget?",
    "meta": {"kind": "question", "options": ["Low", "High"]},
}
ANSWER = {"id": 9, "topic": 1, "author_kind": "chair", "status": "done", "content": "BOTTOM LINE: go"}
VERDICT = {"id": 1, "topic": 1, "message_id": 9, "reason": "consensus", "rounds": 2}


@pytest.fixture
def fake_api(monkeypatch):
    def install(snapshots):
        api = FakeAPI(snapshots)
        monkeypatch.setattr(cli, "get", api.get)
        monkeypatch.setattr(cli, "post", api.post)
        monkeypatch.setattr(cli, "POLL_SECONDS", 0)
        return api

    return install


def test_cli_skips_the_chairs_questions_when_not_interactive(fake_api):
    api = fake_api([snap("clarifying", [QUESTION]), snap("running"), snap("concluded", [ANSWER], [VERDICT])])
    cli.wait_for_answer("d1", interactive=False, progress=cli.Progress(quiet=True))
    assert api.posts == [("/debates/d1/intake/confirm", None)]


def test_cli_answers_a_question_by_number(fake_api, monkeypatch):
    api = fake_api([snap("clarifying", [QUESTION]), snap("intake"), snap("concluded", [ANSWER], [VERDICT])])
    monkeypatch.setattr(cli, "_ask_tty", lambda prompt: "2")
    cli.wait_for_answer("d1", interactive=True, progress=cli.Progress(quiet=True))
    assert api.posts == [("/debates/d1/messages", {"content": "High"})]


def test_cli_waits_for_the_answer_message_to_finish(fake_api):
    streaming = {**ANSWER, "status": "streaming"}
    api = fake_api([snap("concluded", [streaming], [VERDICT]), snap("concluded", [ANSWER], [VERDICT])])
    result = cli.wait_for_answer("d1", interactive=False, progress=cli.Progress(quiet=True))
    assert result["messages"][0]["status"] == "done" and api.posts == []


def test_cli_json_result(fake_api):
    fake_api([snap("concluded", [ANSWER], [VERDICT])])
    out = json.loads(cli._result("d1", "standard", False, as_json=True))
    assert out["answer"] == "BOTTOM LINE: go" and out["reason"] == "consensus" and out["markdown"] == "# exported\n"
    assert out["url"].endswith("/#q/d1") and out["metrics"] == {"duration_ms": 5}


def test_cli_reports_an_unreachable_server(monkeypatch, capsys):
    monkeypatch.setattr(cli, "API_URL", "http://127.0.0.1:9")  # nothing listens on the discard port
    assert cli.main(["list"]) == 1
    assert "Can't reach Quorum" in capsys.readouterr().err
