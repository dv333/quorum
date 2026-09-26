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


async def search(query, limit, focus=""):
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
    assert f"1. **Contradicted**: {MIDDLEWARE} The integration uses Oracle Integration Cloud middleware." in md
    assert "(https://docs.oracle.com/en/cloud/saas/cpq/integrate-subscription-management.html)" in md
    assert f"2. **Unverified**: {COST}" in md


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


def test_replay_phrases_match_whole_words():
    import importlib.util

    loader = importlib.util.spec_from_file_location("replay", "scripts/replay.py")
    replay = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(replay)
    assert not replay.says("the right choice depends on your ERP", "OIC")
    assert replay.says("it runs through OIC (Oracle Integration Cloud)", "OIC")
    assert replay.says("large account hierarchies", "account hierarch")


def test_vendor_help_sites_with_hyphens_are_primary():
    assert firecrawl.is_primary("https://help-cxsales.oraclecloud.com/cpq/Content/Integration_Guides/SubscriptionM")
    assert not firecrawl.is_primary("https://helpful-cpq-tips.example.com/oracle")


async def test_claims_are_also_searched_on_the_vendors_docs_site():
    queries = []

    async def search_spy(query, limit, focus=""):
        queries.append(query)
        if query.startswith("site:docs.oracle.com"):
            return [
                {
                    "url": "https://docs.oracle.com/cpq/subscriptions.html",
                    "title": "Oracle docs",
                    "description": "",
                    "content": ORACLE_DOC,
                }
            ]
        return [
            {
                "url": "https://www.cpq-blog.example/x",
                "title": "Blog",
                "description": "",
                "content": "Oracle CPQ integrates natively, so no middleware is needed.",
            }
        ]

    client = cpq_client()
    client.claims_reply = json.dumps(
        {
            "claims": [
                {
                    "claim": MIDDLEWARE,
                    "query": "oracle cpq subscription integration",
                    "docs_site": "https://docs.oracle.com/en/",
                }
            ]
        }
    )
    eng = make_debate(client, research=True, search=search_spy)
    await eng.post_user_message("Which CPQ?")
    await eng.task
    assert "site:docs.oracle.com oracle cpq subscription integration" in queries
    claim = eng.snapshot()["claims"][0]
    assert claim["status"] == "contradicted" and claim["source_url"] == "https://docs.oracle.com/cpq/subscriptions.html"


async def test_a_quote_that_isnt_in_the_source_is_explained():
    _, eng = await run_cpq()
    cost = next(c for c in eng.snapshot()["claims"] if c["claim"] == COST)
    assert cost["status"] == "unknown" and "quote isn't in the source" in cost["caveat"]


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        return self.payload


@pytest.fixture
def firecrawl_reply(monkeypatch):
    import httpx

    def install(payload):
        async def post(self, url, json=None, headers=None):
            return FakeResponse(payload)

        monkeypatch.setattr(httpx.AsyncClient, "post", post)
        monkeypatch.setattr(firecrawl, "_SEARCH_GAP", 0)

    return install


async def test_a_blocked_search_engine_is_an_error_not_an_empty_result(firecrawl_reply):
    firecrawl_reply({"success": True, "data": {}})  # what Firecrawl sends when DuckDuckGo refuses the query
    with pytest.raises(firecrawl.SearchError, match="blocking automated searches"):
        await firecrawl.search("oracle cpq", 3)
    firecrawl_reply({"success": True, "data": {"web": []}})  # a real "nothing found"
    assert await firecrawl.search("oracle cpq", 3) == []


