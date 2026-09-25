"""Quorum's health report: numbers from your own recent conundrums, and plain-language findings.

Everything here is measured from the local database (no model calls), so findings are evidence, not opinions.
`quorum doctor` prints it; `quorum doctor --ask` hands it to the council for an improvement plan.
"""

import statistics
from datetime import datetime
from typing import Any, Dict, List, Optional

from . import db


def _minutes(start: str, end: str) -> Optional[float]:
    try:
        return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 60
    except (TypeError, ValueError):
        return None


def _pct(part: int, whole: int) -> Optional[float]:
    return round(100 * part / whole, 1) if whole else None


def report(last: int = 20) -> Dict[str, Any]:
    debates = db.query("SELECT * FROM debates ORDER BY created_at DESC LIMIT ?", [max(1, min(last, 500))])
    ids = [d["id"] for d in debates]
    if not ids:
        return {"conundrums": 0, "findings": ["No conundrums yet: ask something first."]}
    marks = ",".join("?" * len(ids))
    verdicts = db.query(f"SELECT * FROM verdicts WHERE debate_id IN ({marks}) ORDER BY id", ids)
    messages = db.query(
        f"SELECT id, debate_id, topic, round, author_kind, seat_id, stance, stance_parsed, status, research_kind, "
        f"content, created_at FROM messages WHERE debate_id IN ({marks})",
        ids,
    )
    seats = {s["id"]: s for s in db.query(f"SELECT * FROM seats WHERE debate_id IN ({marks})", ids)}
    usage = db.query(f"SELECT * FROM usage WHERE debate_id IN ({marks})", ids)
    drafts = db.query(f"SELECT debate_id, topic, round FROM drafts WHERE debate_id IN ({marks})", ids)
    claims = db.query(f"SELECT status FROM claims WHERE debate_id IN ({marks})", ids)
    corrected = sum(
        '"revised": true' in (m.get("meta_json") or "")
        for m in db.query(f"SELECT meta_json FROM messages WHERE author_kind = 'chair' AND debate_id IN ({marks})", ids)
    )
    created = {d["id"]: d["created_at"] for d in debates}

    # --- outcomes and time
    debated = [v for v in verdicts if v["reason"] != "direct"]
    reasons: Dict[str, int] = {}
    for v in verdicts:
        reasons[v["reason"]] = reasons.get(v["reason"], 0) + 1
    first_answers = {}
    for v in verdicts:
        if v["topic"] == 1 and v["reason"] != "direct":
            first_answers.setdefault(v["debate_id"], v)
    waits = [m for v in first_answers.values() if (m := _minutes(created[v["debate_id"]], v["created_at"])) is not None]
    unfinished = sum(d["status"] not in ("concluded", "idle") for d in debates)

    # --- turns and stances
    turns = [m for m in messages if m["author_kind"] == "seat" and m["status"] == "done"]
    stances = {s: sum(m["stance"] == s for m in turns) for s in ("AGREE", "REFINE", "DISAGREE")}
    unparsed = sum(not m["stance_parsed"] for m in turns)
    round1 = [m for m in turns if m["round"] == 1]
    failures = [m for m in messages if m["author_kind"] == "system" and m["status"] == "error"]

    # --- per model
    models: Dict[str, Dict[str, Any]] = {}
    for u in usage:
        if u["kind"] == "search":
            continue
        m = models.setdefault(u["model"], {"model": u["model"], "calls": 0, "seconds": 0.0, "output_tokens": 0})
        m["calls"] += 1
        m["seconds"] += u["duration_ms"] / 1000
        m["output_tokens"] += u["output_tokens"]
    for m in turns:
        seat = seats.get(m["seat_id"])
        if seat and seat["model"] in models:
            e = models[seat["model"]]
            e["turns"] = e.get("turns", 0) + 1
            e["unparsed"] = e.get("unparsed", 0) + (not m["stance_parsed"])
    for f in failures:
        seat = seats.get(f["seat_id"])
        if seat and seat["model"] in models:
            models[seat["model"]]["failures"] = models[seat["model"]].get("failures", 0) + 1
    per_model = []
    for m in models.values():
        per_model.append(
            {
                "model": m["model"],
                "calls": m["calls"],
                "avg_seconds": round(m["seconds"] / m["calls"], 1) if m["calls"] else None,
                "tokens_per_second": round(m["output_tokens"] / m["seconds"], 1) if m["seconds"] else None,
                "turns": m.get("turns", 0),
                "unparsed_stance_pct": _pct(m.get("unparsed", 0), m.get("turns", 0)),
                "failures": m.get("failures", 0),
            }
        )
    per_model.sort(key=lambda m: m["avg_seconds"] or 0, reverse=True)

    # --- research and chair
    research = [m for m in messages if m["author_kind"] == "researcher"]
    searches = sum(u["searches"] for u in usage)
    chair_errors = sum(m["author_kind"] == "chair" and m["status"] == "error" for m in messages)
    rounds_between = sum(max(0, v["rounds"] - 1) for v in debated)

    out: Dict[str, Any] = {
        "conundrums": len(debates),
        "answers": len(verdicts),
        "outcomes": reasons,
        "unfinished": unfinished,
        "minutes_to_first_answer": {
            "median": round(statistics.median(waits), 1) if waits else None,
            "longest": round(max(waits), 1) if waits else None,
        },
        "turns": len(turns),
        "stances_pct": {s.lower(): _pct(n, len(turns)) for s, n in stances.items()},
        "round1_agree_pct": _pct(sum(m["stance"] == "AGREE" for m in round1), len(round1)),
        "unparsed_stance_pct": _pct(unparsed, len(turns)),
        "failed_turns": len(failures),
        "models": per_model,
        "research": {
            "briefs": len(research),
            "failed": sum(m["status"] == "error" for m in research),
            "searches": searches,
        },
        "chair_answer_errors": chair_errors,
        "drafts": {"written": len(drafts), "rounds_that_could_have_one": rounds_between},
        "evidence": {
            "claims": len(claims),
            **{s: sum(c["status"] == s for c in claims) for s in ("supported", "partly", "contradicted", "unknown")},
            "answers_corrected": corrected,
        },
    }
    out["findings"] = findings(out)
    return out


