"""Regression: the CPQ debate presented "Oracle's integration eliminates the need for middleware" as a verified reason
to choose Oracle, although Oracle's own documentation says the integration uses Oracle Integration Cloud middleware.
The claim ledger has to catch it, and the answer has to be corrected before the user sees it."""

import json

import pytest

from backend import db, firecrawl, prompts
from backend.engine import quote_in_source
from tests.test_engine import FakeClient, make_debate, reply

MIDDLEWARE = "Oracle CPQ's integration eliminates the need for middleware"
COST = "Oracle CPQ is cheaper to implement than Salesforce CPQ"
ORACLE_DOC = (
    "Integrate Oracle CPQ with Subscription Management. The integration between Oracle CPQ and Oracle Subscription "
    "Management uses Oracle Integration Cloud as middleware to synchronize quotes, assets and subscriptions."
)


@pytest.fixture(autouse=True)
def memory_db():
    db.connect(":memory:")
    yield


async def search(query, limit):
    if "middleware" in query or "integration" in query:
        return [
            {
                "url": "https://www.cpq-blog.example/oracle-vs-salesforce",
                "title": "Oracle vs Salesforce CPQ",
                "description": "",
                "content": "Oracle CPQ integrates natively with Oracle ERP, so no middleware is needed.",
            },
            {
                "url": "https://docs.oracle.com/en/cloud/saas/cpq/integrate-subscription-management.html",
                "title": "Integrate Oracle CPQ with Subscription Management",
                "description": "",
                "content": ORACLE_DOC,
            },
        ][:limit]
    return [
        {
            "url": "https://www.cpq-blog.example/costs",
            "title": "CPQ costs",
            "description": "",
            "content": "Implementation costs vary widely by scope and partner.",
        }
    ][:limit]


def cpq_client():
    client = FakeClient(lambda h, r, m: reply("AGREE", text=f"{MIDDLEWARE}, so choose Oracle."))
    client.claims_reply = json.dumps(
        {
            "claims": [
                {"claim": MIDDLEWARE, "query": "oracle cpq subscription management integration middleware"},
                {"claim": COST, "query": "oracle cpq implementation cost"},
            ]
        }
    )

    def verify(messages):
        prompt = messages[1]["content"]
        if MIDDLEWARE in prompt:
            return json.dumps(
                {
                    "status": "contradicted",
                    "source": 1,
                    "quote": "uses Oracle Integration Cloud as middleware to synchronize quotes",
                    "caveat": "The integration uses Oracle Integration Cloud middleware.",
                }
            )
        # A made-up quote that isn't in any source must not count as support
        return json.dumps(
            {"status": "supported", "source": 1, "quote": "Oracle is 40% cheaper to implement", "caveat": ""}
        )

    client.verify_reply = verify
    client.verdict_reply = f"BOTTOM LINE: Choose Oracle CPQ; {MIDDLEWARE}.\n\n## Key points\n- Lower cost."
    client.audit_reply = json.dumps(
        {
            "problems": [
                {"text": "eliminates the need for middleware", "issue": "Contradicted by claim 1"},
                {"text": "Lower cost.", "issue": "Unverified comparative claim"},
            ]
        }
    )
    client.revise_reply = (
        "BOTTOM LINE: Two finalists remain: Oracle CPQ fits an Oracle-centric stack, but its Subscription Management "
        "integration uses Oracle Integration Cloud middleware, and cost and deployment speed are unverified.\n\n"
        "## Key points\n- Cost and implementation speed need matched quotes before choosing."
    )
    return client


async def run_cpq():
    client = cpq_client()
    eng = make_debate(client, research=True, search=search)
    await eng.post_user_message("Which CPQ is better for a Fortune 500: Oracle or Salesforce?")
    await eng.task
    return client, eng


async def test_the_middleware_claim_is_contradicted_by_primary_documentation():
    _, eng = await run_cpq()
    claims = {c["claim"]: c for c in eng.snapshot()["claims"]}
    middleware = claims[MIDDLEWARE]
    assert middleware["status"] == "contradicted"
    assert middleware["source_url"].startswith("https://docs.oracle.com/")  # primary docs outrank the blog
    assert quote_in_source(middleware["quote"], ORACLE_DOC)
    assert "Oracle Integration Cloud" in middleware["caveat"]
    # a verdict backed by a quote that isn't in the source is not evidence
    assert claims[COST]["status"] == "unknown" and claims[COST]["quote"] == ""


