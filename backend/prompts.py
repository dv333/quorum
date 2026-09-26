"""Prompt builders for debate turns, rolling summaries, peer votes and the chair's verdict."""

import re
from datetime import date
from typing import Sequence, Any, Dict, List, Optional

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


def guidance_line(guidance: str) -> str:
    """A topic pack's guidance for how the council should approach the conundrum."""
    return f"\n- How to approach this conundrum: {guidance.strip()}" if guidance and guidance.strip() else ""


def role_line(role: Optional[Dict[str, str]]) -> str:
    """The role the chair gave this agent for the current conundrum."""
    if not role or not role.get("role"):
        return ""
    focus = f" {role['focus']}" if role.get("focus") else ""
    line = (
        f"\nYour role in this debate: {role['role']}.{focus} Argue from this perspective, but stay honest: "
        "if the evidence goes against your role's usual view, say so."
    )
    if re.search(r"skeptic|sceptic|critic|devil|contrarian|red team", role["role"], re.I):
        line += (
            " Only AGREE once your strongest objection has been answered with evidence (a source or a checked fact), "
            "not just with more argument or because others agree."
        )
    return line


def agent_system_prompt(
    handle: str,
    others: List[str],
    criteria: List[str],
    custom_rubric: str,
    research: bool = False,
    guidance: str = "",
    role: Optional[Dict[str, str]] = None,
    roster: Optional[List[str]] = None,
) -> str:
    return f"""Today is {today()}. You are {handle}, one member of a council of AI agents working together to give the user the best possible answer, by debating in a group chat.{role_line(role)}
The other agents are: {", ".join(roster or others)}. The user may also post messages; treat them as guidance from the person you all serve.

How to debate:
- Work toward the best answer, not toward winning. Change your mind when another agent makes a better point, and say so.
- Engage directly: name the agent whose point you're building on or disputing (e.g. "I disagree with {others[0] if others else "Agent B"} because...").
- Don't repeat points that were already made. Add something new: evidence, a counter-example, a refinement, or a concrete recommendation.
- Check the numbers against the user's situation. If something doesn't add up (for example, a purchase their income can't support), say so plainly and adjust the advice.
- Keep it under {WORD_CAP} words. Use markdown only when it helps (short lists, code).
- The user cares most about: {criteria_text(criteria, custom_rubric)}.{guidance_line(guidance)}
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
    return "\nSources: " + "; ".join(
        f"[{i + 1}] {src['title']} ({src['url']}){source_label(src)}" for i, src in enumerate(sources)
    )


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
    guidance: str = "",
    role: Optional[Dict[str, str]] = None,
    roster: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    user = [history_block(prior_topics), f"THE QUESTION:\n{question}\n"]
    if summary:
        user.append(f"\nSUMMARY OF ROUNDS 1-{summary_upto} (written by the chair):\n{summary}\n")
    if round_no == 1:
        if transcript:
            user.append(f"\nBEFORE THE DEBATE:\n{transcript}\n")
        user.append(
            "\nThis is round 1. You haven't seen the other agents' views, and that's deliberate: think independently. "
            "Give your own initial recommendation, then the strongest objection to it.\n"
        )
    elif transcript:
        user.append(f"\nDISCUSSION SO FAR:\n{transcript}\n")
    else:
        user.append("\nNobody has spoken yet. Give your initial answer.\n")
    last = " This is the final round, so make your position clear." if round_no >= max_rounds else ""
    user.append(
        f"\nIt's your turn, {handle} (round {round_no} of at most {max_rounds}).{last} "
        f"Reply with your message only, ending with the STANCE and POSITION lines."
    )
    return [
        {
            "role": "system",
            "content": agent_system_prompt(handle, others, criteria, custom_rubric, research, guidance, role, roster),
        },
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

Write an updated summary in at most 230 words. For each agent (by handle), note their current position and key arguments; note any user guidance; list points of agreement. Then, under "Open:", carry forward every unresolved disagreement, every claim that is still unverified or disputed, and any source evidence against the emerging answer, with its caveats; never drop these just because most agents agree. No preamble.""",
        },
    ]


