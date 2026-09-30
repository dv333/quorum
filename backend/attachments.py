"""Files the user attaches to a question: images, PDFs, Word documents and text.

Documents are read when they're uploaded; images when the debate starts, by a model that can see (see the engine).
Everything the council reads goes into the question itself, between markers, so every step sees the same file."""

import html
import os
import re
import shutil
import subprocess
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import db
from .config import ATTACHMENT_MAX_BYTES, UPLOADS_DIR

IMAGE = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
TEXT = {".txt", ".md", ".markdown", ".csv", ".json", ".log", ".html", ".htm"}
KINDS = {**{e: "image" for e in IMAGE}, **{e: "text" for e in TEXT}, ".pdf": "pdf", ".docx": "docx"}
ACCEPT = sorted(KINDS)

# The text of an attached file inside the question, and how to take it out again (for the checks that look at what the
# user typed: is it advice, a plan, a code review)
_BLOCK = re.compile(r"\n*\[ATTACHED FILE: [^\]\n]*\].*?\[END OF FILE\]", re.S)


class AttachmentError(ValueError):
    pass


def kind_of(name: str) -> Optional[str]:
    return KINDS.get(Path(name).suffix.lower())


def _safe_name(name: str) -> str:
    base = Path(name).name.strip() or "file"
    return re.sub(r"[^\w.\- ()]+", "_", base)[:120]


def save(name: str, data: bytes) -> Dict[str, Any]:
    """Store an upload and read its text now when it's a document. Linked to a debate when the question is sent."""
    kind = kind_of(name)
    if not kind:
        raise AttachmentError(f"Can't read {Path(name).suffix or 'this kind of'} files. Try: {', '.join(ACCEPT)}")
    if not data:
        raise AttachmentError("The file is empty.")
    if len(data) > ATTACHMENT_MAX_BYTES:
        raise AttachmentError(f"The file is over {ATTACHMENT_MAX_BYTES // 1_000_000} MB.")
    att_id = uuid.uuid4().hex[:12]
    folder = Path(UPLOADS_DIR) / att_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / _safe_name(name)
    path.write_bytes(data)
    text, pages = None, None
    if kind != "image":
        try:
            text, pages = extract_text(path, kind)
        except AttachmentError:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        if not text.strip():
            shutil.rmtree(folder, ignore_errors=True)
            raise AttachmentError(
                "No text found in this file. If it's a scan, attach the pages as images instead."
                if kind == "pdf"
                else "No text found in this file."
            )
    db.execute(
        "INSERT INTO attachments (id, name, kind, path, size, text, pages, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [att_id, path.name, kind, str(path), len(data), text, pages, db.now()],
    )
    return public(get(att_id))


def add_link(url: str) -> Dict[str, Any]:
    """A link from the question: read when the debate starts (see links.py). The path holds the address."""
    from .links import display

    att_id = uuid.uuid4().hex[:12]
    db.execute(
        "INSERT INTO attachments (id, name, kind, path, size, created_at) VALUES (?, ?, 'link', ?, 0, ?)",
        [att_id, display(url), url, db.now()],
    )
    return public(get(att_id))


def get(att_id: str) -> Optional[Dict[str, Any]]:
    return db.query_one("SELECT * FROM attachments WHERE id = ?", [att_id])


def public(row: Dict[str, Any]) -> Dict[str, Any]:
    """What the app shows: the name and kind, never the path."""
    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "size": row["size"],
        "pages": row["pages"],
        "chars": len(row["text"] or ""),
        "error": row.get("error"),
    }


def for_topic(debate_id: str, topic: int) -> List[Dict[str, Any]]:
    return db.query(
        "SELECT * FROM attachments WHERE debate_id = ? AND topic = ? ORDER BY created_at", [debate_id, topic]
    )