async def test_the_answer_is_bound_by_the_ledger_then_audited_and_corrected():
    client, eng = await run_cpq()
    verdict = next(m for _, m, _ in client.calls if "You turn the council's debate" in m[0]["content"])[1]["content"]
    assert f"[CONTRADICTED] {MIDDLEWARE}" in verdict
    assert "Caveat: The integration uses Oracle Integration Cloud middleware." in verdict
    assert f"[UNVERIFIED] {COST}" in verdict
    assert "Agreement among agents is not evidence" in verdict

    answer = db.query_one("SELECT * FROM messages WHERE author_kind = 'chair'")
    assert "eliminates the need for middleware" not in answer["content"]
    assert "Oracle Integration Cloud middleware" in answer["content"] and "Two finalists remain" in answer["content"]
    evidence = json.loads(answer["meta_json"])["evidence"]
    assert evidence["revised"] is True and len(evidence["problems"]) == 2

    fact_check = db.query_one("SELECT * FROM messages WHERE research_kind = 'factcheck'")
    assert "**Contradicted**: " + MIDDLEWARE in fact_check["content"]
    assert "**Unverified**: " + COST in fact_check["content"]


async def test_a_clean_answer_is_marked_checked_and_left_alone():
    client = cpq_client()
    client.audit_reply = '{"problems": []}'
    client.verdict_reply = "BOTTOM LINE: Two finalists remain."
    eng = make_debate(client, research=True, search=search)
    await eng.post_user_message("Which CPQ?")
    await eng.task
    answer = db.query_one("SELECT * FROM messages WHERE author_kind = 'chair'")
    assert answer["content"] == "BOTTOM LINE: Two finalists remain."
    assert json.loads(answer["meta_json"])["evidence"] == {"checked": True, "problems": []}


def test_quotes_must_really_be_in_the_source():
    assert quote_in_source("uses Oracle Integration Cloud as middleware", ORACLE_DOC)
    assert quote_in_source("USES oracle integration cloud, as middleware!", ORACLE_DOC)  # case and punctuation
    assert not quote_in_source("does not need any middleware at all", ORACLE_DOC)
    assert not quote_in_source("Oracle", ORACLE_DOC)  # too short to prove anything


def test_primary_sources_are_recognized():
    assert firecrawl.is_primary("https://docs.oracle.com/en/cloud/saas/cpq/index.html")
    assert firecrawl.is_primary("https://help.salesforce.com/s/articleView?id=sf.cpq.htm")
    assert firecrawl.is_primary("https://www.irs.gov/forms")
    assert firecrawl.is_primary("https://www.oracle.com/documentation/cpq/")
    assert not firecrawl.is_primary("https://www.cpqconsultant.com/blog/oracle-vs-salesforce")


def test_summaries_carry_open_questions_and_skeptics_need_evidence():
    summary = prompts.summary_messages("Q", None, "transcript")[1]["content"]
    assert 'under "Open:"' in summary and "never drop these just because most agents agree" in summary
    skeptic = prompts.agent_system_prompt("Otter", ["Panda"], [], "", role={"role": "Skeptic", "focus": ""})
    assert "Only AGREE once your strongest objection has been answered with evidence" in skeptic
    expert = prompts.agent_system_prompt("Otter", ["Panda"], [], "", role={"role": "Tax advisor", "focus": ""})
    assert "Only AGREE once" not in expert


async def test_export_lists_the_evidence_under_the_answer():
    from backend import export

    _, eng = await run_cpq()
    md = export.to_markdown(eng.snapshot())
    assert "**Evidence checked**" in md
    assert f"- **Contradicted**: {MIDDLEWARE} The integration uses Oracle Integration Cloud middleware." in md
    assert "(https://docs.oracle.com/en/cloud/saas/cpq/integrate-subscription-management.html)" in md
    assert f"- **Unverified**: {COST}" in md


def test_the_replay_checks_catch_the_original_failure():
    import importlib.util

    loader = importlib.util.spec_from_file_location("replay", "scripts/replay.py")
    replay = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(replay)
    with open("tests/regressions/cpq-oracle-middleware.json", encoding="utf-8") as f:
        spec = json.load(f)
    original = (
        "BOTTOM LINE: Choose Oracle: account hierarchies and subscription billing are built in, and its integration "
        "eliminates the need for middleware."
    )
    cheaper = [{"claim": "Oracle is cheaper to run", "status": "supported"}]
    failed = [what for ok, what in replay.check(spec, original, cheaper) if not ok]
    assert any("Oracle Integration Cloud" in f for f in failed)
    assert any("eliminates the need for middleware" in f for f in failed)
    assert any("cheaper" in f for f in failed)
    good = (
        "BOTTOM LINE: Two finalists remain. Oracle documents account hierarchies and subscription management; its "
        "CPQ and Subscription Management integration uses Oracle Integration Cloud middleware. Cost and deployment "
        "speed are unverified without matched quotes."
    )
    assert all(ok for ok, _ in replay.check(spec, good, [{"claim": "Oracle is cheaper", "status": "unknown"}]))