def draft_messages(
    *,
    question: str,
    previous_draft: Optional[str],
    transcript: str,
    positions: List[Dict[str, str]],
    round_no: int,
) -> List[Dict[str, str]]:
    """The chair's running draft of the answer, rewritten after every round so the user sees it improve."""
    pos = "\n".join(f"- {p['handle']}: {p['stance']} — {p['position']}" for p in positions)
    prev = f"Your draft after the previous round:\n{previous_draft}\n\n" if previous_draft else ""
    return [
        {
            "role": "system",
            "content": "You chair an AI council. While the council debates, you keep a short draft of the answer up to date.",
        },
        {
            "role": "user",
            "content": f"""Today is {today()}.
Question: {question}

{prev}Round {round_no}, as it was argued:
{transcript}

Current positions:
{pos}

Write the draft answer as it stands after round {round_no}, in exactly this format and nothing else:
BOTTOM LINE: <one sentence>
- <key point>
- <key point>
- <key point>
CHANGED: <one short sentence: what changed since your previous draft and whose argument changed it, or "First draft." if there is no previous draft>

Keep it under 90 words. Change only what the debate gave you a reason to change.""",
        },
    ]


def why_messages(
    *,
    question: str,
    answer: str,
    passage: str,
    transcript: str,
    positions: List[Dict[str, str]],
    sources: List[Dict[str, Any]],
    handles: List[str],
) -> List[Dict[str, str]]:
    """Trace one passage of the final answer back to the agents and sources behind it."""
    pos = "\n".join(f"- {p['handle']}: {p['stance']} — {p['position']}" for p in positions)
    src = "\n".join(f"[{i}] {s.get('title') or s.get('url')}" for i, s in enumerate(sources, 1)) or "(none)"
    return [
        {
            "role": "system",
            "content": "You trace claims in an AI council's final answer back to the debate. You only report what the debate actually contains.",
        },
        {
            "role": "user",
            "content": f"""Question: {question}

Final answer:
{answer}

Debate (latest rounds):
{transcript}

Final positions:
{pos}

Research sources:
{src}

Passage to trace: "{passage}"

Which agents argued for this passage, which challenged or qualified it, and which research sources support it? Agents are: {", ".join(handles)}. Use only what appears above; leave a list empty rather than guess.
Reply with JSON only:
{{"summary": "<one sentence on where this came from>", "support": [{{"agent": "<name>", "point": "<what they argued, under 20 words>"}}], "challenges": [{{"agent": "<name>", "point": "<their objection, under 20 words>"}}], "sources": [<source numbers>]}}""",
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
    guidance: str = "",
    claims: Optional[List[Dict[str, Any]]] = None,
    research: str = "",
    studies: str = "",
) -> List[Dict[str, str]]:
    pos = "\n".join(f"- {p['handle']}: {p['stance']} — {p['position']}" for p in positions)
    why = {
        "consensus": "The agents reached consensus.",
        "max_rounds": "The round limit was reached without full consensus.",
        "manual": "The user ended the debate.",
    }.get(reason, "")
    summ = f"\nSummary of earlier rounds:\n{summary}\n" if summary else ""
    found = f"\nWhat {RESEARCHER_NAME}'s research found:\n{research}\n" if research else ""
    if studies:
        found += (
            "\nKey studies (checked against their pages; lead with these and use their names, years and numbers "
            f"exactly, without inventing other details):\n{studies}\n"
        )
    if claims:
        check = f"\nEvidence ledger (checked against sources; it overrides the agents):\n{ledger_text(claims)}\n\n{EVIDENCE_RULES}\n"
    elif fact_check:
        check = f"\nWeb fact-check of key claims (trust this over the agents where they conflict):\n{fact_check}\n"
    else:
        check = ""
    return [
        {
            "role": "system",
            "content": "You are the chair of an AI council. You turn the council's debate into the best possible final answer for the user.",
        },
        {
            "role": "user",
            "content": f"""Today is {today()}.
{history_block(prior_topics)}Question: {question}
{summ}{found}
Latest discussion:
{transcript}

Final positions:
{pos}
{check}
{why}
The user cares most about: {criteria_text(criteria, custom_rubric)}.{guidance_line(guidance)}

Write the final answer in markdown. Combine the strongest arguments from all agents; don't just pick one agent's answer, and don't treat how many agents agree as evidence. Correct anything the evidence or research briefs contradicted; for time-sensitive facts, the web sources beat the agents' memory. Keep the strongest dissent and any open uncertainty in "Where they differed", even if only one agent held it. Address every requirement the user stated in the question, even briefly, and say what the evidence shows for each. When the question covers a category with distinct forms (types of a diet, versions of a product, kinds of treatment), say how the main forms compare. Every section must agree with the bottom line: don't lean toward an option in the details or in "Where they differed" more than the evidence and the bottom line do.

{ANSWER_FORMAT}""",
        },
    ]


# ---------------------------------------------------------------------------
# Researcher
# ---------------------------------------------------------------------------

ANSWER_FORMAT = """Use exactly this structure, with no preamble:

BOTTOM LINE: <one sentence a busy person could act on, wrapping the key recommendation in **bold**>

## Key points
- 3 to 5 bullets. Each starts with a short **bold phrase**, then one plain sentence. Name the key studies or sources and use their numbers (effect sizes, how many people or trials, how long, when) where the research gives them, and keep [n] citations where you rely on them.

## Diagram
Include this section only if a picture genuinely helps (a decision, a process, a comparison or a timeline). Write one small Mermaid diagram in a ```mermaid code block: a "flowchart TD" or "flowchart LR" with at most 8 nodes and no styling, every node written as an id with a quoted label, like A["Check budget"] --> B["Buy"]. Otherwise leave this section out entirely.

## Where they differed
One or two sentences naming the agents who disagreed and why, or "Nothing significant."

## Details
A short paragraph with anything else the user needs: caveats, conditions, next steps."""


def assign_roles_messages(
    question: str, members: List[Dict[str, str]], required: List[str], guidance: str = ""
) -> List[Dict[str, str]]:
    """The chair gives every agent a distinct role so the debate covers the perspectives this question needs."""
    roster = "\n".join(f"- {m['handle']}: {m['description']}" for m in members)
    must = ", ".join(required)
    pack = f"\nThe user chose this kind of debate: {guidance}\n" if guidance else ""
    return [
        {
            "role": "system",
            "content": "You chair an AI council and assign debate roles. Reply with a single JSON object and nothing else.",
        },
        {
            "role": "user",
            "content": f"""Today is {today()}. The council will debate:
{question}
{pack}
Agents (with the model behind each, so you can match roles to strengths):
{roster}

Give every agent one distinct role, so that together they cover every perspective this question needs.
- These roles must be included: {must}. A Skeptic looks for the strongest reasons the emerging answer is wrong; a Pragmatist weighs cost, effort and what's realistic; a User advocate keeps the answer grounded in the person's situation.
- Give the other agents expert roles specific to this question (for example "Tax advisor", "Security engineer", "Pediatrician"), each a different angle.
- Role names: 1 to 3 words. Focus: what that agent should look at, under 15 words.

Reply like: {{"roles": [{{"agent": "{members[0]["handle"]}", "role": "...", "focus": "..."}}]}}""",
        },
    ]


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
    "decision or a question about what research shows, 4-6 for complex trade-offs, more only for hard multi-part "
    "problems"
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


def source_label(src: Dict[str, Any]) -> str:
    """How a source is marked for the researcher: its evidence level, and whether it's primary documentation."""
    level = {3: " [systematic review / meta-analysis]", 2: " [randomized trial]", 1: " [journal article]"}.get(
        src.get("evidence", 0), ""
    )
    return level + (" [primary source]" if src.get("primary") else "")


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

Write 1 to {max_queries} short web search queries (like you'd type into a search engine) that will find current, authoritative sources for this. Prefer queries that surface primary sources: official documentation, the maker's own pages, standards or filings, rather than comparison sites and blogs. When the question is about scientific or medical evidence ("what does the evidence say", health, diet, treatments), make one query find the most recent systematic review or meta-analysis (words like "meta-analysis" or "Cochrane review" help, and so does the current year) and another the largest recent randomized trials. Prefer one query unless the request clearly has several parts. Don't put years in queries unless the request is about a specific year or you need the newest research; then use the current year.""",
        },
    ]