def findings(r: Dict[str, Any]) -> List[str]:
    """Plain-language flags for anything that looks wrong, most important first."""
    out: List[str] = []
    s = r.get("stances_pct") or {}
    if (s.get("disagree") or 0) < 5 and r.get("turns", 0) >= 20:
        out.append(
            f"Agents rarely disagree: {s.get('disagree')}% of turns are DISAGREE. "
            "Debates may be rubber-stamping the first answer."
        )
    if (r.get("round1_agree_pct") or 0) >= 25:
        out.append(
            f"{r['round1_agree_pct']}% of round-1 turns already AGREE, before most agents have heard the others."
        )
    wait = (r.get("minutes_to_first_answer") or {}).get("median")
    if wait and wait >= 10:
        out.append(f"A typical answer takes {wait} minutes (longest {r['minutes_to_first_answer']['longest']}).")
    if r.get("failed_turns"):
        worst = max(r["models"], key=lambda m: m.get("failures", 0), default=None)
        who = f", most from {worst['model']} ({worst['failures']})" if worst and worst.get("failures") else ""
        out.append(f"{r['failed_turns']} agent turns failed{who}.")
    for m in r.get("models", []):
        if (m.get("unparsed_stance_pct") or 0) >= 10 and m["turns"] >= 5:
            out.append(
                f"{m['model']} skipped the STANCE/POSITION lines in {m['unparsed_stance_pct']}% of its turns, "
                "so its position is guessed."
            )
    timed = [m for m in r.get("models", []) if m.get("avg_seconds") and m["calls"] >= 3]
    if len(timed) >= 3:
        typical = statistics.median(m["avg_seconds"] for m in timed)
        slow = [m for m in timed if m["avg_seconds"] >= 2.5 * typical]
        for m in slow[:3]:
            out.append(
                f"{m['model']} averages {m['avg_seconds']}s per call, {m['avg_seconds'] / typical:.1f}× the council's "
                f"typical {typical:.0f}s, so it slows every round. Consider leaving it out of the council."
            )
    research = r.get("research") or {}
    if research.get("failed"):
        out.append(f"{research['failed']} of {research['briefs']} research lookups failed.")
    ev = r.get("evidence") or {}
    if ev.get("claims", 0) >= 5 and (ev.get("contradicted", 0) + ev.get("unknown", 0)) / ev["claims"] >= 0.4:
        out.append(
            f"{ev['contradicted'] + ev['unknown']} of {ev['claims']} checked claims were contradicted or unverified: "
            "the debates lean on facts the sources don't back."
        )
    if r.get("chair_answer_errors"):
        out.append(f"The chair failed to write {r['chair_answer_errors']} answer(s).")
    if r.get("unfinished"):
        out.append(f"{r['unfinished']} conundrum(s) were left unfinished (paused or interrupted).")
    if not out:
        out.append("Nothing stands out: debates finish, models answer in format, and agents disagree when they should.")
    return out


def as_text(r: Dict[str, Any]) -> str:
    """The report as plain text, for the terminal and for the council to read."""
    if not r.get("turns"):
        return "\n".join(r.get("findings", []))
    s = r["stances_pct"]
    lines = [
        f"Last {r['conundrums']} conundrums: {r['answers']} answers "
        + "("
        + ", ".join(f"{k} {v}" for k, v in sorted(r["outcomes"].items()))
        + ")"
        + (f", {r['unfinished']} unfinished" if r["unfinished"] else ""),
        f"Time to first answer: median {r['minutes_to_first_answer']['median']} min, "
        f"longest {r['minutes_to_first_answer']['longest']} min",
        f"Agent turns: {r['turns']} · agree {s['agree']}% · refine {s['refine']}% · disagree {s['disagree']}% · "
        f"round-1 agree {r['round1_agree_pct']}% · missing stance {r['unparsed_stance_pct']}% · "
        f"failed {r['failed_turns']}",
        f"Research: {r['research']['briefs']} briefs, {r['research']['searches']} searches, "
        f"{r['research']['failed']} failed · chair answer errors: {r['chair_answer_errors']} · "
        f"drafts: {r['drafts']['written']}",
        "Evidence: "
        + (
            f"{r['evidence']['claims']} claims checked (supported {r['evidence']['supported']}, partly "
            f"{r['evidence']['partly']}, contradicted {r['evidence']['contradicted']}, unverified "
            f"{r['evidence']['unknown']}) · answers corrected {r['evidence']['answers_corrected']}"
            if r["evidence"]["claims"]
            else "no claims checked yet"
        ),
        "",
        "Models (slowest first):",
    ]
    for m in r["models"]:
        extra = []
        if m["turns"]:
            extra.append(f"{m['turns']} turns, missing stance {m['unparsed_stance_pct']}%")
        if m["failures"]:
            extra.append(f"{m['failures']} failed")
        lines.append(
            f"  {m['model']}: {m['calls']} calls, {m['avg_seconds']}s per call, {m['tokens_per_second']} tok/s"
            + (f" · {'; '.join(extra)}" if extra else "")
        )
    lines += ["", "Findings:"] + [f"  - {f}" for f in r["findings"]]
    return "\n".join(lines)