def link(att_ids: List[str], debate_id: str, topic: int, message_id: int) -> List[Dict[str, Any]]:
    """Attach uploads to the message they were sent with. Uploads already used elsewhere, or unknown, are skipped."""
    out = []
    for att_id in att_ids:
        row = get(att_id)
        if not row or (row["debate_id"] and row["debate_id"] != debate_id):
            continue
        db.execute(
            "UPDATE attachments SET debate_id = ?, topic = ?, message_id = ? WHERE id = ?",
            [debate_id, topic, message_id, att_id],
        )
        out.append(public(get(att_id)))
    return out


def extract_text(path: Path, kind: str) -> "tuple[str, Optional[int]]":
    """The text of a document, and its number of pages for a PDF."""
    if kind == "text":
        raw = path.read_bytes().decode("utf-8", errors="replace")
        if path.suffix.lower() in (".html", ".htm"):
            raw = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
            raw = html.unescape(re.sub(r"<[^>]+>", " ", raw))
            raw = re.sub(r"[ \t]+", " ", raw)
            raw = re.sub(r"\n\s*\n+", "\n\n", raw)
        return raw.strip(), None
    if kind == "docx":
        return _docx_text(path), None
    if kind == "pdf":
        return _pdf_text(path)
    raise AttachmentError("Images are read by a model when the debate starts.")


def _docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, KeyError):
        raise AttachmentError("This doesn't look like a Word document (.docx).")
    paragraphs = []
    for p in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S):
        p = re.sub(r"<w:tab/>", "\t", p)
        text = "".join(re.findall(r"<w:t(?: [^>]*)?>([^<]*)</w:t>", p))
        paragraphs.append(html.unescape(text))
    return "\n".join(paragraphs).strip()


def _pdf_text(path: Path) -> "tuple[str, Optional[int]]":
    """With pypdf when it's installed, otherwise poppler's pdftotext."""
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        PdfReader = None
    if PdfReader is not None:
        try:
            reader = PdfReader(str(path))
            pages = [(p.extract_text() or "").strip() for p in reader.pages]
        except Exception as e:
            raise AttachmentError(f"Couldn't read this PDF: {e}")
        return "\n\n".join(f"[page {i}]\n{t}" for i, t in enumerate(pages, 1) if t), len(pages)
    tool = shutil.which("pdftotext")
    if not tool:
        raise AttachmentError("Reading PDFs needs pypdf (uv add pypdf) or poppler's pdftotext (brew install poppler).")
    try:
        out = subprocess.run([tool, "-layout", str(path), "-"], capture_output=True, timeout=60, check=True).stdout
    except (subprocess.SubprocessError, OSError) as e:
        raise AttachmentError(f"Couldn't read this PDF: {e}")
    pages = out.decode("utf-8", errors="replace").split("\f")
    text = "\n\n".join(f"[page {i}]\n{t.strip()}" for i, t in enumerate(pages, 1) if t.strip())
    return text, len([p for p in pages if p.strip()]) or None


def block(rows: List[Dict[str, Any]], limit: int) -> str:
    """The files' text for the question, within a budget shared by all of them. What didn't fit is said, not hidden."""
    readable = [r for r in rows if (r["digest"] or r["text"] or "").strip()]
    if not readable:
        return ""
    share = max(1000, limit // len(readable))
    parts = []
    for r in readable:
        text = (r["digest"] or r["text"]).strip()
        note = ""
        if r["digest"]:
            note = " (a summary: the file is too long to include in full)"
        elif len(text) > share:
            cut = text[:share].rsplit("\n", 1)[0] or text[:share]
            note = f" (the first {round(100 * len(cut) / len(text))}% of the file)"
            text = cut
        label = {"image": "an image, described by a model that can see it", "link": "the page at this link"}.get(
            r["kind"], r["kind"]
        )
        parts.append(f"[ATTACHED FILE: {r['name']}, {label}{note}]\n{text}\n[END OF FILE]")
    return "\n\n" + "\n\n".join(parts)


def strip_block(text: str) -> str:
    return _BLOCK.sub("", text or "")


def remove(debate_id: str) -> None:
    """A deleted debate's files go too."""
    for r in db.query("SELECT path FROM attachments WHERE debate_id = ? AND kind != 'link'", [debate_id]):
        shutil.rmtree(os.path.dirname(r["path"]), ignore_errors=True)
