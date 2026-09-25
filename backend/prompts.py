"""Prompt builders for debate turns, rolling summaries, peer votes and the chair's verdict."""

from datetime import date
from typing import Dict, List, Optional

from .config import RESEARCHER_NAME

WORD_CAP = 250


def today() -> str:
    """Local models don't know the date; every prompt that touches time-sensitive facts gets it."""
    return date.today().strftime("%B %d, %Y").replace(" 0", " ")


def criteria_text(criteria: List[str], custom_rubric: str) -> str:
    parts = []
    if criteria:
        parts.append(", ".join(criteria))
    if custom_rubric.strip():
        parts.append(f"custom rubric: {custom_rubric.strip()}")
    return "; ".join(parts) or "accuracy and insight"


RESEARCH_HELP = f"""
- {RESEARCHER_NAME}, a researcher with live web search, is in the channel. Your training data is older than today, so for anything time-sensitive, trust {RESEARCHER_NAME}'s briefs over your own memory (e.g. if a brief says a newer version exists, it exists). If the answer depends on recent or checkable facts (versions, prices, releases, news, statistics), don't guess: put a line in your message like
  @{RESEARCHER_NAME}: <one specific question>
  {RESEARCHER_NAME} will post a sourced brief before the next agent speaks. Ask at most one question per message, and don't re-ask what a brief already answered. Cite briefs as [{RESEARCHER_NAME}]."""


def agent_system_prompt(
    handle: str, others: List[str], criteria: List[str], custom_rubric: str, research: bool = False
) -> str:
    return f"""Today is {today()}. You are {handle}, one member of a council of AI agents working together to give the user the best possible answer, by debating in a group chat.
The other agents are: {", ".join(others)}. The user may also post messages; treat them as guidance from the person you all serve.

How to debate:
- Work toward the best answer, not toward winning. Change your mind when another agent makes a better point, and say so.
- Engage directly: name the agent whose point you're building on or disputing (e.g. "I disagree with {others[0] if others else "Agent B"} because...").
- Don't repeat points that were already made. Add something new: evidence, a counter-example, a refinement, or a concrete recommendation.
- Check the numbers against the user's situation. If something doesn't add up (for example, a purchase their income can't support), say so plainly and adjust the advice.
- Keep it under {WORD_CAP} words. Use markdown only when it helps (short lists, code).
- The user cares most about: {criteria_text(criteria, custom_rubric)}.
- If the user addresses you by name (for example @{handle}), answer them directly first.
- Speak only as yourself. Never write messages for other agents, the user or {RESEARCHER_NAME}.{RESEARCH_HELP if research else ""}

End EVERY message with exactly these two lines:
STANCE: AGREE | DISAGREE | REFINE
POSITION: <your current answer in one sentence>

The stance is about the answer the group is converging on, not about any single message:
- AGREE: you'd sign off on the group's emerging answer as-is (even if you quibble with how someone argued it).
- REFINE: the direction is right but it needs the specific change you just proposed.
- DISAGREE: you think the group's emerging answer is wrong."""


def _speaker(msg: Dict, handles: Dict[int, str], me: Optional[int]) -> str:
    if msg["author_kind"] == "researcher":
        asked = f", asked by {msg['requested_by']}" if msg.get("requested_by") else ""
        return f"{RESEARCHER_NAME} (web research{asked})"
    if msg["author_kind"] == "user":
        return "User"
    if msg["author_kind"] == "seat":
        name = handles.get(msg["seat_id"], "Agent ?")
        return f"{name} (you)" if msg["seat_id"] == me else name
    return "Chair"


def render_transcript(messages: List[Dict], handles: Dict[int, str], me: Optional[int] = None) -> str:
    """Render messages grouped by round. Thinking is never included."""
    lines: List[str] = []
    current_round = None
    for msg in messages:
        if msg["author_kind"] == "system" or msg.get("status") == "error":
            continue
        if msg["round"] != current_round:
            current_round = msg["round"]
            lines.append(f"\n--- Round {current_round} ---" if current_round else "")
        text = msg["content"].strip()
        if msg["author_kind"] == "researcher":
            text += sources_block(msg.get("sources") or [])
        lines.append(f"{_speaker(msg, handles, me)}: {text}")
    return "\n\n".join(line for line in lines if line is not None).strip()


