import io
import json
import shutil
import zipfile

import pytest

from backend import attachments, db, engine as engine_mod
from backend.model_queue import ModelQueue
from tests.test_engine import FakeClient, make_debate, reply

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture(autouse=True)
def memory_db(monkeypatch, tmp_path):
    db.connect(":memory:")
    monkeypatch.setattr(attachments, "UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(engine_mod, "QUEUE", ModelQueue())
    yield


def docx(*paragraphs):
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="x"><w:body>{body}</w:body></w:document>')
    return buf.getvalue()


def test_text_html_and_word_files_are_read_when_uploaded():
    assert attachments.save("notes.md", b"# Lease\nRent is $2,000.")["chars"] > 0
    page = attachments.save("post.html", b"<html><script>x()</script><p>Buy &amp; hold</p></html>")
    assert attachments.get(page["id"])["text"] == "Buy & hold"
    doc = attachments.save("offer.docx", docx("Salary: $120,000", "Start: May 1"))
    assert attachments.get(doc["id"])["text"] == "Salary: $120,000\nStart: May 1"
    assert "path" not in doc  # the app never sees where files are kept


def test_images_wait_for_a_model_and_bad_files_say_why(monkeypatch):
    img = attachments.save("feed.png", PNG)
    assert img["kind"] == "image" and attachments.get(img["id"])["text"] is None
    with pytest.raises(attachments.AttachmentError, match="Can't read .exe"):
        attachments.save("setup.exe", b"MZ")
    with pytest.raises(attachments.AttachmentError, match="empty"):
        attachments.save("a.txt", b"")
    with pytest.raises(attachments.AttachmentError, match="Word document"):
        attachments.save("fake.docx", b"not a zip")
    monkeypatch.setattr(attachments, "ATTACHMENT_MAX_BYTES", 10)
    with pytest.raises(attachments.AttachmentError, match="over"):
        attachments.save("big.txt", b"x" * 11)


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="needs pdftotext")
def test_a_pdf_is_read_with_its_page_numbers(tmp_path):
    # A one-page PDF with the text "Hello lease"
    content = b"BT /F1 12 Tf 72 720 Td (Hello lease) Tj ET"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + o + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref))
    saved = attachments.save("lease.pdf", out.getvalue())
    assert saved["pages"] == 1
    assert "[page 1]" in attachments.get(saved["id"])["text"] and "Hello lease" in attachments.get(saved["id"])["text"]


def test_the_question_carries_the_files_within_a_budget_and_says_what_was_cut():
    rows = [
        {"name": "a.txt", "kind": "text", "text": "A" * 50 + "\n" + "B" * 5000, "digest": None},
        {"name": "b.png", "kind": "image", "text": "A chart of rents", "digest": None},
        {"name": "c.pdf", "kind": "pdf", "text": "long", "digest": "The summary"},
        {"name": "d.png", "kind": "image", "text": None, "digest": None},  # not read: left out
    ]
    text = attachments.block(rows, limit=3000)
    assert text.count("[ATTACHED FILE:") == 3 and "d.png" not in text
    assert "a.txt, text (the first 1% of the file)" in text
    assert "b.png, an image, described by a model that can see it]" in text
    assert "c.pdf, pdf (a summary" in text and "The summary" in text
    assert attachments.strip_block("Is this fair?" + text) == "Is this fair?"


def test_checks_on_the_question_ignore_the_files():
    q = "Is this lease fair?" + attachments.block(
        [{"name": "l.txt", "kind": "text", "text": "Should I plan a 5 day trip? Review this code", "digest": None}],
        8000,
    )
    assert not engine_mod.is_plan(q) and not engine_mod.is_advice(q)


def vision_meta(vision):
    async def meta(endpoint_id, model, num_ctx):
        return {"thinking": False, "vision": vision}

    return meta