def claims_messages(
    question: str, positions: List[Dict[str, str]], summary: Optional[str], max_claims: int
) -> List[Dict[str, str]]:
    """The material factual claims the final answer is about to rely on, each with a search query to verify it."""
    pos = "\n\n".join(f"{p['handle']}: {p['position']}\n{p['body'][:700]}" for p in positions)
    summ = f"Summary of the debate so far:\n{summary}\n\n" if summary else ""
    return [
        {
            "role": "system",
            "content": "You list the factual claims a debate's conclusion depends on, so each can be checked. Reply with a single JSON object and nothing else.",
        },
        {
            "role": "user",
            "content": f"""Today is {today()}. Question: {question}

{summ}Final positions:
{pos}

List up to {max_claims} factual claims the final answer will rely on. Start with the central claim: the debate's direct answer to the question, stated as a checkable fact. Then one claim for each requirement stated in the question (what the debate asserts about how the options meet it), then claims that could be wrong or overstated: product capabilities, integrations ("works without middleware"), costs, speed of implementation, versions, numbers, and any comparative claim ("cheaper", "faster", "better integrated") between options. State each claim exactly as the debate asserts it, without softening it. For each, write a web search query naming the specific product and feature and, when the claim is about one vendor's product, that vendor's documentation site (like "docs.oracle.com" or "help.salesforce.com").

Reply like: {{"claims": [{{"claim": "...", "query": "...", "docs_site": "..."}}]}}""",
        },
    ]


