from backend.parsing import (
    ThinkSplitter,
    clean_fact_check,
    parse_json_loose,
    parse_research_requests,
    parse_stance,
    strip_thinking,
)


def test_parse_stance_basic():
    st = parse_stance("I think Go is better.\n\nSTANCE: AGREE\nPOSITION: Use Go.")
    assert st.parsed and st.stance == "AGREE" and st.position == "Use Go."
    assert st.body == "I think Go is better."


def test_parse_stance_markdown_decorations():
    st = parse_stance("Text\n---\n**STANCE:** refine\n**POSITION:** Hybrid with PyO3")
    assert st.parsed and st.stance == "REFINE"
    assert st.position == "Hybrid with PyO3"
    assert st.body == "Text"


def test_parse_stance_uses_last_footer():
    st = parse_stance("Earlier someone said STANCE: AGREE\nmore\nSTANCE: DISAGREE\nPOSITION: No")
    assert st.stance == "DISAGREE"


def test_parse_stance_missing_falls_back():
    st = parse_stance("Just an answer without footer")
    assert not st.parsed and st.stance == "REFINE" and st.body == "Just an answer without footer"


def test_strip_thinking():
    assert strip_thinking("<think>secret</think>Answer") == "Answer"
    assert strip_thinking("Answer<think>unterminated") == "Answer"


def test_think_splitter_across_chunks():
    sp = ThinkSplitter()
    out_c, out_t = [], []
    for piece in ["Hel", "lo <th", "ink>sec", "ret</thi", "nk> world"]:
        c, t = sp.feed(piece)
        out_c.append(c)
        out_t.append(t)
    c, t = sp.flush()
    out_c.append(c)
    out_t.append(t)
    assert "".join(out_c) == "Hello  world"
    assert "".join(out_t) == "secret"


def test_think_splitter_partial_tag_that_isnt():
    sp = ThinkSplitter()
    c1, _ = sp.feed("a <b")
    c2, _ = sp.feed("old> c")
    c3, _ = sp.flush()
    assert c1 + c2 + c3 == "a <bold> c"


def test_research_request_line_forms():
    text = "I think X.\n@Researcher: what is the latest Postgres major version?\nSTANCE: REFINE\nPOSITION: x"
    assert parse_research_requests(text) == ["what is the latest Postgres major version?"]
    assert parse_research_requests("**@Researcher** — current price of an M5 Pro Mac mini") == [
        "current price of an M5 Pro Mac mini"
    ]
    assert parse_research_requests("As I said, @Researcher: is SQLite 3.50 out yet?") == ["is SQLite 3.50 out yet?"]


def test_research_request_ignores_inline_mentions_and_code():
    assert parse_research_requests("As @Researcher showed, Go is fine.") == []
    assert parse_research_requests("```\n@Researcher: not a request here\n```") == []
    assert parse_research_requests("@Researcher: hi") == []  # too short to be a real question


def test_research_request_limit():
    text = "@Researcher: first question here\n@Researcher: second question here"
    assert parse_research_requests(text, limit=1) == ["first question here"]
    assert len(parse_research_requests(text, limit=2)) == 2


def test_clean_fact_check_keeps_final_bullets_only():
    leaked = """- **Supported**: A [1]
- **Contradicted**: B is wrong [2]

Wait, source 2 says otherwise. Revised lines:
- **Supported**: A [1]
- **Unclear**: B [2]

This fits the "at most 5 lines" constraint."""
    assert clean_fact_check(leaked) == "- **Supported**: A [1]\n- **Unclear**: B [2]"
    assert clean_fact_check("No claims could be checked.") == "No claims could be checked."


def test_beagle_mentions():
    assert parse_research_requests("@Beagle: latest Postgres version please") == ["latest Postgres version please"]
    assert parse_research_requests("Hmm.\n@beagle what is the newest Go?") == ["what is the newest Go?"]


def test_parse_json_loose_strict_and_broken():
    assert parse_json_loose('Sure: {"action": "clear"} done') == {"action": "clear"}
    broken = '{"action":"summarize","brief":"Rent or buy in Austin" "assumptions":["Stays 7+ years","Has $80k saved"]}'
    got = parse_json_loose(broken)
    assert got["action"] == "summarize" and got["brief"] == "Rent or buy in Austin"
    assert got["assumptions"] == ["Stays 7+ years", "Has $80k saved"]
    assert parse_json_loose("no json here") == {}