async def test_an_image_is_described_before_the_council_sees_the_question():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, max_rounds=2)
    eng.meta_lookup = vision_meta(True)
    img = attachments.save("feed.png", PNG)
    await eng.post_user_message("", [img["id"]])
    await eng.task
    read = client.calls_with("You describe images")
    assert len(read) == 1 and read[0][1]["images"]  # the chair can see, so it reads the image
    user = db.query_one("SELECT * FROM messages WHERE author_kind = 'user'")
    assert user["content"] == engine_mod.FILE_QUESTION
    assert json.loads(user["meta_json"])["attachments"][0]["name"] == "feed.png"
    turn = client.turn_calls("Otter")[0][0][1]["content"]
    assert "[ATTACHED FILE: feed.png, an image" in turn and "VERDICT TEXT" in turn  # the fake's description
    snap = eng.snapshot()
    assert snap["attachments"][0]["read_by"] == "chair-model" and snap["attachments"][0]["read"]


async def test_without_a_model_that_can_see_the_debate_goes_on_and_says_so(monkeypatch):
    async def no_vision(num_ctx, force=False):
        return {"models": [{"model": "text-only", "endpoint_id": 1, "chat": True, "vision": False}]}

    monkeypatch.setattr(engine_mod.inventory, "inventory", no_vision)
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, max_rounds=2)
    eng.meta_lookup = vision_meta(False)
    img = attachments.save("feed.png", PNG)
    await eng.post_user_message("Is this post true?", [img["id"]])
    await eng.task
    assert "ollama pull gemma3:4b" in attachments.get(img["id"])["error"]
    assert db.query_one(
        "SELECT * FROM messages WHERE author_kind = 'system' AND content LIKE 'Couldn''t read feed.png%'"
    )
    assert eng.debate()["status"] == "concluded"


async def test_a_long_document_is_summarized_by_the_chair_once(monkeypatch):
    monkeypatch.setattr(engine_mod, "ATTACHMENT_CHARS", 100)
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, max_rounds=2)
    doc = attachments.save("contract.txt", b"Clause. " * 200)
    await eng.post_user_message("Anything unfair here?", [doc["id"]])
    await eng.task
    assert len(client.calls_with("You summarize a document")) == 1
    assert "contract.txt, text (a summary" in client.turn_calls("Otter")[0][0][1]["content"]


async def test_a_file_already_used_in_another_debate_isnt_taken():
    client = FakeClient(lambda h, r, m: reply("AGREE"))
    eng = make_debate(client, max_rounds=2)
    doc = attachments.save("a.txt", b"hello")
    db.execute(
        "INSERT INTO debates (id, title, created_at, chair_endpoint_id, chair_model, max_rounds, autopilot, "
        "criteria_json, custom_rubric, num_ctx) VALUES ('d2', '', ?, 1, 'm', 2, 1, '[]', '', 8192)",
        [db.now()],
    )
    db.update("attachments", doc["id"], debate_id="d2", topic=1)
    await eng.post_user_message("Q", [doc["id"]])
    await eng.task
    assert attachments.for_topic("d1", 1) == []


def test_openai_style_servers_get_images_as_content_parts():
    from backend.providers import _openai_message

    plain = {"role": "user", "content": "hi"}
    assert _openai_message(plain) is plain
    parts = _openai_message({"role": "user", "content": "What is this?", "images": ["QUJD"]})["content"]
    assert parts[0] == {"type": "text", "text": "What is this?"}
    assert parts[1]["image_url"]["url"] == "data:image/png;base64,QUJD"


def test_the_upload_endpoint_returns_the_file_or_says_why_not():
    from fastapi.testclient import TestClient

    from backend import main

    client = TestClient(main.app)
    ok = client.post("/api/uploads", files={"file": ("notes.txt", b"Rent is $2,000", "text/plain")})
    assert ok.status_code == 200 and ok.json()["name"] == "notes.txt" and ok.json()["chars"] == 14
    bad = client.post("/api/uploads", files={"file": ("run.exe", b"MZ", "application/octet-stream")})
    assert bad.status_code == 400 and "Can't read .exe" in bad.json()["detail"]
    assert client.post("/api/debates", json={"question": "  "}).status_code == 400