async def test_when_search_is_down_the_ledger_still_binds_the_answer():
    async def blocked(query, limit, focus=""):
        raise firecrawl.SearchError("the search engine returned nothing at all")

    client = cpq_client()
    eng = make_debate(client, research=True, search=blocked)
    await eng.post_user_message("Which CPQ?")
    await eng.task
    claims = eng.snapshot()["claims"]
    assert [c["status"] for c in claims] == ["unknown", "unknown"]
    assert all("Web search failed" in c["caveat"] for c in claims)
    fact_check = db.query_one("SELECT * FROM messages WHERE research_kind = 'factcheck'")
    assert fact_check["status"] == "done" and fact_check["content"].startswith("Web search failed")
    verdict = next(m for _, m, _ in client.calls if "You turn the council's debate" in m[0]["content"])[1]["content"]
    assert f"[UNVERIFIED] {MIDDLEWARE}" in verdict


def test_the_answer_is_written_for_the_user_not_about_the_ledger():
    rules = " ".join(prompts.EVIDENCE_RULES.split())
    assert "Never mention the ledger" in rules and "item numbers" in rules
    assert "don't explain how claims were checked" in rules and "never invent study details" in rules


def test_excerpts_keep_the_passages_about_the_claim():
    page = "\n\n".join(
        [f"Section {i}: Oracle CPQ pricing, discounts and quoting features for sales teams." for i in range(40)]
        + ["Subscription Management integration uses Oracle Integration Cloud as middleware for synchronization."]
    )
    by_query = firecrawl.relevant_excerpt(page, "oracle cpq pricing quoting", limit=600)
    by_claim = firecrawl.relevant_excerpt(
        page, "oracle cpq pricing quoting integration eliminates middleware", limit=600
    )
    assert "Integration Cloud" not in by_query and "Integration Cloud" in by_claim


async def test_claim_searches_pass_the_claim_as_focus():
    seen = []

    async def spy(query, limit, focus=""):
        seen.append(focus)
        return await search(query, limit)

    eng = make_debate(cpq_client(), research=True, search=spy)
    await eng.post_user_message("Which CPQ?")
    await eng.task
    assert MIDDLEWARE in seen and COST in seen


def test_leftover_ledger_jargon_becomes_plain_words():
    from backend.parsing import plain_answer

    text = (
        "Adherence is **[UNVERIFIED]** (Evidence 3). Protein matters (PARTLY SUPPORTED). "
        "Nothing in the ledger suggests harm, and the provided sources agree [SUPPORTED]."
    )
    assert plain_answer(text) == (
        "Adherence is (not confirmed by the sources). Protein matters (only partly confirmed by the sources). "
        "Nothing in the sources suggests harm, and the sources agree."
    )


def test_sources_are_ranked_by_strength_of_evidence():
    from backend.engine import _interleave

    ranked = _interleave(
        [
            [
                {"url": "https://blog.example/if", "title": "My fasting journey", "content": ""},
                {
                    "url": "https://www.nejm.org/x",
                    "title": "Calorie restriction with or without time-restricted eating: a randomized trial",
                    "content": "",
                },
                {
                    "url": "https://www.bmj.com/y",
                    "title": "Intermittent fasting strategies: systematic review and network meta-analysis",
                    "content": "",
                },
            ]
        ],
        3,
    )
    assert [s["evidence"] for s in ranked] == [3, 2, 0]
    assert prompts.source_label(ranked[0]) == " [systematic review / meta-analysis]"


async def test_the_chair_sees_the_opening_brief_even_after_long_debates():
    from tests.test_engine import FakeSearch

    client = FakeClient(lambda h, r, m: reply("REFINE", text="long turn " * 200))
    eng = make_debate(client, research=True, max_rounds=3, search=FakeSearch())
    await eng.post_user_message("Does intermittent fasting beat calorie restriction?")
    await eng.task
    verdict = next(m for _, m, _ in client.calls if "You turn the council's debate" in m[0]["content"])[1]["content"]
    assert "What Beagle's research found:" in verdict and "Opening brief" in verdict and "BRIEF: fact [1]" in verdict


def test_claims_start_with_the_central_claim_and_evidence_questions_seek_reviews():
    claims = prompts.claims_messages("Q", [], None, 6)[1]["content"]
    assert "Start with the central claim" in claims
    plan = prompts.research_plan_messages("Q", "Q", 3)[1]["content"]
    assert "systematic review or meta-analysis" in plan and "randomized trials" in plan


