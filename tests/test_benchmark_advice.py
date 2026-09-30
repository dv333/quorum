import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location("benchmark_advice", "scripts/benchmark_advice.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)

QUORUM = (
    "BOTTOM LINE: **Pay the card.**\n\n## Key points\n- **Math:** 24% beats savings.\n\n"
    "## Where they differed\nOtter wanted a $3k buffer; Panda $2k.\n\n## Details\nPanda's plan works."
)


def test_blind_text_hides_what_gives_quorum_away():
    text = bench.blind_text(QUORUM)
    assert "Where they differed" not in text and "Otter" not in text and "Panda" not in text
    assert "## Details" in text and "an adviser's plan works" in text


def test_the_model_alone_gets_the_same_instructions_and_format_as_the_chairs_first_answer():
    content = bench.single_messages("How should I train for a half marathon in 16 weeks?")[1]["content"]
    assert "BOTTOM LINE:" in content and "## Plan" in content and "Where they differed" not in content
    assert "mainstream expert guidance" in content


def test_a_pair_is_a_win_only_when_both_orders_agree(monkeypatch):
    replies = iter([{"winner": "A", "reason": "r1"}, {"winner": "B", "reason": "r2"},  # Quorum both times
                    {"winner": "A", "reason": "r3"}, {"winner": "A", "reason": "r4"}])  # fmt: skip
    monkeypatch.setattr(bench, "judge_pair", lambda q, a, b, m: next(replies))
    results = {
        "one": {"single": {"answer": "S"}, "quorum": {"answer": QUORUM}},
        "two": {"single": {"answer": "S"}, "quorum": {"answer": QUORUM}},
        "none": {"single": {"answer": "S"}, "quorum": {"answer": ""}},
    }
    qs = [{"id": i, "question": "Q"} for i in ("one", "two", "none")]
    bench.judge(results, qs, "judge")
    assert results["one"]["judge"]["verdict"] == "quorum"
    assert results["two"]["judge"]["verdict"] == "tie"  # the orders disagreed
    assert results["none"]["judge"]["verdict"] == "single"


def test_the_advice_set_has_twenty_distinct_questions():
    qs = json.loads(Path("benchmarks/advice/questions.json").read_text())["questions"]
    assert len(qs) == 20 and len({q["id"] for q in qs}) == 20


def test_the_report_warns_when_the_judge_picks_by_position():
    judged = [({"id": i}, {"judge": {"verdict": "tie", "votes": ["quorum", "single"]}}) for i in range(3)]
    assert "judged by position" in bench.position_note(judged) or "by position" in bench.position_note(judged)
    fair = [({"id": 1}, {"judge": {"verdict": "quorum", "votes": ["quorum", "quorum"]}})]
    assert bench.position_note(fair) == ""