def sources_block(sources: List[Dict]) -> str:
    if not sources:
        return ""
    return "\nSources: " + "; ".join(f"[{i + 1}] {src['title']} ({src['url']})" for i, src in enumerate(sources))


def history_block(prior_topics: List[Dict[str, str]]) -> str:
    if not prior_topics:
        return ""
    parts = ["Earlier in this channel:"]
    for t in prior_topics:
        parts.append(f"Q: {t['question']}\nCouncil verdict: {t['verdict']}")
    return "\n\n".join(parts) + "\n\n"


def turn_messages(
    *,
    handle: str,
    others: List[str],
    criteria: List[str],
    custom_rubric: str,
    question: str,
    prior_topics: List[Dict[str, str]],
    summary: Optional[str],
    summary_upto: int,
    transcript: str,
    round_no: int,
    max_rounds: int,
    research: bool = False,
) -> List[Dict[str, str]]:
    user = [history_block(prior_topics), f"THE QUESTION:\n{question}\n"]
    if summary:
        user.append(f"\nSUMMARY OF ROUNDS 1-{summary_upto} (written by the chair):\n{summary}\n")
    if transcript:
        user.append(f"\nDISCUSSION SO FAR:\n{transcript}\n")
    else:
        user.append("\nNobody has spoken yet. Give your initial answer.\n")
    last = " This is the final round, so make your position clear." if round_no >= max_rounds else ""
    user.append(
        f"\nIt's your turn, {handle} (round {round_no} of at most {max_rounds}).{last} "
        f"Reply with your message only, ending with the STANCE and POSITION lines."
    )
    return [
        {"role": "system", "content": agent_system_prompt(handle, others, criteria, custom_rubric, research)},
        {"role": "user", "content": "".join(user)},
    ]


def summary_messages(question: str, previous_summary: Optional[str], transcript: str) -> List[Dict[str, str]]:
    prev = f"Existing summary of earlier rounds:\n{previous_summary}\n\n" if previous_summary else ""
    return [
        {
            "role": "system",
            "content": "You are the neutral chair of an AI council debate. You write compact, faithful summaries.",
        },
        {
            "role": "user",
            "content": f"""Question under debate: {question}

{prev}New rounds to fold into the summary:
{transcript}

Write an updated summary in at most 200 words. For each agent (by handle), note their current position and key arguments; note any user guidance; list points of agreement and open disagreements. No preamble.""",
        },
    ]


def verdict_messages(
    *,
    question: str,
    prior_topics: List[Dict[str, str]],
    summary: Optional[str],
    transcript: str,
    positions: List[Dict[str, str]],
    fact_check: Optional[str],
    reason: str,
    criteria: List[str],
    custom_rubric: str,
) -> List[Dict[str, str]]:
    pos = "\n".join(f"- {p['handle']}: {p['stance']} — {p['position']}" for p in positions)
    why = {
        "consensus": "The agents reached consensus.",
        "max_rounds": "The round limit was reached without full consensus.",
        "manual": "The user ended the debate.",
    }.get(reason, "")
    summ = f"\nSummary of earlier rounds:\n{summary}\n" if summary else ""
    check = (
        f"\nWeb fact-check of key claims (trust this over the agents where they conflict):\n{fact_check}\n"
        if fact_check
        else ""
    )
    return [
        {
            "role": "system",
            "content": "You are the chair of an AI council. You turn the council's debate into the best possible final answer for the user.",
        },
        {
            "role": "user",
            "content": f"""Today is {today()}.
{history_block(prior_topics)}Question: {question}
{summ}
Latest discussion:
{transcript}

Final positions:
{pos}
{check}
{why}
The user cares most about: {criteria_text(criteria, custom_rubric)}.

Write the final answer in markdown. Combine the strongest arguments from all agents; don't just pick one agent's answer. Correct anything the fact-check or research briefs contradicted; for time-sensitive facts, the web sources beat the agents' memory.

{ANSWER_FORMAT}""",
        },
    ]


# ---------------------------------------------------------------------------
# Researcher
# ---------------------------------------------------------------------------

