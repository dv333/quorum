import pytest

from backend import attachments, db, engine as engine_mod, links
from backend.model_queue import ModelQueue
from tests.test_engine import FakeClient, make_debate, reply


@pytest.fixture(autouse=True)
def memory_db(monkeypatch, tmp_path):
    db.connect(":memory:")
    monkeypatch.setattr(attachments, "UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(engine_mod, "QUEUE", ModelQueue())
    yield


def test_links_are_found_without_trailing_punctuation_and_at_most_three():
    text = "Look at https://github.com/dv333/quorum. And (https://example.com/a?b=1), https://x.org/1 https://y.org/2"
    assert links.links_in(text) == ["https://github.com/dv333/quorum", "https://example.com/a?b=1", "https://x.org/1"]
    assert links.display("https://www.github.com/dv333/quorum/") == "github.com/dv333/quorum"


def test_a_repository_is_summarized_with_what_github_shows_today():
    repo = {
        "full_name": "dv333/quorum",
        "description": "Many minds. One answer.",
        "stargazers_count": 1234,
        "forks_count": 5,
        "open_issues_count": 2,
        "subscribers_count": 3,
        "language": "Python",
        "topics": ["llm", "ollama"],
        "license": {"spdx_id": "MIT"},
        "created_at": "2026-06-01T00:00:00Z",
        "pushed_at": "2026-09-30T10:00:00Z",
    }
    text = links.github_summary(repo, "# Quorum\nA council of local models.", ["README.md", "backend/"])
    assert "Stars: 1,234" in text and "Topics: llm, ollama" in text and "License: MIT" in text
    assert "Last push: 2026-09-30" in text and "Top-level files and folders: README.md, backend/" in text
    assert text.endswith("# Quorum\nA council of local models.")


def test_a_page_keeps_its_title_description_and_text_but_not_scripts():
    raw = (
        '<html><head><title>Pricing &amp; plans</title><meta name="description" content="Plans for teams"></head>'
        "<body><script>track()</script><nav>Menu</nav><p>Pro costs $20</p><p>Team costs $50</p></body></html>"
    )
    text = links.page_text("https://example.com/pricing", raw)
    assert "Title: Pricing & plans" in text and "Description: Plans for teams" in text
    assert "Pro costs $20" in text and "track()" not in text and "Menu" not in text


async def test_a_link_in_the_question_is_read_before_the_council_answers():
    opened = []

    async def read(url):
        opened.append(url)
        return "GitHub repository dv333/quorum\nStars: 0\nREADME:\nA council of local models."

    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, max_rounds=2)
    eng.read_link = read
    await eng.post_user_message("How do I grow https://github.com/dv333/quorum?")
    await eng.task
    assert opened == ["https://github.com/dv333/quorum"]
    turn = client.turn_calls("Otter")[0][0][1]["content"]
    assert "[ATTACHED FILE: github.com/dv333/quorum, the page at this link]" in turn and "Stars: 0" in turn
    snap = eng.snapshot()
    assert snap["attachments"][0]["kind"] == "link" and snap["attachments"][0]["read_by"] == "web"


async def test_a_link_that_wont_open_is_said_and_the_debate_goes_on():
    async def read(url):
        raise links.LinkError("the site answered HTTP 403")

    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, max_rounds=2)
    eng.read_link = read
    await eng.post_user_message("Is https://example.com/post right?")
    await eng.task
    (att,) = eng.snapshot()["attachments"]
    assert att["error"] == "Couldn't open the link: the site answered HTTP 403"
    assert db.query_one(
        "SELECT * FROM messages WHERE author_kind = 'system' AND content LIKE 'Couldn''t open example.com/post%'"
    )
    assert eng.debate()["status"] == "concluded"


def test_the_fact_checker_is_told_to_leave_forecasts_out():
    from backend import prompts

    text = "".join(m["content"] for m in prompts.claims_messages("Q", [], None, 6))
    assert "leave out predictions, estimates of effort and recommendations" in text
