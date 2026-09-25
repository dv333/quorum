"""Markdown export of a conundrum: the answer (at any reading level), its sources and, optionally, the whole debate."""

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from . import db
from .config import APP_NAME, RESEARCHER_NAME

# Same emoji as the UI (frontend/src/agents.js); keep in sync with config.HANDLES
EMOJI = {
    "Otter": "🦦",
    "Panda": "🐼",
    "Koala": "🐨",
    "Penguin": "🐧",
    "Hedgehog": "🦔",
    "Bunny": "🐰",
    "Turtle": "🐢",
    "Dolphin": "🐬",
    RESEARCHER_NAME: "🐶",
}
LEVELS = ("simple", "standard", "expert")
REASONS = {
    "consensus": "all agreed in round {rounds}",
    "max_rounds": "ended at the round limit ({rounds} rounds)",
    "manual": "ended by you after {rounds} rounds",
    "direct": "answered directly by the chair",
}


def _date(iso: Optional[str]) -> str:
    try:
        dt = datetime.fromisoformat(iso).astimezone()
    except (TypeError, ValueError):
        return ""
    return f"{dt:%b} {dt.day}, {dt.year}"


def _quote(text: str) -> str:
    return "\n".join(f"> {line}" if line.strip() else ">" for line in text.strip().splitlines())


def _sources(sources: List[Dict[str, Any]]) -> str:
    lines = []
    for i, s in enumerate(sources, 1):
        title = (s.get("title") or s.get("url") or "source").replace("[", "(").replace("]", ")")
        lines.append(f"{i}. [{title}]({s['url']})" if s.get("url") else f"{i}. {title}")
    return "\n".join(lines)


BOTTOM_LINE = re.compile(r"^\s*[*_#]*\s*BOTTOM\s*LINE\s*[*_]*\s*[:：]\s*(.+)$", re.IGNORECASE | re.MULTILINE)


def _readable(answer: str) -> str:
    """The chair writes "BOTTOM LINE: **...**" for the UI to parse; in a document, make it a bold lead."""
    m = BOTTOM_LINE.search(answer)
    if not m:
        return answer
    lead = m.group(1).replace("**", "").strip().strip("_").strip()
    return answer.replace(m.group(0), f"**Bottom line: {lead}**", 1)


CLAIM_LABEL = {
    "supported": "Supported",
    "partly": "Partly supported",
    "contradicted": "Contradicted",
    "unknown": "Unverified",
}


def _ledger(claims: List[Dict[str, Any]]) -> str:
    lines = []
    for c in claims:
        line = f"- **{CLAIM_LABEL.get(c['status'], 'Unverified')}**: {c['claim']}"
        if c.get("caveat"):
            line += f" {c['caveat'].rstrip('.')}."
        if c.get("quote"):
            source = f"[{c.get('source_title') or c['source_url']}]({c['source_url']})" if c.get("source_url") else ""
            line += f" “{c['quote']}” {source}".rstrip()
        lines.append(line)
    return "\n".join(lines)


def _answer(verdict: Dict[str, Any], message: Dict[str, Any], level: str) -> Tuple[str, str]:
    """The answer at the requested reading level, or the standard one if that version was never written."""
    if level != "standard":
        row = db.query_one(
            "SELECT content FROM verdict_levels WHERE verdict_id = ? AND level = ?", [verdict["id"], level]
        )
        if row:
            return _readable(row["content"]), level
    return _readable(message["content"]), "standard"


def _speaker(m: Dict[str, Any], handles: Dict[int, str], models: Dict[int, str]) -> str:
    kind = m["author_kind"]
    if kind == "seat":
        h = handles.get(m["seat_id"], "Agent")
        stance = f" · {m['stance']}" if m.get("stance") else ""
        role = (m.get("meta") or {}).get("role")
        return f"**{EMOJI.get(h, '')} {h}**{f', {role}' if role else ''} ({models.get(m['seat_id'], '')}){stance}"
    if kind == "researcher":
        what = {"brief": "opening brief", "factcheck": "fact-check"}.get(m.get("research_kind"), "lookup")
        asked = f", asked by {m['requested_by']}" if m.get("requested_by") else ""
        return f"**{EMOJI[RESEARCHER_NAME]} {RESEARCHER_NAME}** ({what}{asked})"
    if kind == "moderator":
        return "**Chair**"
    return "**You**"