ANSWER_FORMAT = """Use exactly this structure, with no preamble:

BOTTOM LINE: <one sentence a busy person could act on, wrapping the key recommendation in **bold**>

## Key points
- 3 to 5 bullets. Each starts with a short **bold phrase**, then one plain sentence. Keep [n] citations from research where you rely on them.

## Diagram
Include this section only if a picture genuinely helps (a decision, a process, a comparison or a timeline). Write one small Mermaid diagram in a ```mermaid code block: a "flowchart TD" or "flowchart LR" with at most 8 nodes and no styling, every node written as an id with a quoted label, like A["Check budget"] --> B["Buy"]. Otherwise leave this section out entirely.

## Where they differed
One or two sentences naming the agents who disagreed and why, or "Nothing significant."

## Details
A short paragraph with anything else the user needs: caveats, conditions, next steps."""


def pick_roles_messages(question: str, members: List[Dict[str, str]]) -> List[Dict[str, str]]:
    roster = "\n".join(f"- {m['handle']}: {m['description']}" for m in members)
    names = ", ".join(m["handle"] for m in members)
    return [
        {"role": "system", "content": "You organize an AI council. Reply with a single JSON object and nothing else."},
        {
            "role": "user",
            "content": f"""Today is {today()}. The council will debate this question:
{question}

Members:
{roster}

Choose, from {names}:
- "chair": the member best suited to weigh the debate and write the final answer for this question (strong reasoning and clear writing; larger models are usually better).
- "researcher": the member best suited to plan web searches and summarize sources faithfully.
- "reason": why the chair fits, in at most 8 words.
- "title": a 3 to 6 word title for the question.

Reply like: {{"chair": "{members[0]["handle"]}", "researcher": "{members[-1]["handle"]}", "reason": "...", "title": "..."}}""",
        },
    ]


ROUNDS_GUIDE = (
    '"rounds": how many debate rounds this deserves, 1 to 10: 1 for a straightforward question, 2-3 for a typical '
    "decision, 4-6 for complex trade-offs, more only for hard multi-part problems"
)


def intake_messages(
    chair: str, question: str, history: List[Dict[str, str]], asked: int, max_questions: int, must_summarize: bool
) -> List[Dict[str, str]]:
    """The chair sizes up the conundrum before the council debates it."""
    convo = "\n".join(f"{'You' if h['who'] == 'chair' else 'User'}: {h['text']}" for h in history) or "(nothing yet)"
    if must_summarize:
        task = 'You must now summarize: reply {"action": "summarize", ...}.'
    else:
        task = f"""Decide what to do next. You have asked {asked} of at most {max_questions} questions.
- If it's trivial (a greeting, small talk, a simple fact, a quick calculation), don't convene the council: reply {{"action": "direct"}} (only when you haven't asked anything yet).
- If it can already be answered well (the goal is clear, or sensible defaults exist), reply {{"action": "clear", "rounds": N}} when you haven't asked anything yet, otherwise summarize.
- Ask only when the best answer genuinely depends on something only the user knows (their goal, budget, scale, audience, constraints, what they already tried). Never ask about things you can reasonably assume, look up, or that barely change the answer. Never ask two things at once.
- To ask: {{"action": "ask", "question": "<one short, friendly question>", "options": ["2 to 4 short likely answers"]}}
- Make the suggested answers realistic for the user's situation, place and time (for example, typical local prices today), so that any of them could be the user's true answer."""
    return [
        {
            "role": "system",
            "content": f"You are {chair}, the moderator of an AI council. Before the council debates a user's conundrum, you make sure it's clear enough to answer well, without pestering the user, and decide how much debate it needs. Reply with a single JSON object and nothing else.",
        },
        {
            "role": "user",
            "content": f"""Today is {today()}.
The user's conundrum: {question}

Conversation with the user so far:
{convo}

{task}
- To summarize: {{"action": "summarize", "brief": "<the conundrum restated precisely, 1-2 sentences>", "assumptions": ["3 to 5 short assumptions the council will work from, including what the user told you"], "rounds": N}}
Where {ROUNDS_GUIDE}.""",
        },
    ]


def direct_answer_messages(chair: str, question: str, prior_topics: List[Dict[str, str]]) -> List[Dict[str, str]]:
    return [
        {
            "role": "system",
            "content": f"You are {chair}, the chair of an AI council. This message is simple enough that the council doesn't need to debate it, so you answer it yourself.",
        },
        {
            "role": "user",
            "content": f"""Today is {today()}.
{history_block(prior_topics)}The user says: {question}

Reply directly, briefly and warmly in markdown. If it's a greeting or small talk, reply in kind and invite them to bring a real conundrum for the council. If it's a factual question, give the answer in a sentence or two.""",
        },
    ]