def verify_claim_messages(claim: str, sources: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Check one claim against the pages found for it; the quote must be copied from a source."""
    blocks = "\n\n".join(
        f"[{i + 1}] {src['title']} ({src['url']}){source_label(src)}\n{src['content']}" for i, src in enumerate(sources)
    )
    return [
        {
            "role": "system",
            "content": f"You are {RESEARCHER_NAME}, a careful fact-checker. You judge a claim only by what the sources literally say. Reply with a single JSON object and nothing else.",
        },
        {
            "role": "user",
            "content": f"""Claim: {claim}

Sources:
{blocks}

Decide what the sources establish about the claim:
- "supported": a source directly states it, in full.
- "partly": a source supports part of it, or supports it only with a condition or limit (give that as the caveat). A page that mentions a capability is only partial support for a broader claim about a whole workflow, cost, speed or superiority.
- "contradicted": a source directly says otherwise (give the correct fact as the caveat).
- "unknown": the sources don't settle it.
Prefer systematic reviews, meta-analyses and sources marked [primary source]. The quote must be copied word for word from one source (one or two sentences); use an empty quote for "unknown".

Reply like: {{"status": "partly", "source": 1, "quote": "...", "caveat": "..."}}""",
        },
    ]


def studies_messages(question: str, pages: List[Dict[str, Any]], max_studies: int = 5) -> List[Dict[str, str]]:
    """Pull the key studies out of the pages the research read, with only the details the pages state."""
    blocks = "\n\n".join(
        f"[{i + 1}] {p['title']} ({p['url']}){source_label(p)}\n{p['content']}" for i, p in enumerate(pages)
    )
    return [
        {
            "role": "system",
            "content": f"You are {RESEARCHER_NAME}, a careful research librarian. You report only what the pages literally say. Reply with a single JSON object and nothing else.",
        },
        {
            "role": "user",
            "content": f"""Question: {question}

Pages:
{blocks}

List up to {max_studies} studies described in these pages that best answer the question, strongest evidence first: systematic reviews and meta-analyses, then randomized trials, then other studies. For each:
- "name": a short name, like "BMJ network meta-analysis", "Cochrane review" or "NEJM trial (Liu et al.)"
- "year": publication year, only if the page states it
- "design": what kind of study and how big, e.g. "network meta-analysis of 99 randomized trials" or "12-month randomized trial"
- "participants": how many people, as a number, only if stated
- "finding": one plain sentence with its key result and numbers, naming the specific forms compared (for example time-restricted eating or alternate-day fasting)
- "quote": one or two sentences copied word for word from the page that back the finding
- "source": the page's number
Leave out (don't describe) any detail the page doesn't state, and skip a study whose result the page doesn't give.

Reply like: {{"studies": [{{"name": "...", "year": "...", "design": "...", "participants": "...", "finding": "...", "quote": "...", "source": 1}}]}}""",
        },
    ]


def _people(s: Dict[str, str]) -> str:
    p = s.get("participants") or ""
    return f"{p} participants" if re.fullmatch(r"[\d,.]+", p) else p


def studies_text(studies: List[Dict[str, str]]) -> str:
    """The checked studies as the chair and the audit see them."""
    lines = []
    for s in studies:
        head = s["name"] + (f" ({s['year']})" if s.get("year") else "")
        detail = "; ".join(x for x in (s.get("design"), _people(s)) if x)
        lines.append(f"- {head}{': ' + detail if detail else ''}. {s['finding']}")
    return "\n".join(lines)


def studies_markdown(studies: List[Dict[str, str]]) -> str:
    """The "Key studies" section added to the answer."""
    lines = ["## Key studies", ""]
    for i, s in enumerate(studies, 1):
        head = f"**{s['name']}**" + (f" ({s['year']})" if s.get("year") else "")
        detail = ", ".join(x for x in (s.get("design"), _people(s)) if x)
        lines.append(f"{i}. {head}{' · ' + detail if detail else ''}. {s['finding']} [Source]({s['url']})")
    return "\n".join(lines)


def ledger_text(claims: List[Dict[str, Any]]) -> str:
    """The claim ledger as the chair and the checker see it."""
    label = {
        "supported": "SUPPORTED",
        "partly": "PARTLY SUPPORTED",
        "contradicted": "CONTRADICTED",
        "unknown": "UNVERIFIED",
    }
    lines = []
    for i, c in enumerate(claims, 1):
        line = f"{i}. [{label.get(c['status'], 'UNVERIFIED')}] {c['claim']}"
        # Notes about the checking itself (search outages, unmatched quotes) are for the UI, not the answer
        if c.get("caveat") and c["status"] != "unknown":
            line += f" | Caveat: {c['caveat']}"
        if c.get("quote"):
            line += f' | Source says: "{c["quote"]}" ({c.get("source_title") or c.get("source_url")})'
        lines.append(line)
    return "\n".join(lines)


EVIDENCE_RULES = """Rules for factual claims (they override the debate):
- Never state a CONTRADICTED claim; state the correct fact from the ledger instead.
- A PARTLY SUPPORTED claim must carry its caveat.
- An UNVERIFIED claim (the check couldn't confirm it) may be stated only when the research findings above back it,
  citing them; otherwise present it as uncertain, and never as a deciding reason.
- Don't present a comparative advantage (cheaper, faster to implement, better integrated, no middleware) as established unless a SUPPORTED claim says exactly that.
- If the evidence can't establish a winner, say so: name the finalists and what would decide between them. Agreement among agents is not evidence.
- Only use names, numbers and citations that appear in the ledger or the research findings; never invent study
  details. Cite checked claims with the ledger's numbers, like [2]; for other research findings, name the study or
  review (with its year and key numbers) instead of using the briefs' citation numbers.
- When the research found systematic reviews, meta-analyses or large randomized trials, the answer leads with them,
  named with their year and key numbers, even if the checked claims are narrower.
- Write for the user in plain words ("Oracle's documentation confirms…", "not confirmed by the sources"). Never mention
  the ledger, these rules, statuses in capitals or item numbers, and don't explain how claims were checked."""


def answer_check_messages(
    answer: str, claims: List[Dict[str, Any]], research: str = "", handles: Sequence[str] = ()
) -> List[Dict[str, str]]:
    found = f"\nResearch findings (these count as sourced):\n{research}\n" if research else ""
    agents = f" The council members' names ({', '.join(handles)}) are fine to mention." if handles else ""
    return [
        {
            "role": "system",
            "content": "You audit an AI council's final answer against its evidence ledger. Reply with a single JSON object and nothing else.",
        },
        {
            "role": "user",
            "content": f"""Evidence ledger:
{ledger_text(claims)}
{found}
{EVIDENCE_RULES}

Answer to audit:
{answer}

List every place where the answer breaks a rule: it states a contradicted claim; drops a caveat; rests a decision on an unverified claim the research findings don't back; presents a comparative advantage neither the ledger nor the research supports; gives names, numbers or citations found in neither; or favors one option more strongly than the bottom line and the evidence do (for example calling it "safer" or "more reliable" without support). A fact the research findings state is sourced, not a problem. Advice, recommendations and judgment calls (what to choose, how to decide, what to try first) are not factual claims; don't flag them.{agents} Quote the answer's words exactly. If nothing breaks a rule, return an empty list.

Reply like: {{"problems": [{{"text": "...", "issue": "..."}}]}}""",
        },
    ]


def answer_revise_messages(
    answer: str, problems: List[Dict[str, str]], claims: List[Dict[str, Any]]
) -> List[Dict[str, str]]:
    issues = "\n".join(f'- "{p["text"]}": {p["issue"]}' for p in problems)
    return [
        {
            "role": "system",
            "content": "You are the chair of an AI council. You correct your final answer so it says only what the evidence supports.",
        },
        {
            "role": "user",
            "content": f"""Evidence ledger:
{ledger_text(claims)}

{EVIDENCE_RULES}

Your answer:
{answer}

An audit found these problems:
{issues}

Fix each flagged passage with the smallest change that makes it true to the evidence (correct it, add the caveat, or say plainly that it's uncertain) and keep everything else word for word, including every section and heading, named studies and numbers. Write the fixes for the reader: never say "unverified", "the provided evidence", "research findings" or anything about the audit. If the fixes mean no option is clearly best, say so in the bottom line. Output only the corrected answer.""",
        },
    ]


def research_brief_messages(
    request: str, requested_by: Optional[str], sources: List[Dict[str, str]], kind: str
) -> List[Dict[str, str]]:
    blocks = "\n\n".join(
        f"[{i + 1}] {src['title']} ({src['url']}){source_label(src)}\n{src['description']}\n{src['content']}"
        for i, src in enumerate(sources)
    )
    task = """Write a brief (at most 170 words) that answers the request using only the sources. Cite every fact with [n], and for the facts that matter most, quote the exact words from the source in "double quotes". Lead with the direct answer, and when the request is about versions or releases, state the newest one explicitly. Mention dates when recency matters.
When sources include systematic reviews, meta-analyses or randomized trials, lead with the strongest and most recent, and give their key numbers (how many trials or participants, how long, the effect size) and year; say when a finding comes from a single small or short study, or only from a narrative review.
Report exactly what each source establishes and no more: a page that mentions a capability is not proof of a whole workflow, lower cost, faster implementation or superiority over another product. Prefer sources marked [primary source]; say when a fact comes only from a comparison site or blog. If the sources don't answer it, or contradict each other, say so plainly instead of guessing."""
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