def test_quotes_tidied_by_the_model_still_count():
    source = (
        "Across 99 trials, intermittent fasting diets result in similar weight loss and cardiometabolic risk factor "
        "changes to traditional calorie-restricted diets. Alternate day fasting showed a small benefit in short trials."
    )
    tidied = "intermittent fasting diets result in similar weight loss… to traditional calorie restricted diets"
    assert quote_in_source(tidied, source)
    assert not quote_in_source(
        "intermittent fasting diets produce much greater weight loss than calorie restriction", source
    )
    assert quote_in_source("similar weight loss and cardiometabolic", source)
    assert not quote_in_source("similar weight loss and cardiac risk", source)  # short quotes must match exactly


def test_the_audit_counts_research_as_sourced():
    audit = prompts.answer_check_messages(
        "BOTTOM LINE: x", [{"claim": "c", "status": "unknown"}], "Opening brief\nNEJM trial: no difference [1]"
    )[1]["content"]
    assert "Research findings (these count as sourced)" in audit and "NEJM trial" in audit
    assert "names, numbers or citations found in neither" in audit


def test_internal_check_notes_stay_out_of_the_chairs_ledger():
    text = prompts.ledger_text(
        [
            {
                "claim": "A",
                "status": "unknown",
                "caveat": "Judged partly, but the quote isn't in the source, so it stays unverified.",
            },
            {"claim": "B", "status": "partly", "caveat": "Only in the US."},
        ]
    )
    assert "quote isn't in the source" not in text and "Caveat: Only in the US." in text


async def test_a_revision_that_drops_sections_is_rejected():
    client = cpq_client()
    client.verdict_reply = (
        "BOTTOM LINE: Choose Oracle.\n\n## Key points\n- Integrated.\n\n## Details\nMore detail here."
    )
    client.revise_reply = "BOTTOM LINE: Two finalists remain."  # lost its sections
    eng = make_debate(client, research=True, search=search)
    await eng.post_user_message("Which CPQ?")
    await eng.task
    answer = db.query_one("SELECT * FROM messages WHERE author_kind = 'chair'")
    assert "## Details" in answer["content"]
    assert json.loads(answer["meta_json"])["evidence"].get("revised") is None


def test_evidence_questions_also_search_for_the_newest_reviews():
    from backend.engine import evidence_queries

    qs = evidence_queries(
        "Intermittent fasting vs. daily calorie restriction for weight loss: what does the evidence say?"
    )
    assert qs[0].startswith("intermittent fasting daily calorie restriction weight loss meta-analysis 20")
    assert qs[1] == "intermittent fasting daily calorie restriction weight loss Cochrane review"
    assert evidence_queries("Should I rent or buy a home in Cupertino?") == []


async def test_the_opening_brief_runs_the_review_searches():
    from tests.test_engine import FakeSearch

    fake = FakeSearch()
    eng = make_debate(FakeClient(lambda h, r, m: reply("AGREE")), research=True, search=fake)
    await eng.post_user_message("Does creatine improve memory? What does the research say?")
    await eng.task
    assert any("meta-analysis" in q for q in fake.queries) and any("Cochrane review" in q for q in fake.queries)


def test_reviews_and_journals_are_recognized_by_site():
    assert firecrawl.evidence_level("Does fasting help?", "", "https://www.cochrane.org/evidence/CD015610") == 3
    assert firecrawl.evidence_level("Calorie restriction with or without TRE", "", "https://www.nejm.org/doi/x") == 1
    assert (
        firecrawl.evidence_level(
            "Fasting strategies",
            "systematic review and network meta-analysis of 99 trials",
            "https://www.bmj.com/content/389",
        )
        == 3
    )
    assert firecrawl.evidence_level("My fasting journey", "", "https://blog.example/fasting") == 0


