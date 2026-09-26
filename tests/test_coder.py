"""The Coder (Claude Code or Codex reading the repository, read-only) and specialist roles for code debates."""

import subprocess
import sys

import pytest

from backend import coder, db
from backend.engine import ROLE_FOCUS, code_roles, is_code_debate, settle_roles
from backend.parsing import parse_coder_requests
from tests.test_engine import FakeClient, make_debate, reply


@pytest.fixture(autouse=True)
def fresh_db():
    db.connect(":memory:")


def test_agents_ask_the_coder_with_a_mention():
    text = "I think auth is fine.\n@Coder: where is the session token validated?\n```\n@Coder: not this one\n```"
    assert parse_coder_requests(text) == ["where is the session token validated?"]
    assert parse_coder_requests("no mention here") == []


def test_citations_are_checked_against_the_files(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "auth.py").write_text("line\n" * 30)
    text = "Tokens are checked in app/auth.py:12 and app/auth.py:20-25, not app/auth.py:99 or app/missing.py:3."
    refs = coder.check_refs(text, str(tmp_path))
    assert refs["found"] == ["app/auth.py:12", "app/auth.py:20-25"]
    assert refs["missing"] == ["app/auth.py:99", "app/missing.py:3"]
    assert "Couldn't find in the repository: `app/auth.py:99`" in coder.with_ref_note(text, refs)


def git_repo(path):
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "a.py").write_text("x = 1\n")
    for args in (["add", "."], ["-c", "user.email=t@e.com", "-c", "user.name=T", "commit", "-qm", "init"]):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


async def test_the_coder_runs_read_only_and_its_answer_is_returned(tmp_path, monkeypatch):
    git_repo(tmp_path)
    monkeypatch.setattr(
        coder, "command", lambda cli, prompt, repo: [sys.executable, "-c", "print('x is set in a.py:1')"]
    )
    text, run = await coder.run("claude", "where is x?", str(tmp_path))
    assert text == "x is set in a.py:1" and run["cli"] == "claude"


async def test_an_answer_that_changed_the_repository_is_discarded(tmp_path, monkeypatch):
    git_repo(tmp_path)
    script = "open('a.py', 'w').write('x = 2\\n'); print('done')"
    monkeypatch.setattr(coder, "command", lambda cli, prompt, repo: [sys.executable, "-c", script])
    with pytest.raises(coder.CoderError, match="changed the repository"):
        await coder.run("claude", "q", str(tmp_path))


async def test_a_slow_coder_times_out(tmp_path, monkeypatch):
    monkeypatch.setattr(
        coder, "command", lambda cli, prompt, repo: [sys.executable, "-c", "import time; time.sleep(5)"]
    )
    with pytest.raises(coder.CoderError, match="didn't answer within"):
        await coder.run("claude", "q", str(tmp_path), timeout=0.5)


def test_claude_code_gets_only_read_and_search_tools():
    cmd = coder.command("claude", "q", "/repo")
    assert cmd[:3] == ["claude", "-p", "q"] and cmd[cmd.index("--allowedTools") + 1] == "Read,Grep,Glob"
    assert coder.command("codex", "q", "/repo")[:4] == ["codex", "exec", "--sandbox", "read-only"]


def test_code_debates_get_specialists_for_what_the_change_touches():
    diff = (
        "```diff\n+cursor.execute('SELECT * FROM users WHERE id = %s', [uid])\n+resp = httpx.get(url, timeout=5)\n```"
    )
    assert is_code_debate(diff) and not is_code_debate("Should I buy an EV?")
    assert is_code_debate("Is this design right?", repo_path="/repo")
    roles = code_roles(diff, 8)
    assert roles[:4] == ["Skeptic", "Security expert", "Performance & reliability", "Database expert"]
    assert "Network expert" in roles and "Test engineer" in roles and "API & compatibility" not in roles
    plain = code_roles("```python\nx = 1\n```", 8)
    assert plain == ["Skeptic", "Security expert", "Performance & reliability", "Test engineer", "Pragmatist"]
    assert code_roles(diff, 3) == ["Skeptic", "Security expert", "Performance & reliability"]


def test_specialists_fill_seats_the_chair_left_generic():
    handles = ["Otter", "Panda", "Koala", "Penguin"]
    required = ["Skeptic", "Security expert", "Performance & reliability", "Database expert"]
    roles = settle_roles(handles, {"otter": {"role": "Optimist", "focus": ""}}, required)
    names = {r["role"] for r in roles.values()}
    assert {"Security expert", "Performance & reliability", "Database expert", "Skeptic"} <= names
    assert roles["Panda"]["focus"] == ROLE_FOCUS[roles["Panda"]["role"]]


async def test_a_debate_with_a_repository_starts_with_the_coders_brief(tmp_path, monkeypatch):
    git_repo(tmp_path)
    calls = []

    async def fake_run(cli, prompt, repo, timeout=coder.TIMEOUT_S):
        calls.append(prompt)
        return "The value lives in a.py:1.", {"cli": cli, "duration_ms": 5}

    monkeypatch.setattr(coder, "which", lambda: "claude")
    monkeypatch.setattr(coder, "run", fake_run)
    client = FakeClient(lambda h, r, m: reply("AGREE", text="@Coder: where else is x changed?"))
    eng = make_debate(client, max_rounds=1)
    db.update("debates", "d1", repo_path=str(tmp_path))
    await eng.post_user_message("Is x set correctly?")
    await eng.task
    rows = db.query("SELECT * FROM messages WHERE research_kind IN ('codebrief', 'code') ORDER BY id")
    assert rows[0]["research_kind"] == "codebrief" and "a.py:1" in rows[0]["content"]
    assert any(r["research_kind"] == "code" and r["research_request"] == "where else is x changed?" for r in rows)
    assert "brief a council of AI agents" in calls[0]


async def test_a_signed_out_coder_says_how_to_fix_it(tmp_path, monkeypatch):
    script = "import sys; print('Not logged in · Please run /login'); sys.exit(1)"
    monkeypatch.setattr(coder, "command", lambda cli, prompt, repo: [sys.executable, "-c", script])
    with pytest.raises(coder.CoderError, match="isn't signed in: run `claude`"):
        await coder.run("claude", "q", str(tmp_path))
