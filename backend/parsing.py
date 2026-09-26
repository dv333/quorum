"""Parsing of model output: thinking blocks, stance footers, and peer-vote rankings."""

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

STANCES = ("AGREE", "DISAGREE", "REFINE")

_STANCE_RE = re.compile(
    r"^[\s>*_`#-]*STANCE\s*[:：]\s*[*_`]*\s*(AGREE|DISAGREE|REFINE)\b", re.IGNORECASE | re.MULTILINE
)
_POSITION_RE = re.compile(r"^[\s>*_`#-]*POSITION\s*[:：]\s*[*_`]*\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_THINK_BLOCK_RE = re.compile(r"<think>.*?(</think>|$)", re.DOTALL | re.IGNORECASE)


@dataclass
class Stance:
    stance: str  # AGREE / DISAGREE / REFINE
    position: str
    parsed: bool  # False when the model omitted or garbled the footer
    body: str  # content with the footer removed


def parse_stance(content: str) -> Stance:
    """Extract the STANCE/POSITION footer. Missing stance falls back to REFINE (flagged unparsed)."""
    matches = list(_STANCE_RE.finditer(content))
    if not matches:
        return Stance("REFINE", "", False, content.strip())

    m = matches[-1]
    stance = m.group(1).upper()
    tail = content[m.start() :]
    pos = _POSITION_RE.search(tail)
    position = pos.group(1).strip().strip("*_` ") if pos else ""
    body = content[: m.start()].rstrip()
    # Drop a trailing horizontal rule the model may have put above the footer
    body = re.sub(r"\n\s*(-{3,}|\*{3,}|_{3,})\s*$", "", body).rstrip()
    return Stance(stance, position, True, body)


def strip_thinking(text: str) -> str:
    """Remove <think>...</think> blocks (including an unterminated trailing one)."""
    return _THINK_BLOCK_RE.sub("", text).strip()


class ThinkSplitter:
    """Incrementally splits a token stream into (content, thinking) around <think> tags.

    Handles tags split across chunk boundaries by holding back a possible partial tag.
    """

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self.in_think = False
        self._buf = ""

    def feed(self, text: str) -> Tuple[str, str]:
        self._buf += text
        content, thinking = [], []
        while self._buf:
            tag = self.CLOSE if self.in_think else self.OPEN
            idx = self._buf.find(tag)
            if idx >= 0:
                (thinking if self.in_think else content).append(self._buf[:idx])
                self._buf = self._buf[idx + len(tag) :]
                self.in_think = not self.in_think
                continue
            # Keep back the longest suffix that could be the start of the tag
            keep = 0
            for k in range(min(len(tag) - 1, len(self._buf)), 0, -1):
                if tag.startswith(self._buf[-k:]):
                    keep = k
                    break
            emit, self._buf = self._buf[: len(self._buf) - keep], self._buf[len(self._buf) - keep :]
            (thinking if self.in_think else content).append(emit)
            break
        return "".join(content), "".join(thinking)

    def flush(self) -> Tuple[str, str]:
        rest, self._buf = self._buf, ""
        return ("", rest) if self.in_think else (rest, "")


_MENTION = r"@(?:Beagle|Researcher)"
_RESEARCH_RE = re.compile(
    rf"(?:^|\n)[ \t>*_-]*{_MENTION}\b[*_]*[ \t]*[:,\-–—]?[ \t]*(?P<line>[^\n]+)"  # mention starting a line
    rf"|{_MENTION}[*_]*[ \t]*:[ \t]*(?P<inline>[^\n]+)",  # or "@Beagle: ..." inline
    re.IGNORECASE,
)
MENTION_RE = re.compile(_MENTION + r"[:,]?", re.IGNORECASE)
_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)


def parse_research_requests(text: str, limit: int = 1) -> List[str]:
    """Extract '@Beagle: <question>' (or legacy '@Researcher:') requests, outside code blocks."""
    text = _CODE_BLOCK_RE.sub("", text)
    out: List[str] = []
    for m in _RESEARCH_RE.finditer(text):
        req = (m.group("line") or m.group("inline") or "").strip().strip("*_` ")
        if len(req) >= 8 and req not in out:
            out.append(req)
        if len(out) >= limit:
            break
    return out


_STR = r'"((?:[^"\\]|\\.)*)"'


def parse_json_loose(text: str) -> Dict[str, Any]:
    """Parse a model's JSON object, recovering what it can from almost-JSON.

    Small models often emit a stray quote or a missing comma; rather than lose the whole reply,
    fall back to pulling out simple "key": "string" and "key": ["strings"] pairs.
    """
    text = strip_thinking(text)
    start = text.find("{")
    if start < 0:
        return {}
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
        if isinstance(obj, dict):
            return obj
    except ValueError:
        pass
    out: Dict[str, Any] = {}
    for key, body in re.findall(r'"(\w+)"\s*:\s*\[(.*?)\]', text, re.DOTALL):
        out[key] = [json.loads(f'"{m}"') if "\\" in m else m for m in re.findall(_STR, body)]
    for key, val in re.findall(r'"(\w+)"\s*:\s*' + _STR, text):
        out.setdefault(key, val)
    return out


_STATUS_PLAIN = [
    (re.compile(r"\*{0,2}[\[(]\s*PARTLY SUPPORTED\s*[\])]\*{0,2}"), "(only partly confirmed by the sources)"),
    (re.compile(r"\*{0,2}[\[(]\s*(UNVERIFIED|UNKNOWN)\s*[\])]\*{0,2}"), "(not confirmed by the sources)"),
    (re.compile(r"\*{0,2}[\[(]\s*CONTRADICTED\s*[\])]\*{0,2}"), "(contradicted by the sources)"),
    (re.compile(r"\s*\*{0,2}[\[(]\s*SUPPORTED\s*[\])]\*{0,2}"), ""),
    (re.compile(r"\s*\((?:Evidence|Item|Claim|Ledger item)\s*\[?\d+\]?\)", re.I), ""),
    (re.compile(r"\b(in|from) the (?:evidence )?ledger\b", re.I), r"\1 the sources"),
    (re.compile(r"\bthe (?:evidence )?ledger\b", re.I), "the evidence"),
    (re.compile(r"\*{0,2}\bunverified\b\*{0,2} by the provided (?:evidence|research|sources)", re.I), "not established"),
    (re.compile(r"\bthe provided sources\b", re.I), "the sources"),
    (re.compile(r"\bthe provided (?:evidence|research(?: findings)?)\b", re.I), "the evidence"),
    (re.compile(r"\bthe research findings\b", re.I), "the studies"),
    (re.compile(r"\b(?:it )?remains unverified\b", re.I), lambda m: m.group(0)[: -len("remains unverified")] + "isn't established"),
    (re.compile(r"\bunverified\b", re.I), "unconfirmed"),
]


def plain_answer(text: str) -> str:
    """Turn leftover evidence-ledger jargon in an answer into plain words (models sometimes echo the labels)."""
    for pattern, replacement in _STATUS_PLAIN:
        text = pattern.sub(replacement, text)
    return re.sub(r"[ \t]{2,}", " ", text)