def _transcript(topic_msgs: List[Dict[str, Any]], verdict_msg_id: Optional[int], handles, models) -> List[str]:
    out: List[str] = []
    current_round = None
    for m in topic_msgs:
        if m["id"] == verdict_msg_id or m["author_kind"] in ("system", "chair") or m.get("status") == "error":
            continue
        body = (m.get("body") or "").strip()
        if not body:
            continue
        if m["round"] != current_round:
            current_round = m["round"]
            out.append("### Before the debate" if current_round == 0 else f"### Round {current_round}")
        text = body
        if m["author_kind"] == "moderator" and (m.get("meta") or {}).get("kind") == "summary":
            assumptions = "\n".join(f"- {a}" for a in (m["meta"].get("assumptions") or []))
            text = f"{body}\n\n{assumptions}" if assumptions else body
        out.append(f"{_speaker(m, handles, models)}\n\n{text}")
        if m["author_kind"] == "researcher" and m.get("sources"):
            out.append(_sources(m["sources"]))
    return out


def to_markdown(snapshot: Dict[str, Any], *, level: str = "standard", include_debate: bool = False) -> str:
    """Render a debate snapshot (DebateEngine.snapshot()) as a Markdown document."""
    if level not in LEVELS:
        raise ValueError(f"level must be one of {', '.join(LEVELS)}")
    d = snapshot["debate"]
    seats = snapshot["seats"]
    handles = {s["id"]: s["handle"] for s in seats}
    models = {s["id"]: s["model"] for s in seats}
    messages = snapshot["messages"]
    verdicts = {v["topic"]: v for v in snapshot["verdicts"]}
    by_id = {m["id"]: m for m in messages}
    topics = sorted({m["topic"] for m in messages if m["topic"] > 0})

    def question(topic: int) -> str:
        first = next((m for m in messages if m["topic"] == topic and m["author_kind"] == "user"), None)
        return first["content"].strip() if first else ""

    title = d.get("title") or (question(topics[0]).splitlines()[0][:80] if topics else "Conundrum")
    parts = [f"# {title}"]
    council = ", ".join(f"{s['handle']} ({s['model']})" for s in seats)
    meta = [f"Asked {_date(d['created_at'])}", f"council: {council}"]
    if d.get("chair_handle"):
        meta.append(f"chair: {d['chair_handle']}")
    if d.get("pack"):
        meta.append(f"topic pack: {d['pack'].get('name')}")
    parts.append("*" + " · ".join(m for m in meta if m) + "*")

    for i, topic in enumerate(topics):
        if i:
            parts.append("---")
            parts.append("## Follow-up")
        parts.append(_quote(question(topic)))
        v = verdicts.get(topic)
        msg = by_id.get(v["message_id"]) if v else None
        if not v or not msg or msg.get("status") != "done":
            parts.append("*No answer yet.*")
        else:
            outcome = REASONS.get(v["reason"], v["reason"]).format(rounds=v["rounds"])
            text, used = _answer(v, msg, level)
            parts.append(f"*Answer · {outcome}" + (f" · {used} version*" if used != "standard" else "*"))
            parts.append(text.strip())
            ledger = [c for c in snapshot.get("claims", []) if c["topic"] == topic]
            check = [m for m in messages if m["topic"] == topic and m.get("research_kind") == "factcheck"]
            if ledger:
                parts.append("**Evidence checked**")
                parts.append(_ledger(ledger))
            elif check and check[-1].get("sources"):
                parts.append(f"**Sources ({RESEARCHER_NAME}'s fact-check)**")
                parts.append(_sources(check[-1]["sources"]))
        if include_debate:
            topic_msgs = [m for m in messages if m["topic"] == topic]
            transcript = _transcript(topic_msgs[1:], v["message_id"] if v else None, handles, models)
            if transcript:
                parts.append("## How the council got here" if len(topics) == 1 else "### How the council got here")
                parts.extend(transcript)

    parts.append("---")
    parts.append(f"*Exported from {APP_NAME}: a council of AI models running locally.*")
    return "\n\n".join(p for p in parts if p) + "\n"


def filename(snapshot: Dict[str, Any]) -> str:
    title = snapshot["debate"].get("title") or "conundrum"
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "conundrum"
    return f"{slug}.md"