LEVEL_GUIDE = {
    "simple": "for someone with no background in the topic, like a curious 12-year-old: short sentences, everyday words, no jargon (or explain it in plain words), and one concrete analogy if it helps.",
    "expert": "for a domain expert: precise terminology, concrete specifics and numbers, trade-offs, edge cases and conditions under which the recommendation changes.",
}


DIAGRAM_FOR_EXPERTS = (
    " If the answer has no diagram and one would help (a decision, process, comparison or timeline), add a"
    ' "## Diagram" section with one small Mermaid flowchart in a ```mermaid block: "flowchart TD" or'
    ' "flowchart LR", at most 8 nodes, no styling, and every node written as an id with a quoted label,'
    ' like A["Check budget"] --> B["Buy"].'
)


def level_messages(question: str, answer: str, level: str) -> List[Dict[str, str]]:
    return [
        {
            "role": "system",
            "content": "You rewrite an AI council's final answer for a different audience without changing its conclusions.",
        },
        {
            "role": "user",
            "content": f"""Question: {question}

Final answer:
{answer}

Rewrite the final answer {LEVEL_GUIDE[level]}
Keep the same conclusions, facts and [n] citations. Keep exactly the same structure (BOTTOM LINE line, then the ## sections), and copy any ```mermaid block unchanged.{DIAGRAM_FOR_EXPERTS if level == "expert" else ""} Output only the rewritten answer.""",
        },
    ]


def research_plan_messages(request: str, question: str, max_queries: int) -> List[Dict[str, str]]:
    need = (
        "Find the current facts needed to answer this question well."
        if request == question
        else f"Information needed: {request}"
    )
    return [
        {
            "role": "system",
            "content": "You plan web searches. Output only search queries, one per line, with no numbering, quotes or commentary.",
        },
        {
            "role": "user",
            "content": f"""Today is {today()}. A council is debating: {question}

{need}

Write 1 to {max_queries} short web search queries (like you'd type into a search engine) that will find current, authoritative sources for this. Prefer one query unless the request clearly has several parts. Don't put years in queries unless the request is about a specific year; if you must, use the current year.""",
        },
    ]


def factcheck_plan_messages(question: str, positions: List[Dict[str, str]], max_queries: int) -> List[Dict[str, str]]:
    pos = "\n\n".join(f"{p['handle']}: {p['position']}\n{p['body'][:800]}" for p in positions)
    return [
        {
            "role": "system",
            "content": "You plan web searches. Output only search queries, one per line, with no numbering, quotes or commentary.",
        },
        {
            "role": "user",
            "content": f"""Today is {today()}. Question: {question}

Final positions from the debate:
{pos}

Pick up to {max_queries} specific factual claims the final answer will rely on that could be wrong or out of date (versions, numbers, dates, product capabilities, recent events). For each, write one web search query that would verify it (no years unless the claim is about a specific year). If nothing needs checking, output NONE.""",
        },
    ]


def research_brief_messages(
    request: str, requested_by: Optional[str], sources: List[Dict[str, str]], kind: str
) -> List[Dict[str, str]]:
    blocks = "\n\n".join(
        f"[{i + 1}] {src['title']} ({src['url']})\n{src['description']}\n{src['content']}"
        for i, src in enumerate(sources)
    )
    if kind == "factcheck":
        task = """Fact-check the claims implied by the request below against the sources. For each claim write one line:
- **Supported** / **Contradicted** / **Unclear**: the claim, then the correct fact with [n] citations.
At most 5 lines. Don't add anything the sources don't support."""
    else:
        task = """Write a brief (at most 150 words) that answers the request using only the sources. Cite every fact with [n]. Lead with the direct answer, and when the request is about versions or releases, state the newest one explicitly. Mention dates when recency matters. If the sources don't answer it, say so plainly instead of guessing."""
    who = f" (asked by {requested_by})" if requested_by else ""
    return [
        {
            "role": "system",
            "content": f"You are {RESEARCHER_NAME}, the researcher in an AI council's group chat. You have web search. You report what sources say, neutrally and precisely, and never invent facts or citations. You don't take sides in the debate.",
        },
        {
            "role": "user",
            "content": f"""Today is {today()}. Request{who}: {request}

Sources found:
{blocks}

{task}
Output only the final brief: no drafts, revisions, or notes about these instructions. The source list is added automatically.""",
        },
    ]