def test_evidence_questions_get_at_least_two_rounds_and_cover_variants():
    assert "a question about what research shows" in prompts.ROUNDS_GUIDE
    verdict = prompts.verdict_messages(
        question="Q",
        prior_topics=[],
        summary=None,
        transcript="",
        positions=[],
        fact_check=None,
        reason="consensus",
        criteria=[],
        custom_rubric="",
    )[1]["content"]
    assert "say how the main forms compare" in verdict


def test_claim_check_reuses_matching_research_pages():
    from backend.engine import _related_pages

    bmj = {
        "url": "https://www.bmj.com/content/389/bmj-2024-082007",
        "title": "Intermittent fasting strategies and body weight: network meta-analysis",
        "content": "Intermittent fasting and continuous energy restriction had similar effects on body weight.",
        "evidence": 3,
    }
    blog = {
        "url": "https://blog.example.com/fasting",
        "title": "My fasting journey",
        "content": "Fasting changed my life and my morning routine.",
        "evidence": 0,
    }
    claim = "Intermittent fasting and continuous energy restriction produce similar body weight loss."
    assert _related_pages([blog, bmj], claim, []) == [bmj]
    assert _related_pages([bmj], claim, [bmj]) == []  # already among the claim's own results


def test_audit_wording_becomes_plain_words():
    from backend.parsing import plain_answer

    text = "This is **unverified** by the provided evidence, and the research findings don't compare it."
    assert plain_answer(text) == "This is not established, and the studies don't compare it."


def test_key_studies_keep_only_what_the_page_states():
    from backend.engine import check_studies
    from backend.prompts import studies_markdown

    page = {
        "url": "https://www.cochranelibrary.com/cdsr/doi/10.1002/14651858.CD015610",
        "title": "Intermittent fasting for adults with overweight or obesity",
        "content": "We included 22 randomized trials with 1,995 participants. Intermittent fasting probably results in "
        "little to no difference in weight loss compared with standard dietary advice.",
        "evidence": 3,
    }
    good = {
        "name": "Cochrane review",
        "year": "2026",  # not on the page, so it's dropped
        "design": "systematic review of 22 randomized trials",
        "participants": "1995",
        "finding": "Fasting made little to no difference to weight loss versus standard advice.",
        "quote": "Intermittent fasting probably results in little to no difference in weight loss compared with standard "
        "dietary advice.",
        "source": 1,
    }
    invented = {**good, "finding": "Fasting lost 4.2 kg more.", "source": 1}
    fake_quote = {**good, "quote": "Fasting is clearly better than any other diet for everyone.", "source": 1}
    studies = check_studies([invented, fake_quote, good], [page])
    assert [s["finding"] for s in studies] == [good["finding"]]
    assert studies[0]["year"] == "" and studies[0]["participants"] == "1995"
    md = studies_markdown(studies)
    assert md.startswith("## Key studies") and "22 randomized trials" in md and page["url"] in md


def test_key_studies_drop_notes_about_missing_details():
    from backend.engine import check_studies
    from backend.prompts import studies_markdown

    page = {
        "url": "https://www.bmj.com/content/389/bmj-2024-082007",
        "title": "Intermittent fasting strategies: network meta-analysis",
        "content": "Alternate day fasting showed a small reduction in weight compared with continuous energy restriction.",
        "evidence": 3,
    }
    quote = "Alternate day fasting showed a small reduction in weight compared with continuous energy restriction."
    vague = {"name": "Review", "finding": "The provided text excerpts don't give a result.", "quote": quote, "source": 1}
    study = {
        "name": "BMJ network meta-analysis",
        "participants": "Adults with obesity (specific count not stated)",
        "finding": "Alternate-day fasting lost a little more weight than continuous restriction.",
        "quote": quote,
        "source": 1,
    }
    kept = check_studies([vague, study], [page])
    assert [s["name"] for s in kept] == ["BMJ network meta-analysis"] and kept[0]["participants"] == ""
    assert "participants" not in studies_markdown(kept)
