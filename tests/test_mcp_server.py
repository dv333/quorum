"""Quorum's MCP tools: reading files and diffs locally, starting and waiting on debates, and client setup."""

import subprocess

import pytest

from backend import cli, mcp_server


def test_files_are_read_locally_capped_and_kept_inside_the_root(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("print('hi')\n")
    (tmp_path / "big.txt").write_text("x" * (mcp_server.MAX_FILE_CHARS + 50))
    (tmp_path / "blob.bin").write_bytes(b"\0\1\2")
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("nope")
    text = mcp_server.read_files(["a.py", "big.txt", "blob.bin", "missing.py", str(outside)], str(tmp_path))
    assert "File a.py:\n```\nprint('hi')" in text
    assert "File big.txt (truncated)" in text
    assert "(skipped blob.bin: binary)" in text and "(skipped missing.py: not found)" in text
    assert "outside" in text and "nope" not in text


def test_the_uncommitted_change_is_the_diff_under_review(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "T")
    (tmp_path / "app.py").write_text("def add(a, b):\n    return a + b\n")
    git("add", ".")
    git("commit", "-qm", "first")
    (tmp_path / "app.py").write_text("def add(a, b):\n    return a - b\n")
    (tmp_path / "new.py").write_text("pass\n")
    diff = mcp_server.git_diff(str(tmp_path))
    assert "-    return a + b" in diff and "+    return a - b" in diff
    assert "(new file, not yet tracked: new.py)" in diff


def test_review_questions_ask_for_severities_and_the_focus():
    q = mcp_server.review_question("+x = 1", "", "security", "add login")
    assert "made to: add login" in q and "security: injection" in q
    assert "High (bugs, security or data-loss risks" in q and "```diff\n+x = 1\n```" in q


@pytest.fixture
def backend(monkeypatch):
    """A fake Quorum app behind cli.get / cli.post."""
    calls = {"post": [], "snaps": []}

    def get(path, raw=False):
        if path.startswith("/auto-council"):
            return {"seats": [{"endpoint_id": 1, "model": f"m{i}", "est_bytes": 1} for i in range(8)]}
        return calls["snaps"].pop(0) if len(calls["snaps"]) > 1 else calls["snaps"][0]

    def post(path, body=None):
        calls["post"].append((path, body))
        return {"debate": {"id": "abc123"}} if path == "/debates" else {}

    monkeypatch.setattr(cli, "get", get)
    monkeypatch.setattr(cli, "post", post)
    monkeypatch.setattr(cli, "POLL_SECONDS", 0)
    monkeypatch.setattr(mcp_server, "app_up", lambda: True)
    return calls


def snapshot(status, verdict=False, topic=1):
    return {
        "debate": {"id": "abc123", "status": status, "topic": topic, "round": 1, "max_rounds": 3},
        "seats": [{"id": 1}, {"id": 2}],
        "messages": [
            {"id": 5, "topic": topic, "round": 0, "author_kind": "moderator", "status": "done"},
            {"id": 9, "topic": topic, "round": 1, "author_kind": "chair", "status": "done"},
        ],
        "verdicts": [{"topic": topic, "message_id": 9}] if verdict else [],
    }


def test_quick_mode_is_a_small_council_for_one_round(backend):
    assert mcp_server.start("Q?", "quick", research=False, pack="code-review") == "abc123"
    path, body = backend["post"][0]
    assert path == "/debates" and body["max_rounds"] == 1 and len(body["seats"]) == 3
    assert body["pack"] == "code-review" and body["research_enabled"] is False
    mcp_server.start("Q?", "deep")
    assert backend["post"][-1][1]["max_rounds"] == 5 and "seats" not in backend["post"][-1][1]


def test_waiting_skips_the_chairs_questions_and_returns_the_answer(backend):
    backend["snaps"] += [snapshot("clarifying"), snapshot("running"), snapshot("concluded", verdict=True)]
    snap, done = mcp_server.wait("abc123", 5)
    assert done and ("/debates/abc123/intake/confirm", None) in backend["post"]


def test_an_unfinished_debate_says_how_to_get_the_answer(backend):
    backend["snaps"] += [snapshot("running")]
    snap, done = mcp_server.wait("abc123", 0)
    text = mcp_server.respond("abc123", snap, done)
    assert not done and "round 1 of 3" in text and 'quorum_result with conundrum_id="abc123"' in text
    assert "/#q/abc123" in text


def test_codex_setup_is_added_once(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text('model = "gpt-5"\n')
    assert "Added Quorum" in mcp_server.install("codex", apply=True)
    config = (tmp_path / "config.toml").read_text()
    assert (
        config.startswith('model = "gpt-5"') and "[mcp_servers.quorum]" in config and "tool_timeout_sec = 300" in config
    )
    assert "already" in mcp_server.install("codex", apply=True)
    assert "claude mcp add --scope user quorum --" in mcp_server.install("claude", apply=False)


async def test_the_server_offers_six_tools():
    server = mcp_server.build_server()
    names = {t.name for t in await server.list_tools()}
    assert names == {
        "quorum_ask",
        "quorum_review",
        "quorum_challenge",
        "quorum_result",
        "quorum_followup",
        "quorum_list",
    }


def test_a_stopped_local_engine_is_started_in_the_background(tmp_path, monkeypatch):
    calls = {"up": 0, "popen": []}

    def up():
        calls["up"] += 1
        return calls["up"] > 1  # down at first, up once started

    monkeypatch.setattr(mcp_server, "backend_up", up)
    monkeypatch.setattr(mcp_server, "LOG_PATH", tmp_path / "log")
    monkeypatch.setattr(mcp_server.subprocess, "Popen", lambda cmd, **kw: calls["popen"].append((cmd, kw)))
    monkeypatch.setattr(cli, "API_URL", "http://127.0.0.1:8002")
    note = mcp_server.ensure_backend()
    assert "started in the background" in note
    cmd, kw = calls["popen"][0]
    assert cmd[1:] == ["-m", "backend.main"] and kw["start_new_session"]


def test_a_remote_or_opted_out_engine_is_never_started(monkeypatch):
    monkeypatch.setattr(mcp_server, "backend_up", lambda: False)
    monkeypatch.setattr(mcp_server.subprocess, "Popen", lambda *a, **k: pytest.fail("started"))
    monkeypatch.setattr(cli, "API_URL", "https://quorum.example.com")
    with pytest.raises(cli.QuorumError, match="Start it with ./start.sh"):
        mcp_server.ensure_backend()
    monkeypatch.setattr(cli, "API_URL", "http://127.0.0.1:8002")
    monkeypatch.setenv("QUORUM_MCP_AUTOSTART", "0")
    with pytest.raises(cli.QuorumError):
        mcp_server.ensure_backend()


def test_tool_errors_come_back_as_text(monkeypatch):
    monkeypatch.setattr(mcp_server, "ensure_backend", lambda: "(started)")
    assert mcp_server.with_backend(lambda x: f"ok {x}", 1) == "(started)\n\nok 1"

    def boom():
        raise cli.QuorumError("no models")

    assert mcp_server.with_backend(boom) == "Quorum couldn't do that: no models"


def test_doctor_names_a_fix_for_each_problem(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "backend_up", lambda: True)
    monkeypatch.setattr(mcp_server.shutil, "which", lambda name: None)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr("backend.coder.which", lambda: None)

    def get(path):
        if path == "/inventory":
            return {"models": [{"local": True, "fit": "fits"}]}
        return {"ready": False}

    monkeypatch.setattr(cli, "get", get)
    report, ok = mcp_server.doctor()
    assert not ok
    assert "✗ uv isn't installed" in report
    assert "only 1 local model(s) fit" in report and "ollama pull" in report
    assert "! web search isn't available" in report
    assert "ChatGPT isn't supported yet" in report


def test_without_the_app_open_the_result_says_how_to_watch(backend, monkeypatch):
    monkeypatch.setattr(mcp_server, "app_up", lambda: False)
    backend["snaps"] += [snapshot("running")]
    snap, done = mcp_server.wait("abc123", 0)
    text = mcp_server.respond("abc123", snap, done)
    assert "/#q/" not in text and "./start.sh" in text
